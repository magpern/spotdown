# Runbook: install and upgrade spotdown from its Docker images

spotdown ships as two Docker images from one tag
([ADR 0005](../adr/0005-two-images-backend-api-and-ui.md)):

- `ghcr.io/magpern/spotdown`, the **backend** (api and worker): `/v2` behind a caller key, and
  `/healthz`;
- `ghcr.io/magpern/spotdown-ui`, the **UI**: the page, which calls the backend server-side with its
  own key.

`.github/workflows/release.yml` builds both for `linux/amd64` and `linux/arm64` from a version tag
on `main`. A server needs the images, the release's `compose.yaml`, its own key files and,
optionally, a `spotdown.env` and a `spotdown-ui.env`. It never needs a clone. Deploying stays the
owner's decision (AGENTS.md, safety envelope).

## What a release publishes

| Item | Value |
|---|---|
| Images | `ghcr.io/magpern/spotdown` and `ghcr.io/magpern/spotdown-ui`, **private** packages. Never change their visibility. |
| Tags | `X.Y.Z`, `X.Y` (stable releases only, moves to the newest patch) and `sha-<short commit>`, the same for both images. There is **no `latest`**. |
| Attestations | SLSA provenance (`mode=max`) and an SBOM in the registry; a GitHub artifact attestation of each image digest and of every release asset. |
| Release assets | `compose.yaml` (from `deploy/compose.yaml`), `compose.no-ui.yaml`, `compose.spotify.yaml`, `spotdown.env.example`, `spotdown-ui.env.example` (each from `deploy/`) and `SHA256SUMS`. |
| Release notes | Generated from the merged PRs, plus both attested image digests. A tag with a hyphen (`v1.2.3-rc.1`) is a pre-release. |

A tag that is not `vMAJOR.MINOR.PATCH[-prerelease]`, or whose commit is not on `main`, publishes
nothing. Agents never push a tag on their own; the Architect cuts it after a merge
(`git tag -a vX.Y.Z -m vX.Y.Z origin/main && git push origin vX.Y.Z`), and a pushed tag is never
moved or deleted: fix forward with a new version.

## What the images are

- Python 3.11 on Debian slim, base pinned by version and digest. Both run as the unprivileged user
  **uid 10001, gid 10001**; the code is root-owned and read-only.
- **The backend** has no mode: only `/v2` behind a caller key, and `/healthz`
  ([contract](../contract-v2.md)); every other path is 404. Without a readable
  `SERVICE_KEY_FILE` the api does not start; the worker needs no key. It holds ffmpeg and
  libsndfile, and only `requirements.txt` and `app/` enter its build (no UI files). It writes only
  to `/data` (the database `service.db`, uploads, work files and mp3s) and `/tmp` (numba and
  yt-dlp caches). Healthcheck: `python -m app.healthcheck api` asks `/healthz` on
  `127.0.0.1:8000`; the deploy file gives the worker `python -m app.healthcheck worker` (a Redis
  ping).
- **The UI** holds `spotdown_ui/` only: no `app/`, yt-dlp, librosa or ffmpeg. It calls nothing but
  `SPOTDOWN_BACKEND_URL`, and its key never reaches a browser. It writes only its generated access
  token to `/data`. Healthcheck: `python -m spotdown_ui.healthcheck` (never calls the backend).
- Neither holds a `.env`, audio, data or a secret, and neither build takes arguments or secrets.

## Install

On the server, as the deploying user:

