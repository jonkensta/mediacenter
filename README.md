# Media Center Configuration

This repo contains the rough steps that I used to create my media center using docker.

## Groups, Files, Folders

Create a user and group for each docker container:

```bash
sudo useradd media
```

Eight data drives are mounted to `/mnt/00` through `/mnt/07` and combined using mergerfs:

```bash
cd /mnt
sudo mkdir -p 00 01 02 03 04 05 06 07 merged
sudo chown -R media:media 00 01 02 03 04 05 06 07 merged
```

Set the `PUID` and `PGID` given by `id media` as the environment variables used within the `docker-compose.yml`.
The compose files currently hardcode `1012` for both.
There might be a less manual way to set the user, but I haven't gotten anything else to work.

Create the config folders for each service:

```bash
# Create directories for services
sudo mkdir -p /opt/mediaserver/{radarr,sonarr,bazarr,prowlarr,deluge,heimdall,jellyfin,endlessh,gluetun,foundryvtt,cloudflare-ddns,swag}
sudo mkdir -p /opt/pihole/config

# Copy the compose files to their respective destinations, naming them docker-compose.yml
sudo cp docker-compose.mediaserver.yml /opt/mediaserver/docker-compose.yml
sudo cp docker-compose.pihole.yml /opt/pihole/docker-compose.yml

# Set permissions
sudo chown -R media:media /opt/mediaserver
sudo chown -R media:media /opt/pihole
```

## Utility Scripts

Install the host utility scripts to `/usr/local/bin`:

```bash
sudo install -m755 bin/relay /usr/local/bin/
sudo install -m755 bin/await-block-devices /usr/local/bin/
sudo install -m755 bin/update-deluge-port /usr/local/bin/
sudo install -m755 bin/mergerfs-hardlink-downloads /usr/local/bin/
```

Install required dependencies:

```bash
sudo pacman -S python-pyudev python-pyserial
```

`update-deluge-port` needs the Gluetun API key out-of-band (never in this repo):

```bash
sudo install -Dm600 /dev/stdin /etc/mediaserver/gluetun-api-key <<< 'your-gluetun-api-key'
```

The three `deluge-*` scripts run *inside* the deluge container and need
`deluge-client` in a venv at `/config/venv`:

```bash
docker exec deluge python3 -m venv /config/venv
docker exec deluge /config/venv/bin/pip install deluge-client
for s in deluge-list-torrents deluge-remove-torrent deluge-update-tracker; do
  docker cp "bin/$s" "deluge:/usr/local/bin/$s"
done
```

## Service Files

Install and enable the systemd service files in dependency order:

Create the external docker network first, or `mediaserver.service` will fail to start:

```bash
docker network create mediaserver
```

```bash
sudo cp systemd/mount-das.service /etc/systemd/system/
sudo cp systemd/mergerfs.service /etc/systemd/system/
sudo cp systemd/mediaserver.service /etc/systemd/system/

# Keeps dockerd from auto-starting `restart: unless-stopped` containers before
# the pool is mounted - see the note below.
sudo install -Dm644 systemd/docker.service.d/after-mergerfs.conf \
  /etc/systemd/system/docker.service.d/after-mergerfs.conf

sudo systemctl daemon-reload
sudo systemctl enable mount-das.service mergerfs.service mediaserver.service
```

The service dependency chain is:
1. `mount-das.service` - Powers on the DAS using a USB relay and mounts all drives
2. `mergerfs.service` - Creates a merged view of all drives at `/mnt/merged`
3. `mediaserver.service` - Starts the main Docker containers.

The docker drop-in matters because containers are declared `restart: unless-stopped`.
dockerd starts them by itself at boot, independently of `mediaserver.service`, and
it is ready in seconds while the DAS can take minutes. Without the drop-in the
containers bind-mount `/mnt/merged` while it is still an empty directory on the
root filesystem.

