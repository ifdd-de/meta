# Local test on macOS with Colima

A complete installation on a Mac, with the host names `*.demo.test`, a local CA
and the same Compose files as a server (`environment: production`). It is the
test before a demo or production server. Every block can be copied as a whole
into a terminal (zsh or bash).

The commands that need root (`/etc/hosts`, the System keychain) are only
printed by `ifdd-meta local-setup`; you run them yourself.

## 1. Requirements

| What | Checked with | Note |
|---|---|---|
| macOS on Apple silicon (arm64) | `uname -m` | |
| Colima with the Docker runtime | `colima list` | at least 4 CPUs, 12 GiB memory, 60 GiB disk |
| Docker CLI, Compose and buildx | `docker compose version`, `docker buildx version` | the Dockerfiles of ui and ct use BuildKit |
| Git with name and e-mail | `git config user.name`, `git config user.email` | every instance becomes a Git repository |
| uv | `uv --version` | brings Python 3.14 |
| free ports 80 and 443 | `sudo lsof -iTCP:80 -iTCP:443 -sTCP:LISTEN` | no output: free |

With Homebrew, the Compose and buildx plugins need the plugin directory in
`~/.docker/config.json`; `brew info docker-compose` and `brew info docker-buildx`
show the line. A Colima VM that is too small can be resized:

```bash
colima stop
colima start --cpu 4 --memory 12 --disk 60
colima list
```

## 2. Paths

Set these in every new terminal (or put them into `~/.zshrc`):

```bash
export IFDD_META="$HOME/src/ifdd-de/meta"              # clone of this repository
export IFDD_INST="$HOME/ifdd/installations/notebook"   # description, in a private repository
export IFDD_RUN="$HOME/ifdd/run/notebook"              # instances and kept files, not in Git
```

Colima shares the home directory with its VM; keep all three below `$HOME`.

## 3. ifdd-meta

```bash
git clone https://github.com/ifdd-de/meta "$IFDD_META"
cd "$IFDD_META"
uv sync --python 3.14 --extra browser
.venv/bin/python -m playwright install chromium
```

To try a pull request before it is merged:

```bash
cd "$IFDD_META"
git fetch origin
git switch <branch of the pull request>
uv sync --python 3.14 --extra browser
```

In every new terminal:

```bash
source "$IFDD_META/.venv/bin/activate"
cd "$IFDD_INST"
```

## 4. Describe the installation

The description lives in a private repository; `.env` holds secrets and paths
of this machine and stays out of Git.

```bash
mkdir -p "$IFDD_INST" "$IFDD_RUN"
cd "$(dirname "$IFDD_INST")"
git init -q 2>/dev/null || true
printf '.env\n' > .gitignore
cd "$IFDD_INST"
cat > installation.yaml <<'EOF'
installation: notebook
hosts:
  suffix: demo.test
tls: local-ca
realm: ifdd
context_root: ifdd.de
services: [proxy, keycloak, be, ui, schema, type, unit, ct, ts, ts-api, wf, model]
wf_git: local
templates:
  ref: v26.11.2
  refs:
    schema: v26.11.1
    type: v26.11.1
    unit: v26.11.1
    ct: v26.11.1
    ts: v26.11.1
    model: v26.11.1
EOF
printf 'IFDD_META_WORKDIR=%s\nIFDD_META_PERSIST=%s\nIFDD_META_TESTS=write\n' \
    "$IFDD_RUN/work" "$IFDD_RUN/state" > .env
chmod 600 .env
```

`IFDD_META_TESTS=write` lets `ifdd-meta test` add test data; use `read` on an
installation whose data matters.

## 5. Compile and check

```bash
ifdd-meta compile installation.yaml
ifdd-meta config show
ifdd-meta config check
```

`compile` writes `cookiecutter_all.yaml`, `ifdd-meta.yaml`, `.env.example` and
`hosts.txt` next to the description. Commit the description and these files:

```bash
git add -A && git commit -q -m "Notebook installation" && git status --short
```

## 6. Create the instances