```bash
mkdir -p /opt/spotdown && cd /opt/spotdown
V=1.2.3                                   # the release to install
gh release download "v$V" -R magpern/spotdown -p compose.yaml -p compose.no-ui.yaml \
  -p compose.spotify.yaml -p spotdown.env.example -p spotdown-ui.env.example -p SHA256SUMS
sha256sum -c SHA256SUMS
gh attestation verify compose.yaml -R magpern/spotdown

# The private images: log in once with a token that has read:packages only.
docker login ghcr.io -u <github user>      # paste the token at the prompt, never on the command line

# The callers' network. classbeat and the ui join it and call http://spotdown-api:8000.
docker network create --internal spotdown

# The keys: at least 32 characters, one line, readable only by the images' user. Two different
# keys: classbeat's service key, and the UI's own backend key.
mkdir -p secrets
umask 077
for name in spotdown_service_key spotdown_ui_backend_key; do
  openssl rand -hex 32 > "secrets/$name.new"
  sudo install -m 0400 -o 10001 -g 10001 "secrets/$name.new" "secrets/$name"
  rm "secrets/$name.new"
done
# Give the service key to the caller (classbeat) through its own secret file; never paste a key
# into chat, a URL, an environment variable or a log. The UI's key stays on this host.

cp spotdown.env.example spotdown.env          # optional: uncomment only what you change
cp spotdown-ui.env.example spotdown-ui.env    # optional, likewise
SPOTDOWN_VERSION=$V docker compose up -d --wait

# The UI's access token, generated once in its data volume: open the UI's /login and paste it.
SPOTDOWN_VERSION=$V docker compose exec ui cat /data/ui_token
```

Check the pulled images are the attested ones:

```bash
for image in spotdown spotdown-ui; do
  docker image inspect --format '{{index .RepoDigests 0}}' "ghcr.io/magpern/$image:$V"
  gh attestation verify "oci://ghcr.io/magpern/$image:$V" -R magpern/spotdown
done
```

Each digest must be the one in the release notes.

### Without the UI

Add the overlay `compose.no-ui.yaml` to every `docker compose` command. The ui is not started and
the api neither mounts nor reads the UI's key, so only `secrets/spotdown_service_key` is needed
(skip `spotdown_ui_backend_key` in the loop above). It needs Docker Compose 2.24.4 or later.

```bash
SPOTDOWN_VERSION=$V docker compose -f compose.yaml -f compose.no-ui.yaml up -d --wait
```

To turn the UI off on a running stack, run that command, then remove the old container with
`docker compose -f compose.yaml rm -sf ui`.

### With Spotify credentials (optional)

Without them `POST /v2/spotify/resolve` reads Spotify's public pages. With them it uses the Web API
(full playlists and the ISRC). Set `SPOTIFY_CLIENT_ID` in `spotdown.env`, install the client
secret as a file, and add the overlay `compose.spotify.yaml` (after `compose.no-ui.yaml` if you
use both). The secret is mounted into the api only, never an env value. One of the two without the
other stops the api's start-up.

```bash
sudo install -m 0400 -o 10001 -g 10001 /path/to/client_secret secrets/spotdown_spotify_client_secret
SPOTDOWN_VERSION=$V docker compose -f compose.yaml -f compose.spotify.yaml up -d --wait
```

## Settings

The deploy file fixes these, and `spotdown.env` cannot change them:

- api: `SERVICE_KEY_FILE=/run/secrets/spotdown_service_key`,
  `UI_SERVICE_KEY_FILE=/run/secrets/spotdown_ui_backend_key`, `DATA_DIR=/data` and
  `REDIS_URL=redis://redis:6379/0` (`compose.spotify.yaml` adds
  `SPOTIFY_CLIENT_SECRET_FILE=/run/secrets/spotdown_spotify_client_secret`);
- worker: `DATA_DIR` and `REDIS_URL`, with no key;
- ui: `SPOTDOWN_BACKEND_URL=http://spotdown-api:8000`,
  `SPOTDOWN_BACKEND_KEY_FILE=/run/secrets/spotdown_ui_backend_key` and `UI_DATA_DIR=/data`.

Everything else is optional, one `NAME=value` per line. `spotdown.env` (the api and the worker)
and `spotdown-ui.env` (the ui) list each setting with its default:

- `URL_ALLOWED_HOSTS`: the hosts the `url` source may fetch from. Empty turns that source off.
- `ALLOW_YTDLP_SOURCING`: YouTube. On by default (owner, 2026-11-24); `false` turns every YouTube
  path off (README, "The backend"). Turning it on accepts a terms-of-service risk and is the
  operator's decision. See the upgrade note below: an unset flag used to mean off.
