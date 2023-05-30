# Media Center Configuration
This repo contains the rough steps that I used to create my media center using docker.

## Groups, Files, Folders
Create a user and group for each docker container:
```bash
sudo useradd media
sudo useradd pihole
```

Eight drives: Seven date and one parity:
```bash
cd /mnt
sudo mkdir -p 00 01 02 03 04 05 06 parity merged
sudo chown -R media:media 01 02 03 04 05 06 parity merged
```
Enabling the given `mergerfs.service` file will create the merged view after the drives have been mounted.
The given `snapraid.conf` will store the parity on the parity drive;
I am running this manually for now.

Set the `PUID` and `GUID` given by `id media` as the environment variables used within the `mediacenter.yml`.
There might be a less manual way to set the user, but I haven't gotten anything else to work.

Create the config folders for each process running within the `mediaserver` container:
```bash
sudo mkdir -p /opt/mediaserver/{radarr,sonarr,bazarr,deluge,heimdall,plex,tautulli,jellyfin}
sudo cp mediaserver.yml /opt/mediaserver
sudo chown -R media:media /opt/mediaserver
```

Ditto for pihole:
```bash
sudo mkdir -p /opt/pihole/{config,dnsmasq.d}
sudo cp pihole.yml /opt/pihole
sudo chown -R pihole:pihole /opt/pihole
```

## Service Files

```bash
sudo systemctl enable mergerfs.service docker-compose@pihole.service mediacenter.service
```

## Connecting Services

The docker services can talk with any host-mode service using the DNS name `host.docker.interal`.
For any non-hostmode service, they can talk to each other using the container name as a DNS name.

## Notes

  - `deluge` uses `mmap`, so it's important to keep `cache.files=partial` enabled using `mergerfs`.
  - You can run `docker system prune` to clean any unused networks, volumes, or containers.
  - I added `nofail` as an option to the data drives in `/etc/fstab` so the system would keep booting if they failed to mount.
  - I removed the use of hardlinks within `radarr` and `sonarr` as I don't know how well `mergerfs` works with these.
