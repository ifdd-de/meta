# The public demo server

The public demo of IFDD runs on a server of its own, under
`*.demo.grid.example.ifdd.de` (ADR 0002 of `ifdd-de/overview`). It is the
installation of [`config/demo`](../config/demo/installation.yaml): every
service except lab, Let's Encrypt, the local Git server for the flows of wf.
Once a week a cron job rebuilds it from the newest release of this repository
with `ifdd-meta rebuild`; only the certificates of Let's Encrypt
(`acme.json`) survive. Users, data and flows start fresh every week.

There are no public accounts. `demo load` creates the user `ifdd-demo` with a
password that only the operator knows.

## Requirements

- Linux on x86_64 with at least 16 GB of memory and 50 GB of disk. The
  integration run of this repository (all services with lab) ends with about
  25 GB of images, 16 GB of build cache and 3 GB of volumes; `--prune`
  deletes every image and the cache before each build and the cache after it,
  so each week pulls and builds everything again.
- Docker Engine with the Compose plugin. The integration run uses Docker 29.4
  and Compose v5.1 (GitHub runner, 2026-11-22). Older versions, such as the
  packages of a distribution, are not tested; Docker's own repository
  (<https://docs.docker.com/engine/install/>) gives the tested ones.
- Ports 80 and 443 reachable from the internet (Let's Encrypt uses the HTTP-01
  challenge on port 80).
- One DNS record (A, with IPv6 also AAAA) for every name in
  [`config/demo/hosts.txt`](../config/demo/hosts.txt), pointing to the
  server. The dashboard of Traefik has no record.
- A normal user that runs everything. `create` refuses root (the hooks of
  keycloak and ts write the user's ids). The user is in the group `docker`,
  which is equivalent to root on that host.
- `git` and [`uv`](https://docs.astral.sh/uv/).

## Set up the server

As the operator (here `ifdd`):

```bash
sudo adduser --disabled-password --gecos "" ifdd
sudo usermod -aG docker ifdd
sudo -iu ifdd
```

Then, as `ifdd`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
git config --global user.name "IFDD demo"
git config --global user.email "maintainers@ifdd.de"
git clone https://github.com/ifdd-de/meta "$HOME/meta"
mkdir -p "$HOME/demo/work" "$HOME/demo/state" "$HOME/bin"
touch "$HOME/ifdd-demo.env" && chmod 600 "$HOME/ifdd-demo.env"
printf 'IFDD_META_WORKDIR=%s\nIFDD_META_PERSIST=%s\nIFDD_META_DEMO_PASSWORD=%s\n' \
    "$HOME/demo/work" "$HOME/demo/state" \
    "$(head -c 24 /dev/urandom | base64 | tr -d '/+=')" > "$HOME/ifdd-demo.env"
```

The installer of uv puts `uv` into `~/.local/bin`, which the current shell
does not have in its `PATH` yet; the `export` covers this shell, the script
below sets it itself (also for cron).

`ifdd-demo.env` is the only private file: the paths and the password of
`ifdd-demo`. The description and the settings come unchanged from
`config/demo` of the checked-out release.

The script of the weekly run, `$HOME/bin/ifdd-demo-rebuild`:

```sh
#!/bin/sh
# Rebuilds the IFDD demo from the newest release of ifdd-de/meta.
set -eu
PATH="$HOME/.local/bin:$PATH"
cd "$HOME/meta"
git fetch --quiet --tags origin
tag=$(git tag --list 'v*' --sort=-version:refname | head -n 1)
git -c advice.detachedHead=false checkout --quiet "$tag"
uv sync --quiet --python 3.14
set -a
# shellcheck source=/dev/null
. "$HOME/ifdd-demo.env"
set +a
echo "ifdd-meta $tag"
exec uv run --no-sync ifdd-meta --settings config/demo/ifdd-meta.yaml \
    rebuild --prune --demo --test
```

```bash
chmod 755 "$HOME/bin/ifdd-demo-rebuild"
```

## First run

```bash
"$HOME/bin/ifdd-demo-rebuild"
cat "$HOME/demo/state/rebuild-status.txt"
```

The first run takes longer: it pulls and builds every image. Traefik requests
one certificate per host; they land in `acme.json`, which `down --purge` keeps
in the persist directory, so later runs request none.

`--test` runs the integration tests that only read (setting `tests: read` of
`config/demo`); the browser tests are skipped because the server does not
install the extra `browser`. The tests reach the services under their public
names. If the server cannot reach its own public address (NAT without
hairpinning), put the names of `config/demo/hosts.txt` with `127.0.0.1` into
`/etc/hosts`.

## Weekly run

```bash
crontab -e
```

```text
PATH=/home/ifdd/.local/bin:/usr/local/bin:/usr/bin:/bin
17 3 * * 0 flock -n "$HOME/.ifdd-demo.lock" "$HOME/bin/ifdd-demo-rebuild" > "$HOME/ifdd-demo-cron.log" 2>&1
```

Sunday 03:17 in the time zone of the server. `flock` skips a run while the
previous one is still going. The demo is down for the length of a run.

## After a run

| What | Where |
|---|---|
| result of the last run | `$HOME/demo/state/rebuild-status.txt` (`result: ok` or `failed`, the step, the first line of the error) |
| console of the last run | `$HOME/ifdd-demo-cron.log` |
| full log of every run | `$HOME/demo/work/logs/rebuild-*.log` (mode 0600; delete old ones now and then) |
| state of the containers | `uv run --no-sync ifdd-meta --settings config/demo/ifdd-meta.yaml status` in `$HOME/meta`, with the variables of `ifdd-demo.env` set |

## Security notes

- The admin console of Keycloak is reachable through the proxy at
  `https://id.demo.grid.example.ifdd.de/admin/`. Its bootstrap admin gets a new
  generated password with every run (`$HOME/demo/work/keycloak/.env`).
- The demo holds no real data: only the fake data of the ct pipelines.
- Telemetry and update checks of the services are off (Grafana, Kestra,
  Traefik, Gitea).