- `SPOTIFY_CLIENT_ID`: only with `compose.spotify.yaml` (above).
- `MAX_UPLOAD_BYTES`, `UPLOAD_TTL_S`, `AUDIO_TTL_S`, `URL_FETCH_TIMEOUT_S`, `ANALYSIS_MAX_*`,
  `GLOBAL_*_TTL_S`, `SCOPED_RETENTION_TTL_S`: limits and retention.
- In `spotdown-ui.env`: `UI_ALLOWED_HOSTS` and `UI_PUBLIC_ORIGIN` for a reverse proxy (below), and
  the UI's upload and relay limits.

`SERVICE_MODE`, `SERVE_UI` and `SPOTDOWN_UI` are deprecated and ignored: the api logs one warning
that names any that are set. Remove them (see "Upgrading from a SERVICE_MODE compose file").

Compose variables, read from the shell or from a `.env` file next to `compose.yaml`:

| Variable | Meaning |
|---|---|
| `SPOTDOWN_VERSION` | **Required.** `X.Y.Z` (pinned, recommended) or `X.Y` (follows patch releases on each pull). The same for both images. |
| `SPOTDOWN_SERVICE_KEY_FILE` | classbeat's key file on the host. Default `./secrets/spotdown_service_key`. |
| `SPOTDOWN_UI_KEY_FILE` | The UI's key file on the host. Default `./secrets/spotdown_ui_backend_key`. |
| `SPOTDOWN_SPOTIFY_SECRET_FILE` | With `compose.spotify.yaml`: the client secret file. Default `./secrets/spotdown_spotify_client_secret`. |
| `SPOTDOWN_SERVICE_NETWORK` | The callers' external network. Default `spotdown`. |
| `SPOTDOWN_PUBLISH_HOST` | The address the UI's port 8000 is published on. Default `127.0.0.1`. |

Never put a key or secret in `spotdown.env`, `spotdown-ui.env` or any environment variable. Each is
a Docker secret file mounted only where it is used:

| Secret file | Mounted into |
|---|---|
| `spotdown_service_key` | the api |
| `spotdown_ui_backend_key` | the api and the ui |
| `spotdown_spotify_client_secret` (optional) | the api |

The worker and Redis never see a key.

## Networks, ports and volumes

- **The backend publishes no port.** The api listens on port 8000 inside its networks only. A
  caller reaches it as `http://spotdown-api:8000` on the `spotdown` network, as the ui does. Redis
  is on the `backend` network (internal), which only the api and the worker share.
- **The UI publishes port 8000** on `SPOTDOWN_PUBLISH_HOST`, the loopback by default, for the
  owner's reverse proxy. The app never reads that setting. Its `ui-published` network exists only
  for that port (an internal network cannot publish one), and it gives the UI a route out: see
  "The UI's outbound access" to close it.
- `egress` gives the api and the worker a way out: Spotify's pages or Web API, the `url` source
  and, when on, YouTube.
- `spotdown-data` (mounted at `/data` in the api and the worker) holds the service database, the
  temporary audio of running jobs and the kept mp3s. Back it up while the stack is stopped. A
  takedown does not reach an earlier backup (contract, "Retention of global analyses").
- `spotdown-ui-data` holds the UI's generated access token only.
- `redis-data` holds the job queue. Losing it loses the jobs still waiting in the queue. Redis runs
  as the image's own `redis` user (`999:1000`) with every capability dropped and a read-only root;
  a new volume takes that ownership from the image. A volume created earlier by a Redis started as
  root is already owned by `redis` (that entrypoint chowns it), so it needs no change.

### The UI's bind address

Choosing the bind address and exposing the UI beyond the loopback is the owner's deploy step.

- **Reverse proxy on the same host:** keep the default. The proxy forwards to `127.0.0.1:8000`.
- **Reverse proxy on another host (the LAN bind):** publish on this host's own LAN address, set in
  the deploy environment (the shell or the `.env` next to `compose.yaml`), never in the
  repository. `<LAN address>` is a placeholder for that address, for example `192.0.2.10`:

  ```bash
  SPOTDOWN_PUBLISH_HOST=<LAN address> SPOTDOWN_VERSION=$V docker compose up -d --wait
  ```

  Never `0.0.0.0`. Restrict the port to the proxy's address in the host firewall (an example
  is under "The UI's outbound access", "The LAN bind").