The `pihole` service is now managed separately. It can be enabled and started using a systemd template service. For instructions on creating the necessary template file, see the [Arch Linux Wiki](https://wiki.archlinux.org/title/Docker#Start_Docker_Compose_projects_on_boot).

```bash
sudo systemctl enable --now docker-compose@pihole
```

## Configuration

### FoundryVTT Secrets

Create the FoundryVTT secrets file before starting the services:

```bash
sudo mkdir -p /opt/mediaserver/foundryvtt
# Create the secrets.json file with your FoundryVTT credentials
sudo nano /opt/mediaserver/foundryvtt/secrets.json
```

### Credentials

Before deploying, update all placeholder credentials in `docker-compose.mediaserver.yml` and `docker-compose.pihole.yml`:
- PiHole admin password (`FTLCONF_webserver_api_password` — v6 renamed this from `WEBPASSWORD`)
- VPN credentials (`OPENVPN_USER`, `OPENVPN_PASSWORD`)
- Gluetun control-server API key (`HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE`) — the
  same key must go in `/etc/mediaserver/gluetun-api-key` for `update-deluge-port`
- Cloudflare API token (`CLOUDFLARE_API_TOKEN`) — a *scoped* token with
  Zone.DNS:Edit + Zone.Zone:Read on the zone, not a Global API key
- Email addresses

These are placeholders in a public repo. Keep it that way: the deployed copy
under `/opt` is what holds real values.

### SWAG Reverse Proxy

SWAG (Secure Web Application Gateway) provides reverse proxy and SSL termination for the services. Configuration requires several steps:

1. **Update domain in site configuration**:
   ```bash
   # Edit the default site configuration
   sudo nano /opt/mediaserver/swag/nginx/site-confs/default.conf
   # Update all domain references to match your domain
   ```

2. **Enable proxy configurations for each service**:
   ```bash
   cd /opt/mediaserver/swag/nginx/proxy-confs
   # Copy sample configs for each service you want to proxy
   sudo cp bazarr.subdomain.conf.sample bazarr.subdomain.conf
   sudo cp deluge.subdomain.conf.sample deluge.subdomain.conf
   sudo cp foundryvtt.subdomain.conf.sample foundryvtt.subdomain.conf
   sudo cp heimdall.subdomain.conf.sample heimdall.subdomain.conf
   sudo cp jellyfin.subdomain.conf.sample jellyfin.subdomain.conf
   sudo cp pihole.subdomain.conf.sample pihole.subdomain.conf
   sudo cp prowlarr.subdomain.conf.sample prowlarr.subdomain.conf
   sudo cp radarr.subdomain.conf.sample radarr.subdomain.conf
   sudo cp sonarr.subdomain.conf.sample sonarr.subdomain.conf
   ```

3. **Special configuration for Deluge**:
   Since Deluge uses Gluetun's network interface, edit `deluge.subdomain.conf` and change the `upstream_app` from `deluge` to `gluetun`:
   ```nginx
   set $upstream_app gluetun;
   ```

See the [SWAG documentation](https://docs.linuxserver.io/general/swag/) for more details.

## Connecting Services

There are two networks:

- `mediaserver` — the backend. All the application containers, plus `pihole`
  from the other compose file. Created out-of-band (see the service-files step)
  because two compose projects share it.
- `frontend` — the internet-facing tier: `swag` and `endlessh`. Managed by
  compose itself, no manual step.

`swag` is the only container on both, so it terminates TLS on `frontend` and
proxies through to the backend. `endlessh` is frontend-only and therefore cannot
reach Deluge's RPC port, the \*arr APIs, or Gluetun's control server.

For any non-hostmode service, they can talk to each other using the container name as a DNS name.

This limits blast radius but is not authentication: any proxy-conf you enable
without auth still publishes that admin UI to the internet.

## Notes

- `deluge` uses `mmap`, so it's important to keep `cache.files=partial` enabled using `mergerfs`.
- You can run `docker system prune` to clean any unused networks, volumes, or containers.
- Most images are on `:latest` and `mediaserver.service` runs `docker compose pull`
  on every start, so a reboot can deploy a new major version unattended. This is
  a deliberate choice; if something breaks after a reboot, suspect an image bump
  first and check `docker compose logs`.
- The data drives are mounted by `mount-das.service`, **not** `/etc/fstab`. Don't
  add fstab entries for `/mnt/00`–`/mnt/07`; two owners of the same mountpoints
  is how you end up with a half-mounted pool that still looks healthy.
- Hardlinks **are** enabled in `radarr`/`sonarr`. `mergerfs` uses the `epmfs`
  create policy so new files land on the same branch as their parent directory,
  which is what makes hardlinks work across the pool. `bin/mergerfs-hardlink-downloads`
  retroactively de-duplicates copies made before this was set up.
- The `mount-das.service` will automatically retry mounting the DAS by power cycling it via the USB relay if it fails to mount: a failed start runs `ExecStopPost`, which opens the relay (power off), and the `Restart=on-failure` attempt 60s later closes it again (power on).
- PIA hands out a new forwarded port on every VPN reconnect. Nothing schedules
  `update-deluge-port`, so run it after a reconnect or Deluge keeps announcing a
  dead port.
