# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This repository contains configuration files and utilities for managing a Docker-based media server on Arch Linux. The setup uses systemd services to manage a DAS (Direct Attached Storage) system with automatic power cycling via USB relay, mergerfs for unified storage, and Docker Compose for running media services.

## Architecture

### Storage Layer (3-tier dependency chain)

1. **DAS Management** (`mount-das.service`)
   - Controls power to the DAS via USB relay at `/dev/ttyUSB0`
   - Uses `bin/relay` Python script to send serial commands to power relay
   - Uses `bin/await-block-devices` to wait for all 8 drives (by UUID) to appear
   - Mounts 8 data drives to `/mnt/00` through `/mnt/07`
   - Auto-retries on failure by power cycling the DAS (60s restart, 5 attempts in 30min)

2. **Filesystem Unification** (`mergerfs.service`)
   - Depends on `mount-das.service`
   - Merges `/mnt/00` through `/mnt/07` into `/mnt/merged`
   - Uses `cache.files=partial` (critical for deluge's mmap usage)
   - Category create policy: `epmfs` (existing path, most free space)
   - `ExecStartPost` waits for `/mnt/merged` to actually be a mountpoint before
     dependants start

3. **Docker Services** (`mediaserver.service`)
   - `BindsTo=mergerfs.service`, so the stack stops if the pool disappears
     rather than writing into an empty `/mnt/merged` on the root filesystem
   - Runs from `/opt/mediaserver/docker-compose.yml`
   - All services use PUID/PGID=1012 (`media` user)

4. **Docker daemon drop-in** (`systemd/docker.service.d/after-mergerfs.conf`)
   - Containers are `restart: unless-stopped`, so dockerd starts them itself at
     boot, independently of `mediaserver.service`. dockerd is ready in seconds
     while the DAS can take minutes, so without this drop-in containers can
     bind-mount `/mnt/merged` before it is mounted.

### Network Architecture

- **VPN Routing**: Deluge traffic routes through Gluetun VPN container using `network_mode: "service:gluetun"`
  - This affects reverse proxy setup: deluge's nginx config must set `upstream_app` to `gluetun` instead of `deluge`
- **Reverse Proxy**: SWAG handles SSL termination and reverse proxying for 9 services (bazarr, deluge, foundryvtt, heimdall, jellyfin, pihole, prowlarr, radarr, sonarr)
- **Two networks**: `frontend` (compose-managed) holds the internet-facing tier — swag and endlessh. `mediaserver` (external, shared with the pihole compose file) holds everything else. `swag` is the only container on both, bridging TLS termination to the backend. `endlessh` is frontend-only, so a compromise there cannot reach Deluge RPC, the \*arr APIs, or Gluetun's control server. This limits blast radius but is not auth: a proxy-conf without auth still exposes an admin UI.
- **Service Communication**: Non-host-mode containers communicate via Docker DNS using container names

### Utility Scripts

Located in `bin/` and deployed to `/usr/local/bin/`:

- `relay` - Python script using pyserial to control USB relay (commands: open, close, test)
- `await-block-devices` - Python script using pyudev to wait for block devices by UUID (monotonic deadline, one udev enumeration per poll)
- `update-deluge-port` - Reads the forwarded port from Gluetun and sets it as Deluge's listen port. Reads the Gluetun API key from `$GLUETUN_API_KEY` or `/etc/mediaserver/gluetun-api-key` (mode 0600) and pipes it in over stdin; the Deluge password is read inside the container from `/config/auth`. Neither ever appears on the host command line. Nothing schedules this - run it after a VPN reconnect (PIA changes the forwarded port each time).

#### Deluge management scripts (run inside the container via `docker exec`)

These scripts connect to the Deluge daemon over RPC. They use `/config/venv/bin/python3` (a venv inside the deluge container's config volume with `deluge-client` installed). All read daemon credentials automatically from `/config/auth`.

- `deluge-list-torrents` - Lists all torrents as TSV: `hash`, `name`, `size`, `label`, `tracker`, `tracker_status`
- `deluge-remove-torrent` - Removes torrents and their data; reads hashes from stdin (one per line)
- `deluge-update-tracker` - Forces a tracker re-announce; reads hashes from stdin (one per line)

```bash
# Copy scripts into the container after modifying
docker cp bin/deluge-list-torrents deluge:/usr/local/bin/deluge-list-torrents
docker cp bin/deluge-remove-torrent deluge:/usr/local/bin/deluge-remove-torrent
docker cp bin/deluge-update-tracker deluge:/usr/local/bin/deluge-update-tracker

# List all torrents
docker exec deluge deluge-list-torrents

# Filter on a COLUMN, never the whole line. `grep "not registered"` also matches
# a release *named* "Not.Registered.2024" - which then gets its data deleted.
# Columns: 1=hash 2=name 3=size 4=label 5=tracker 6=tracker_status

# Preview first - removal is irreversible
docker exec deluge deluge-list-torrents | awk -F'\t' '$6 ~ /not registered/' | cut -f1 \
  | docker exec -i deluge deluge-remove-torrent --dry-run

# Remove unregistered torrents (--yes required: stdin is the hash list, so
# there is no terminal left to prompt on)
docker exec deluge deluge-list-torrents | awk -F'\t' '$6 ~ /not registered/' | cut -f1 \
  | docker exec -i deluge deluge-remove-torrent --yes

# Re-announce unreachable torrents
docker exec deluge deluge-list-torrents | awk -F'\t' '$6 ~ /unreachable/' | cut -f1 \
  | docker exec -i deluge deluge-update-tracker

# Remove all non-HDBits torrents (match the tracker column)
docker exec deluge deluge-list-torrents | awk -F'\t' '$5 !~ /hdbits\.org/' | cut -f1 \
  | docker exec -i deluge deluge-remove-torrent --yes

# Interactive removal with fzf (tab to multi-select)
docker exec deluge deluge-list-torrents | fzf -m --with-nth=2.. | cut -f1 \
  | docker exec -i deluge deluge-remove-torrent --yes
```

`deluge-remove-torrent` validates every hash as 40 hex chars, resolves names,
prints what it will delete, and aborts the whole batch if any hash is unknown -
a hash that does not resolve usually means the filter was wrong.

#### mergerfs utility scripts

- `mergerfs-hardlink-downloads` - Finds files duplicated between `/mnt/merged/Downloads` and the media library (Movies/TV). Candidates are shortlisted by size and then **compared byte-for-byte** before anything is touched; size alone is not a match. Replaces media copies with hardlinks to the Downloads copy, moving files to the same branch if needed.

Safety behaviour:
- Refuses to run with `--execute` while `mediaserver.service` is active (`--force` overrides)
- Skips pairs that are already the same inode, and reports them separately so the dry-run's savings figure is honest
- Skips any path present on more than one branch (removing one copy would expose a stale duplicate)
- Verifies each new link with `os.path.samefile` before deleting the old copy
- Per-pair error handling: one failure is reported and the run continues, with a summary and non-zero exit
- Branches come from mergerfs' own `user.mergerfs.srcmounts`, falling back to `/mnt/0[0-7]`

```bash
# Dry-run (safe, default)
bin/mergerfs-hardlink-downloads

# Execute (stop mediaserver.service first, fix ownership after)
sudo systemctl stop mediaserver.service
bin/mergerfs-hardlink-downloads --execute
sudo chown -R media:media /mnt/0{0..7}
sudo systemctl start mediaserver.service

# Clean up leftover .hlnk_tmp files from an interrupted run
bin/mergerfs-hardlink-downloads --clean-tmp
```

`--no-verify` restores the old size-only matching. Do not use it: two different
files of identical size will silently overwrite each other.

## Key Configuration Points

### Docker Compose Structure

The `docker-compose.yml` contains placeholders that must be replaced before deployment:
- `WEBPASSWORD`: PiHole admin password
- `OPENVPN_USER` / `OPENVPN_PASSWORD`: VPN credentials
- `API_KEY`: Cloudflare API key
- Email addresses

### SWAG Reverse Proxy Setup

After first run, SWAG generates sample subdomain configs. Enable services by:
```bash
cd /opt/mediaserver/swag/nginx/proxy-confs
cp <service>.subdomain.conf.sample <service>.subdomain.conf
```

**Critical**: Deluge config requires manual edit to change `upstream_app` from `deluge` to `gluetun` due to network_mode sharing.

### FoundryVTT

Requires secrets file at `/opt/mediaserver/foundryvtt/secrets.json` (referenced via Docker secrets).

## Common Operations

### Managing Services

```bash
# View service status (check dependency chain)
sudo systemctl status mount-das.service mergerfs.service mediaserver.service

# Restart media services
sudo systemctl restart mediaserver.service

# View Docker logs
cd /opt/mediaserver && docker compose logs -f [service_name]
```

### Deploying Configuration Changes

```bash
# After modifying systemd service files
sudo cp systemd/*.service /etc/systemd/system/
sudo install -Dm644 systemd/docker.service.d/after-mergerfs.conf \
  /etc/systemd/system/docker.service.d/after-mergerfs.conf
sudo systemctl daemon-reload
sudo systemctl restart <service_name>

# After modifying docker-compose.yml
cd /opt/mediaserver
docker compose down
docker compose up -d
```

### Testing Utility Scripts

```bash
# Test relay communication
/usr/local/bin/relay /dev/ttyUSB0 test

# Test awaiting specific drive
/usr/local/bin/await-block-devices <uuid> --timeout 30
```

## Important Constraints

- **No parity**: Data can be re-downloaded, so no snapraid/parity drive is used
- **Hardlinks enabled**: Radarr/sonarr use hardlinks. mergerfs uses `epmfs` (existing path, most free space) create policy so new files land on the same branch as their parent directory, making hardlinks work correctly across the pool.
- **mergerfs cache setting**: Must keep `cache.files=partial` for deluge's mmap usage
- **Mount failures are fatal**: `mount-das.service` no longer prefixes its mounts with `-`. A failed mount fails the unit so `Restart=on-failure` power-cycles the DAS, instead of leaving a silently degraded pool that looks healthy. Unmounts on stop are likewise gated: if one fails, the relay is not opened, so the DAS keeps power rather than being cut out from under a live filesystem.
- **Start timeouts**: `TimeoutStartSec` is set explicitly on `mount-das.service` (300s) and `mediaserver.service` (1800s). The systemd default is 90s, which is shorter than the DAS spin-up budget and shorter than a large `docker compose pull`.
- **Credentials**: This is a public repo - never commit real credentials, use placeholders only. Runtime secrets live outside the repo: `/etc/mediaserver/gluetun-api-key` (mode 0600), `/opt/mediaserver/foundryvtt/secrets.json`, and Deluge's own `/config/auth`.