Behind a proxy, set the public name in `spotdown-ui.env`, for example
`UI_ALLOWED_HOSTS=spotdown.example.org` and `UI_PUBLIC_ORIGIN=https://spotdown.example.org`. The UI
ignores forwarded headers.

Operator commands run in the api container, for example a takedown:

```bash
SPOTDOWN_VERSION=$V docker compose exec api python -m app.service.takedown youtube:<video id>
```

### The UI's outbound access

The UI needs to reach exactly one destination: **the backend's internal origin,
`http://spotdown-api:8000`** (the fixed `SPOTDOWN_BACKEND_URL`), on the `spotdown` network. It needs
nothing on the internet, on the LAN or on the host. Its code calls nothing else (ADR 0005
Decision 7), but its container is not locked in: the `ui-published` network, which exists only for
its published port, is a plain bridge, and so gives it a route out.

Limiting that route is **the operator's choice**, made in the host firewall. Below is one way, for a
host where Docker manages iptables (the default). It drops every connection the UI starts through
`ui-published`; replies on connections made *to* the UI still pass, so the published port keeps
working.

**Precondition: every other network the UI is on is internal.** The rules below close only the
`ui-published` bridge. The UI is also on the callers' network, `SPOTDOWN_SERVICE_NETWORK` (default
`spotdown`), and that network gives no route out only because it was created `--internal`
(`docker network create --internal spotdown` above). If that setting names a network that is not
internal, for example a caller's own bridge, the UI has a second route out that these rules do not
see. Check it (if you set `SPOTDOWN_SERVICE_NETWORK` in the `.env` rather than the shell, put that
name in place of the variable):

```bash
docker network inspect -f '{{.Internal}}' "${SPOTDOWN_SERVICE_NETWORK:-spotdown}"   # must print true
```

If it prints `false`, or the network does not exist, these rules are not enough: create an internal
network (`docker network create --internal <name>`), connect the callers to it, set
`SPOTDOWN_SERVICE_NETWORK` to it and run `SPOTDOWN_VERSION=$V docker compose up -d --wait` again,
then run the check
again. The block below runs the same check on every network of the running ui and changes nothing
if one fails.

Run the block where `compose.yaml` is, as the deploying user, with `V` set as for `up`. It is safe
to paste whole: it checks everything first, and if a check fails it prints `STOP` and why, and
inserts no rule. It is the same for the loopback bind and the LAN bind. If you start the stack with
`-p <project>` or `COMPOSE_PROJECT_NAME`, Compose names the network `<project>_ui-published`, not
`spotdown_ui-published`. The block finds it by its `_ui-published` suffix, but its
`docker compose ps` line must see the same project: add the same `-p <project>` to it, or keep
`COMPOSE_PROJECT_NAME` set as for `up`. If it finds no ui, it stops.

```bash
# The ui's networks: each must be internal except ui-published, whose bridge Docker names
# br-<first 12 characters of the network ID>. Sets UI_BR, or prints STOP and returns 1.
ui_bridge() {
  UI_BR=
  local ct n id
  ct=$(SPOTDOWN_VERSION=$V docker compose ps -q ui)
  [ -n "$ct" ] || { echo "STOP: no running ui container here" >&2; return 1; }
  for n in $(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$ct"); do
    [ "$(docker network inspect -f '{{.Internal}}' "$n")" = true ] && continue
    case "$n" in
      *_ui-published) id=$(docker network inspect -f '{{.Id}}' "$n") && UI_BR="br-${id:0:12}" ;;
      *) echo "STOP: the ui's network $n is not internal (see the precondition)" >&2; return 1 ;;
    esac
  done
  [ -n "$UI_BR" ] && ip link show "$UI_BR" >/dev/null ||
    { echo "STOP: no ui-published bridge found" >&2; UI_BR=; return 1; }
}

if ui_bridge; then
  # Forwarded traffic (DOCKER-USER runs before Docker's own rules): replies pass, new connections
  # from the UI to anywhere off its bridge are dropped. Inserted at 1 and 2, in this order.
  sudo iptables -I DOCKER-USER 1 -i "$UI_BR" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN &&
  sudo iptables -I DOCKER-USER 2 -i "$UI_BR" ! -o "$UI_BR" -j DROP &&
  # The host's own services (sshd, a database on the host) are reached through INPUT, which
  # DOCKER-USER does not see. Drop new connections the UI starts to the host.
  sudo iptables -I INPUT 1 -i "$UI_BR" -m conntrack --ctstate NEW -j DROP &&
  sudo iptables -S DOCKER-USER | grep -- "$UI_BR"   # check: the two rules, RETURN first
fi
```

