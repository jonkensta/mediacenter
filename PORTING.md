# Porting guide: `787bc30` → `caf8d3b`

**Audience:** a Claude instance running on the media server itself, working with
the operator (Jonathan). Read this end-to-end before running anything.

This describes a one-time migration. Once it is done and verified, this file can
be deleted.

---

## 0. What you are working on

An Arch Linux box running a Docker-based media server. Storage is a USB-attached
DAS (8 drives) that is **powered on and off by a USB relay**, mounted to
`/mnt/00`–`/mnt/07`, and unified with mergerfs at `/mnt/merged`.

The dependency chain, top to bottom:

```
mount-das.service    relay powers on DAS -> waits for 8 UUIDs -> mounts /mnt/00..07
      |
mergerfs.service     unions the 8 branches -> /mnt/merged
      |
mediaserver.service  docker compose up in /opt/mediaserver
```

`CLAUDE.md` in this repo has the fuller architecture description. Read it too.

**The repo is not the deployment.** The repo holds *placeholders*; the live
config lives at `/opt/mediaserver/` and `/opt/pihole/` and contains real
secrets. Copying repo files over `/opt` blindly will destroy credentials. See
§4, which is the most dangerous step in this guide.

---

## 1. What changed and why it matters operationally

A code review found real bugs. The fixes change runtime behaviour in ways that
will surprise you if you don't know about them:

| Change | Operational consequence |
|---|---|
| `mount-das` mounts no longer prefixed `-` | A failed mount now **fails the unit** and triggers a power-cycle retry. Previously a 7-of-8 pool looked healthy. |
| `mount-das` unmounts gated | If an unmount fails on stop, the relay is **not** opened — the DAS keeps power instead of being cut out from under a live filesystem. |
| `TimeoutStartSec` set explicitly | Was the 90s default, shorter than the 180s device wait. Cold boots with slow spin-up used to fail. |
| `mediaserver.service` gains `BindsTo=mergerfs.service` | **If mergerfs stops or fails, the stack stops and stays down.** Previously containers kept running against an empty `/mnt/merged`, writing into the root filesystem. This is the intended safer failure mode, but it means a mergerfs blip now takes the stack offline until someone starts it. |
| New `docker.service` drop-in | dockerd auto-starts `restart: unless-stopped` containers at boot independently of `mediaserver.service`. Without the drop-in they bind-mount `/mnt/merged` before it is mounted. |
| `mergerfs.service`: no `KillMode=none`, non-lazy unmount, mountpoint wait | Cleaner shutdown; dependants no longer start before the mount is live. |
| Pi-hole volume path fixed | `./pihole/config` → `./config`. **Existing data must be moved** or Pi-hole comes up blank. |
| Pi-hole v6 env rename | `WEBPASSWORD` → `FTLCONF_webserver_api_password`. |
| cloudflare-ddns image swapped | `oznu` (archived) → `favonia`. **Different env schema; needs a new scoped token or it will not work.** |
| Network split | New compose-managed `frontend` network for swag + endlessh. |
| Scripts rewritten | `update-deluge-port` no longer has hardcoded credentials; it reads them from a key file and from inside the container. |

### Not done, still outstanding

- **Leaked credentials have not been rotated.** The old Gluetun API key and
  Deluge `localclient` password were committed to a public repo and were visible
  in the tip of `main` from 2026-03-22 to 2026-07-25. They are still in git
  history. **Rotation is a prerequisite of this migration** (§3), not a
  follow-up.
- Images are still on `:latest` with a pull on every boot. Deliberate choice.

---

## 2. Pre-flight — inventory before touching anything

Do not skip. You need to know the current state to migrate safely and to roll
back. Capture this somewhere you can read later:

```bash
# Where are we starting from?
cd /path/to/repo && git log --oneline -1

# Current service state
systemctl status mount-das.service mergerfs.service mediaserver.service --no-pager

# Is the pool actually mounted, and are all 8 branches there?
mountpoint /mnt/merged && df -h /mnt/0[0-7] /mnt/merged

# What is running
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'

# Back up the live config - THIS IS THE ONE THAT MATTERS
sudo cp -a /opt/mediaserver/docker-compose.yml /opt/mediaserver/docker-compose.yml.bak
sudo cp -a /opt/pihole/docker-compose.yml /opt/pihole/docker-compose.yml.bak
sudo cp -a /etc/systemd/system/{mount-das,mergerfs,mediaserver}.service /root/ 2>/dev/null

# Where does pihole data currently live?
ls -la /opt/pihole/ /opt/pihole/config /opt/pihole/pihole/config 2>/dev/null
```

Confirm with the operator before proceeding:

- Is there anything mid-download in Deluge that a stack restart would disrupt?
- Is anyone streaming from Jellyfin right now?
- Has a maintenance window been agreed? **The stack will be down for the whole
  of §5.**

---

## 3. Prerequisites (do these first, they gate later steps)

### 3a. Rotate the leaked credentials

```bash
# Generate a new Gluetun API key (any long random string)
openssl rand -hex 24
```

Then:
1. Put the new key in the **live** `/opt/mediaserver/docker-compose.yml` under
   `HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE`.
2. Write the same key to the new key file:
   ```bash
   sudo install -Dm600 /dev/stdin /etc/mediaserver/gluetun-api-key <<< 'NEW_KEY_HERE'
   ```
3. Change the Deluge `localclient` password in
   `/opt/mediaserver/deluge/auth` (format `user:password:level`). Deluge must be
   restarted to pick it up — that happens in §5 anyway.

### 3b. Cloudflare token

`favonia/cloudflare-ddns` uses a **scoped API token**, not a Global API key.
Mint one at Cloudflare with `Zone.DNS:Edit` + `Zone.Zone:Read` on `jstarr.me`.
Hold onto it for §4.

### 3c. Docker network

```bash
docker network ls | grep mediaserver || docker network create mediaserver
```

The `frontend` network is compose-managed — do **not** create it by hand.

---

## 4. Compose files — the dangerous step

**Do not `cp` the repo compose files over `/opt`.** The repo versions contain
placeholders (`YOUR_VPN_USERNAME`, `YOUR_PIHOLE_PASSWORD`, …). Overwriting
blindly wipes every real secret on the box.

Instead, **diff and port the changes across by hand**:

```bash
diff -u /opt/mediaserver/docker-compose.yml docker-compose.mediaserver.yml
diff -u /opt/pihole/docker-compose.yml docker-compose.pihole.yml
```

The changes you need to carry into the live files:

**`/opt/mediaserver/docker-compose.yml`**
- `endlessh`: `networks:` → `- frontend` (was `- mediaserver`)
- `swag`: `networks:` → both `- frontend` and `- mediaserver`
- Bottom `networks:` block: add the compose-managed `frontend` network
- `cloudflare-ddns`: replace the whole service block with the `favonia` version
  from the repo file, and fill in the real token from §3b

**`/opt/pihole/docker-compose.yml`**
- `WEBPASSWORD=…` → `FTLCONF_webserver_api_password=…` (keep the real password)
- Delete the `VIRTUAL_HOST` line (dead nginx-proxy variable, nothing reads it)
- Volume `./pihole/config:/etc/pihole` → `./config:/etc/pihole`

Then move the Pi-hole data to match the corrected path — **only if it is
currently in the nested location**:

```bash
ls -la /opt/pihole/pihole/config   # if this exists and /opt/pihole/config does not:
sudo mv /opt/pihole/pihole/config /opt/pihole/config
sudo rmdir /opt/pihole/pihole 2>/dev/null
```

Validate before going further — this catches YAML mistakes cheaply:

```bash
docker compose -f /opt/mediaserver/docker-compose.yml config >/dev/null && echo ok
docker compose -f /opt/pihole/docker-compose.yml config >/dev/null && echo ok
```

Ask the operator to eyeball the diff of the live files before you apply them.

---

## 5. The migration itself

Order matters. Stop top-down, install, start bottom-up. Do this in one sitting.

```bash
# 5a. Host scripts
sudo install -m755 bin/relay bin/await-block-devices \
                   bin/update-deluge-port bin/mergerfs-hardlink-downloads \
                   /usr/local/bin/

# 5b. Stop the stack, top-down
sudo systemctl stop mediaserver.service
sudo systemctl stop mergerfs.service
# Do NOT stop mount-das unless you intend to power down the DAS.

# 5c. Install units + the docker drop-in
sudo cp systemd/*.service /etc/systemd/system/
sudo install -Dm644 systemd/docker.service.d/after-mergerfs.conf \
  /etc/systemd/system/docker.service.d/after-mergerfs.conf
sudo systemctl daemon-reload

# 5d. Restart dockerd so it picks up the drop-in
sudo systemctl restart docker.service

# 5e. Start bottom-up
sudo systemctl start mergerfs.service
mountpoint /mnt/merged || echo "STOP - pool not mounted, do not continue"
sudo systemctl start mediaserver.service
```

If `mergerfs.service` fails to start, **stop and investigate** — do not start
`mediaserver.service`. `journalctl -u mergerfs -n 50`. The new `ExecStartPost`
waits up to 10s for `/mnt/merged` to become a real mountpoint and fails the unit
if it doesn't.

Note that `mount-das.service` is deliberately *not* restarted here — restarting
it would power-cycle the DAS. `daemon-reload` loads its new definition, but the
changed mount/unmount behaviour is only actually exercised at the next boot.
That is why the reboot test in §6b matters.

### 5f. Deluge container scripts

```bash
docker exec deluge test -x /config/venv/bin/python3 \
  || { docker exec deluge python3 -m venv /config/venv
       docker exec deluge /config/venv/bin/pip install deluge-client; }

for s in deluge-list-torrents deluge-remove-torrent deluge-update-tracker; do
  docker cp "bin/$s" "deluge:/usr/local/bin/$s"
done
```

---

## 6. Verification

Work through all of these. Report results to the operator rather than assuming.

```bash
# Units healthy and in the right order
systemctl status mount-das mergerfs mediaserver --no-pager

# Pool is real and complete - expect 8 branches with data
mountpoint /mnt/merged && ls /mnt/merged && df -h /mnt/0[0-7]

# All containers up; nothing restart-looping
docker ps --format 'table {{.Names}}\t{{.Status}}'

# Network split is as intended:
#   endlessh -> frontend only
#   swag     -> frontend AND mediaserver
# NOTE: `frontend` is compose-managed, so docker names it `mediaserver_frontend`
# (project name from the /opt/mediaserver directory). That prefix is expected -
# it is not a misconfiguration. `mediaserver` is external, so it keeps its name.
docker inspect endlessh -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
docker inspect swag     -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
# Expect: endlessh -> mediaserver_frontend
#         swag     -> mediaserver_frontend mediaserver

# SWAG can still reach its proxy targets. busybox nslookup is used because
# `getent` is not reliably present in Alpine-based LSIO images.
for s in radarr sonarr bazarr prowlarr jellyfin heimdall foundryvtt gluetun pihole; do
  docker exec swag nslookup "$s" >/dev/null 2>&1 && echo "  ok  $s" || echo "  FAIL $s"
done

# The rewritten port updater works end-to-end with the new key file
sudo /usr/local/bin/update-deluge-port

# Deluge tooling works and reads /config/auth
docker exec deluge deluge-list-torrents | head -3

# DDNS actually authenticated (new image, new token)
docker logs cloudflare-ddns --tail 30

# Pi-hole found its data - blocklist count should be non-zero, not a fresh install
docker logs pihole --tail 30
```

