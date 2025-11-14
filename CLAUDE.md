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
   - Category create policy: `mfs` (most free space)

3. **Docker Services** (`mediaserver.service`)
   - Depends on `mergerfs.service`
   - Runs from `/opt/mediaserver/docker-compose.yml`
   - All services use PUID/PGID=1012 (`media` user)

### Network Architecture

- **VPN Routing**: Deluge traffic routes through Gluetun VPN container using `network_mode: "service:gluetun"`
  - This affects reverse proxy setup: deluge's nginx config must set `upstream_app` to `gluetun` instead of `deluge`
- **Reverse Proxy**: SWAG handles SSL termination and reverse proxying for 8 services (bazarr, deluge, foundryvtt, heimdall, jellyfin, pihole, radarr, sonarr)
- **Service Communication**: Non-host-mode containers communicate via Docker DNS using container names

### Utility Scripts

Both located in `bin/` and deployed to `/usr/local/bin/`:

- `relay` - Python script using pyserial to control USB relay (commands: open, close, test)
- `await-block-devices` - Python script using pyudev to wait for block devices by UUID (async polling with timeout)

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
- **No hardlinks**: Disabled in radarr/sonarr due to mergerfs behavior
- **mergerfs cache setting**: Must keep `cache.files=partial` for deluge's mmap usage
- **Credentials**: This is a public repo - never commit real credentials, use placeholders only