The rules are joined by `&&`, so if one fails (a mistyped `sudo` password, for example) the rest are
not inserted out of order; read the error, remove what was inserted (`sudo iptables -S` shows it)
and run the block again. Run it once per bridge: a second run inserts the rules twice.

Why this is enough, and safe:

- **The UI has no other route out**, as long as the precondition above holds: its only other
  network, the callers' network, is internal. The block checks that before it inserts anything.
- **The backend still works.** The UI reaches the api over the callers' network's bridge, not
  over `ui-published`, so no rule above matches that traffic.
- **The published port still works.** A request to the port arrives from outside the bridge (from
  the LAN interface, or from Docker's port proxy on the host for the loopback bind); the UI's
  answers belong to that connection and pass as `ESTABLISHED`. `RELATED` keeps the ICMP errors
  that belong to it. The `INPUT` rule drops only `NEW` connections, so the UI's answers to the port
  proxy still reach it.
- **It cannot lock you out.** Every rule matches only packets that enter from the UI's bridge
  (`-i "$UI_BR"`). Your SSH session, the api's and the worker's `egress` and other containers are
  never matched. No rule changes a policy or flushes a chain.
- **No DNS exception is needed.** The only name the UI resolves, `spotdown-api`, is answered by
  Docker's embedded DNS server inside the container (`127.0.0.11`). A DNS exception would be needed
  only for a backend origin that is a public name, and the deploy file does not allow one.

What it does not cover:

- **Persistence.** The rules are lost on reboot, and also on a firewall reload that rewrites the
  ruleset: `firewall-cmd --reload` on a host running firewalld, and an iptables-persistent (or
  `netfilter-persistent`) reload or restore of a saved ruleset that does not contain them. Run the
  block again after each of these once Docker has started (for example from a systemd unit ordered
  `After=docker.service`), and check with `sudo iptables -S DOCKER-USER`. The bridge name changes
  when the network is recreated (`docker compose down` removes it): the old rules then match
  nothing, so the UI is open again but nothing breaks. Run the block again after every `down`, and
  remove the stale rules (`sudo iptables -D DOCKER-USER ...` with the old name).
- **IPv6.** The deploy file enables no IPv6 on its networks. If your daemon gives containers IPv6,
  repeat the rules with `ip6tables`.
- **nftables hosts.** On a host whose Docker uses its nftables firewall backend, or whose firewall
  is managed natively in nftables or by firewalld, there is no `DOCKER-USER` chain to use. Write the
  same two rules (replies accepted, then new connections from the UI's bridge dropped) in that
  firewall. The precondition holds there too.

**The LAN bind.** With `SPOTDOWN_PUBLISH_HOST` set to a LAN address, the rules above are unchanged:
the UI still reaches only the backend. To let only your reverse proxy reach the port, also drop new
connections into the UI's bridge that do not come from the proxy (`<proxy address>` is a
placeholder, for example `192.0.2.20`). Run it in the same shell, after the block above: it inserts
nothing unless that block's `DROP` rule is in place for `$UI_BR`.

```bash
sudo iptables -C DOCKER-USER -i "$UI_BR" ! -o "$UI_BR" -j DROP &&
  sudo iptables -I DOCKER-USER 3 -o "$UI_BR" -m conntrack --ctstate NEW ! -s <proxy address> -j DROP
```

It matches only connections into the UI's bridge, so the api's and the worker's outbound traffic
and its replies are untouched. Do not use a rule on the LAN interface itself (for example `-i eth0 ! -s ...`):
it would also drop the replies to the backend's outbound calls.

## Upgrade, pin and roll back

**Upgrade note, YouTube sourcing (spotdown#86).** From this change on, an unset
`ALLOW_YTDLP_SOURCING` means **on**: a stack whose `spotdown.env` does not set it starts calling
YouTube after the upgrade. Before you upgrade, set it explicitly in `spotdown.env`, `true` or
`false`. `ALLOW_YTDLP_SOURCING=false` keeps the old behaviour (every YouTube path off).

1. Read the release notes of every version between the running one and the new one, and back up
   `spotdown-data` (stack stopped).
2. Download that release's deploy files and `SHA256SUMS`, check them as above, and compare the new
   `compose.yaml` with the running one (`diff`).
3. `SPOTDOWN_VERSION=<new> docker compose pull && SPOTDOWN_VERSION=<new> docker compose up -d --wait`,
   with the same `-f` overlays as the running stack.
4. Check `docker compose ps` shows every service `healthy`.

To roll back, run step 3 with the previous version and its deploy files. A newer version may have
migrated the service database on start, so roll back only when the newer release notes say an
older version can read it; otherwise restore the backup of `spotdown-data` taken before the upgrade.

Pin `X.Y.Z` in production; `X.Y` moves on every patch release and changes what a plain
`docker compose pull` installs.

### Upgrading from a SERVICE_MODE compose file

Before spotdown#79 one image ran as either of two apps, chosen by `SERVICE_MODE`. Now the backend
image has no mode and no UI, and the UI is the `spotdown-ui` image.

- **A compose file of your own that sets `SERVICE_MODE=true`, `SERVE_UI=false` or `SPOTDOWN_UI`**
  (classbeat's, for example) keeps working unchanged for one release: the backend ignores those
  settings and logs one warning naming them. Its api still needs `SERVICE_KEY_FILE`; its worker
  needs no key, and the queue (`spotdown`) and the healthchecks are unchanged. Remove the three
  settings before the next release, which drops them.
- **The previous `deploy/compose.yaml`:** download the new deploy files (step 2), install the UI's
  key (`spotdown_ui_backend_key`, as in "Install"), or add `-f compose.no-ui.yaml` to keep running
  without a UI, then step 3. Remove `SPOTDOWN_UI` from `spotdown.env` if it is there.
- **The old standalone app** (`SERVICE_MODE=false`): its routes, its v1 cache under `/data/files`
  and `spotdown.db`, and its `STANDALONE_*`, `UI_ALLOWED_HOSTS`, `UI_PUBLIC_ORIGIN`,
  `PUBLIC_BASE_URL`, `SPOTIFY_CLIENT_SECRET` and conversion settings are gone. Run the UI image
  instead; move `UI_ALLOWED_HOSTS` and `UI_PUBLIC_ORIGIN` to `spotdown-ui.env`, and the Spotify
  secret to a secret file (`compose.spotify.yaml`). The old files in `/data` are no longer read;
  delete them once you no longer need them.

## Rotating a key

Write a new key file as in "Install", then recreate the containers that mount it:

- classbeat's service key: update the caller's copy, then
  `SPOTDOWN_VERSION=$V docker compose up -d --force-recreate api`;
- the UI's key: `SPOTDOWN_VERSION=$V docker compose up -d --force-recreate api ui`; classbeat is
  not touched.

The old key stops working when the api restarts.

## Local development

`docker-compose.yml` in the repository builds both images from the checkout for development
(README, "Local development"): redis, api, worker and ui, with the ui on the loopback, and the
backend's data on the host's `./data` bind mount, as root. The two key files are
`./secrets/spotdown_service_key` and `./secrets/spotdown_ui_backend_key`. It is never a production
install.