Then confirm in a browser that the SWAG-proxied services still load over HTTPS,
and that Jellyfin can play a file (proves the pool is readable through the
container bind mounts).

### 6b. The reboot test — this is the real one

The mount-error handling and the boot-race fix **only exercise on a cold boot**.
Everything above can pass while those remain unproven. With the operator's
agreement, schedule a reboot and afterwards check:

```bash
systemctl status mount-das mergerfs mediaserver --no-pager
journalctl -b -u mount-das -u mergerfs -u mediaserver --no-pager
mountpoint /mnt/merged
# Confirm no container started before the mount - look for containers whose
# start time precedes the mergerfs mount in the journal timeline.
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

---

## 7. Rollback

```bash
sudo systemctl stop mediaserver.service mergerfs.service
sudo cp /root/{mount-das,mergerfs,mediaserver}.service /etc/systemd/system/
sudo rm /etc/systemd/system/docker.service.d/after-mergerfs.conf
sudo cp /opt/mediaserver/docker-compose.yml.bak /opt/mediaserver/docker-compose.yml
sudo cp /opt/pihole/docker-compose.yml.bak /opt/pihole/docker-compose.yml
sudo systemctl daemon-reload && sudo systemctl restart docker.service
sudo systemctl start mergerfs.service mediaserver.service
```

Two things a rollback does **not** undo: the Pi-hole directory move (move it
back) and the rotated credentials (keep the new ones — the old ones are burned).

---

## 8. Explicitly out of scope

**Do not run `mergerfs-hardlink-downloads` as part of this migration.** It is a
separate, destructive maintenance task. When it is eventually run:

```bash
bin/mergerfs-hardlink-downloads                    # dry-run FIRST, always
sudo systemctl stop mediaserver.service
bin/mergerfs-hardlink-downloads --execute
sudo chown -R media:media /mnt/0{0..7}
sudo systemctl start mediaserver.service
```

It now verifies file contents byte-for-byte before linking; the previous version
matched on size alone and could destroy a media file. Expect it to report
*fewer* reclaimable bytes than the old version did — it no longer counts
already-hardlinked pairs as savings. Never pass `--no-verify`.

---

## 9. What was and wasn't verified upstream

Be honest with the operator about this. Before hand-off, on a **separate dev
machine, not this box**:

- **Verified:** all Python and Bash files parse; `systemd-analyze verify` is
  clean apart from missing-binary errors expected off-box; `docker compose
  config` succeeds for both files; systemd `$$` escaping in the new shell
  fragments confirmed empirically; the network topology was inspected and every
  SWAG proxy target confirmed reachable; `await-block-devices` timeout accuracy
  measured; `relay` argument handling exercised; the rewritten hardlink script
  passed 11/11 assertions against a simulated two-branch pool, including the
  case that used to cause data loss.
- **Not verified — no access to real hardware:** anything involving the actual
  DAS, the USB relay, a real mergerfs mount, a *busy* unmount actually failing
  the unit, and the cold-boot path. Treat §6b as genuinely unproven.

---

## 10. Notes for the assisting Claude

- **Inspect before you act.** This guide was written without access to this
  machine. Paths, existing state, and what is already installed may differ.
  Check, then adapt; don't assume.
- **The `/opt` compose files hold real secrets.** Never overwrite them with repo
  copies. Never print their contents into a shared transcript.
- **Ask rather than guess** on anything involving the operator's data: the
  Pi-hole directory move, whether a download is in flight, when to reboot.
- **Stop on the first failure** rather than pushing through. A half-migrated
  storage stack is worse than either end state — particularly between §5b and
  §5e, where the pool is unmounted and the containers are down.
- If `mount-das.service` fails after this change where it used to "succeed",
  that is very likely the fix working: a drive is genuinely not mounting. Check
  `journalctl -u mount-das` and the physical DAS before assuming a regression.
