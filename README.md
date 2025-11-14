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

Set the `PUID` and `GUID` given by `id media` as the environment variables used within the `docker-compose.yml`.
There might be a less manual way to set the user, but I haven't gotten anything else to work.

Create the config folders for each service:

```bash
sudo mkdir -p /opt/mediaserver/{radarr,sonarr,bazarr,deluge,heimdall,jellyfin,pihole,endlessh,gluetun,foundryvtt,cloudflare-ddns,swag}
sudo cp docker-compose.yml /opt/mediaserver
sudo chown -R media:media /opt/mediaserver
```

## Utility Scripts

Install the utility scripts to `/usr/local/bin`:

```bash
sudo cp bin/relay /usr/local/bin/
sudo cp bin/await-block-devices /usr/local/bin/
sudo chmod +x /usr/local/bin/relay /usr/local/bin/await-block-devices
```

Install required dependencies:

```bash
sudo pacman -S python-pyudev python-pyserial
```

## Service Files

Install and enable the systemd service files in dependency order:

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl enable mount-das.service mergerfs.service mediaserver.service
```

The service dependency chain is:
1. `mount-das.service` - Powers on the DAS using a USB relay and mounts all drives
2. `mergerfs.service` - Creates a merged view of all drives at `/mnt/merged`
3. `mediaserver.service` - Starts all Docker containers

## Configuration

### FoundryVTT Secrets

Create the FoundryVTT secrets file before starting the services:

```bash
sudo mkdir -p /opt/mediaserver/foundryvtt
# Create the secrets.json file with your FoundryVTT credentials
sudo nano /opt/mediaserver/foundryvtt/secrets.json
```

### Credentials

Before deploying, update all placeholder credentials in `docker-compose.yml`:
- PiHole admin password (`WEBPASSWORD`)
- VPN credentials (`OPENVPN_USER`, `OPENVPN_PASSWORD`)
- Cloudflare API key (`API_KEY`)
- Email addresses

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

For any non-hostmode service, they can talk to each other using the container name as a DNS name.

## Notes

- `deluge` uses `mmap`, so it's important to keep `cache.files=partial` enabled using `mergerfs`.
- You can run `docker system prune` to clean any unused networks, volumes, or containers.
- I added `nofail` as an option to the data drives in `/etc/fstab` so the system would keep booting if they failed to mount.
- I removed the use of hardlinks within `radarr` and `sonarr` as I don't know how well `mergerfs` works with these.
- The `mount-das.service` will automatically retry mounting the DAS by power cycling it via the USB relay if it fails to mount.