```bash
ifdd-meta create
```

Each instance in `$IFDD_RUN/work` is now a local Git repository with one commit
and no remote; `cruft update` merges later template changes into it.

## 7. Host names and the local CA

```bash
ifdd-meta local-setup
```

Copy the printed `sudo` lines and run them: one adds the block of
`hosts.txt` to `/etc/hosts`, one trusts the local CA in the System keychain.
Check:

```bash
ping -c 1 id.demo.test
```

Browsers read the keychain when they start: restart them afterwards.

## 8. Build and start

```bash
ifdd-meta build
ifdd-meta up
ifdd-meta status
```

The first `build` pulls and builds several gigabytes. `up` starts the services in
order and waits until each one is healthy.

```bash
CA="$IFDD_RUN/work/proxy/certs/ca.crt"
for url in https://id.demo.test/realms/ifdd/.well-known/openid-configuration \
    https://rest.demo.test/version https://ui.demo.test/ https://ts.demo.test/api/health; do
  printf '%s %s\n' "$(curl -sS --cacert "$CA" -o /dev/null -w '%{http_code}' "$url")" "$url"
done
```

Every line should start with `200`.

## 9. Demo data

```bash
ifdd-meta demo load
```

This creates the user `ifdd-demo` (or sets its password again) and loads the fake
energy grid and its time series through ct. The password is
`IFDD_META_DEMO_PASSWORD` from `.env`; if there is none, a new one is written
there. It is never shown; to read it:

```bash
grep '^IFDD_META_DEMO_PASSWORD=' .env
```

## 10. Test

```bash
ifdd-meta test -v --junit "$IFDD_RUN/junit.xml" --screenshots "$IFDD_RUN/screenshots"
```

The browser tests are skipped with a hint if Chromium does not trust the local
CA (step 7).

Then in the browser: <https://ui.demo.test/> (sign in as `ifdd-demo`),
<https://ts.demo.test/> (Grafana), <https://wf.demo.test/> (Kestra).

## 11. Logs to share

`ifdd-meta` writes one log per command into `$IFDD_RUN/work/logs` (mode 0600).
Before you pass logs on, remove every file that contains a secret of this
installation:

```bash
mkdir -p "$IFDD_RUN/share" && cp "$IFDD_RUN"/work/logs/*.log "$IFDD_RUN/share/"
ifdd-meta logs --output "$IFDD_RUN/share/containers"
python "$IFDD_META/tools/ci/scrub_secrets.py" "$IFDD_RUN/work" "$IFDD_RUN/share"
```

The script prints the names of the files it deleted, never the values.

## 12. Stop, start, remove

```bash
ifdd-meta down        # stop; data stays
ifdd-meta up          # start again
```

To remove everything, undo step 7 first (`down --purge` deletes the CA file):

```bash
ifdd-meta local-setup --remove
```

Run the printed `sudo` lines, then:

```bash
ifdd-meta down --purge
```

## Troubleshooting

| Symptom | Check |
|---|---|
| `create`: Git needs a name and an e-mail address | `git config --global user.name …` and `user.email …` |
| `up` hangs or a port is taken | `sudo lsof -iTCP:443 -sTCP:LISTEN`; another landscape still running? |
| browser tests skipped: Chromium does not trust the local CA | step 7, then start the test again |
| `build` fails for lack of space | `colima list`; `docker system df`; resize the VM (section 1) |
| a host name does not resolve | `grep -A1 'BEGIN ifdd-meta notebook' /etc/hosts` |

## Honesty notes

- Tested on Linux only (GitHub runner and a sandbox): `compile`, `create` with
  the real templates, the instance repositories and their `.gitignore`,
  `local-setup` and its Linux commands, `demo load`, the browser tests.
- Not run on macOS by the authors: the `security` commands (taken from
  `man security`), the BSD form `sed -i ''`, Chromium with the System keychain,
  Colima's port forwarding for 80 and 443, the arm64 variants of all images and
  OpenSSL 4 in `proxy/adm_01_certs.sh`. The first run on a Mac is the test.
