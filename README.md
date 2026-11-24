# meta

Build, run and test a complete IFDD installation from the templates of the
GitHub organisation [`ifdd-de`](https://github.com/ifdd-de).

The command `ifdd-meta` creates the service instances with `cruft`, builds and
starts them in the right order, loads demo data and runs integration tests. The
same tool serves the CI of this repository, a demo server and the servers of an
operator. The design, with everything planned, is in
[`docs/design.md`](docs/design.md).

**Status:** in development. Available so far: `compile`, `config show`, `config check`,
`create`, `update`, `local-setup`, `build`, `up`, `down`, `status`, `logs`, `demo load`, `test`,
`rebuild`, `keycloak add-client`, `keycloak add-user`.

A test installation on a Mac (Colima): [`docs/local-macos-colima.md`](docs/local-macos-colima.md).
The public demo server: [`docs/demo-server.md`](docs/demo-server.md).

## Requirements

- Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) (or pip)
- Git, with `user.name` and `user.email` set (every instance becomes a Git repository)
- Docker with Compose, for the commands that build and run services

## Quick start

```bash
git clone https://github.com/ifdd-de/meta
cd meta
uv run ifdd-meta --help
uv run ifdd-meta --settings path/to/ifdd-meta.yaml config show
```

## Describe an installation

A short description of an installation is enough; `ifdd-meta compile` writes
the complete files next to it:

```bash
uv run ifdd-meta compile config/demo/installation.yaml          # write
uv run ifdd-meta compile --check config/demo/installation.yaml  # compare only
uv run ifdd-meta --settings config/demo/ifdd-meta.yaml config check
```

| Generated file | Content |
|---|---|
| `cookiecutter_all.yaml` | every template variable except the versions, with comments |
| `ifdd-meta.yaml` | settings of the tool |
| `.env.example` | names of the secrets the tool needs, without values |
| `hosts.txt` | host names for DNS records, or a block for `/etc/hosts` (short names or a local CA) |

The description sets the installation name, a host suffix (empty: short names
`id`, `rest`, `ui`, …), the TLS mode (`local-ca`, `letsencrypt`, `own`), a
contact address, the realms, the context root, the services, the template
reference (`templates.ref`, with exceptions per template under `templates.refs`
for templates without that tag) and, under `overrides`, any template variable. Values not derived from
it come from [`src/ifdd_meta/data/defaults.yaml`](src/ifdd_meta/data/defaults.yaml).
Short names work for a quick local test, but not with wf in production: Java
clients such as Kestra send no server name (SNI) for names without a dot, and
the proxy refuses such handshakes (`sniStrict`). `compile` warns; use a suffix
such as `ci.test`. The only example is the description of the public demo,
[`config/demo/installation.yaml`](config/demo/installation.yaml); its host names
lie below `example.ifdd.de`, which is reserved for examples.

### Services of other servers

`services` can also be a mapping with one block per service. A block may set
the host (`host:`), mark the service as running on another server
(`external: true`) and name a template reference (`ref:`; then there is no
`templates.refs`). An external service gets host and URLs in the variables
file, but no instance, no proxy alias, no DNS record and no CA copy. Every
service that a local one needs must be local or external, otherwise `compile`
stops (ts-api needs ts on the same server). `ca_certs` lists further CA
certificates, relative to the description: `create` copies them into every
instance that trusts CAs and gives ct and oauth2-proxy (the login gate of the
proxy, which calls the realm) one bundle file each.

```yaml
installation: dev
tls: letsencrypt
realm: ifdd-dev
templates:
  ref: v26.11.3
services:
  proxy: {}
  be: {host: data.dev.cache.example.ifdd.de}
  ui: {host: ui.dev.example.ifdd.de}
  ts: {host: ts.dev.example.ifdd.de}
  ts-api: {}
  keycloak: {host: oidc.example.ifdd.de, external: true}
  schema: {host: schema.example.ifdd.de, external: true}
  type: {host: type.model.example.ifdd.de, external: true}
  unit: {host: unit.model.example.ifdd.de, external: true}
ca_certs: [certs/company-ca.crt]
```

With an external Keycloak the client secrets come from a file (setting
`keycloak_secrets`, default `keycloak-secrets.env` next to the settings, mode
0600). The server that runs Keycloak writes it for one realm; the tool refuses
to write it to a terminal:

```bash
(umask 077 && uv run ifdd-meta --settings prod/ifdd-meta.yaml \
    keycloak secrets --realm ifdd-dev > keycloak-secrets.env)
```

Copy the file to the other server without putting it into a shell history or a
repository.

### Private files and further realms

`files` lists private files that `create` copies into the instances, each with
its source (relative to the description) and its target (`<service>/<path>` of
a local service). The targets go onto the skip list of `cruft update`; a target
may replace a file of the template (for instance the login theme), which then
no longer receives template changes. Only `create` copies, `update` leaves the
files alone (they may be changed on the server). Never `.env`, `.cruft.json` or
`.git`.

```yaml
realm: prod
extra_realms: [dev, int]
files:
  - {from: realm-dev.toml, to: keycloak/realm/values_dev.toml}
  - {from: realm-int.toml, to: keycloak/realm/values_int.toml}
  - {from: theme/ifdd.css, to: keycloak/themes/ifdd/login/resources/css/ifdd.css}
```

With a local Keycloak every further realm needs its values file (a copy of the
instance's `realm/values.toml` with another `[realm] name`) at
`keycloak/realm/values_<x>.toml`; `compile` checks both directions by the realm
name in the file. `create` then runs `realm/build_realm.py --values …`, and
Keycloak imports these realms at its first start. A realm added later: add it
to `extra_realms` and `files`, `update` keycloak (it lists the new secrets of
`dotenv.dist`: add them to `.env` with random values), copy the values file by
hand, run
`python3 realm/build_realm.py --values realm/values_<x>.toml`, commit, restart
Keycloak.

`config check` compares the variables file with the `cookiecutter.json` of
every template at the chosen reference: a missing variable, one that no template
defines, a version, a secret-like name, a malformed choice or Jinja that refers
to another variable is an error. A template that the description does not use
and that is missing at the reference only gives a note.

## Create the instances

```bash
uv run ifdd-meta --settings config/demo/ifdd-meta.yaml create          # all services
uv run ifdd-meta --settings path/to/ifdd-meta.yaml create --only be,ui  # some
```

`create` runs `cruft create` for every service with its skip list and the
template reference of the settings, then copies optional wheels, creates the
local CA (TLS mode `local-ca`) and copies the client secrets from
`keycloak/.env` into the instances that use them. It never overwrites an
existing instance. Run it as a normal user: the hooks of keycloak and ts refuse
root. The console shows one line per step; the full output is in
`<workdir>/logs/` (mode 0600), `--verbose` shows it as well.

At the end every instance becomes a local Git repository with one commit and no
remote, so that `cruft update` can merge later template changes into it. Git
takes name and e-mail from your configuration only. The `.gitignore` of the
templates keeps secrets, certificates and data out; if a file that looks like a
secret (`.env`, files in `secrets/`, keys) would be committed nevertheless,
`create` stops and leaves that instance without a repository. Nothing is pushed
anywhere: configuration and data stay on the machine.

## Update the instances

```bash
uv run ifdd-meta --settings path/to/ifdd-meta.yaml update --check       # what would change
uv run ifdd-meta --settings path/to/ifdd-meta.yaml update [--only be,ui]
```

Later template changes reach an existing installation one way: from the
template repositories into the instances, never back. Change the references
in the description (`templates.ref`, `templates.refs`), run `compile`, then
`update`. Per instance that is behind its reference, `update` runs
`cruft update` and commits the result ("Update be to v26.11.2"). The result is
what a fresh `create` at that reference gives:

- the values of the variables file are passed again, so the description stays
  the only source of the shared values;
- a variable the template owns gets the template's new default if that default
  changed (for example `ifdd_ui_version`), which cruft alone would not do;
- the skip list in `.cruft.json` stays as `create` wrote it (cruft 2.16.0
  appends the `[tool.cruft]` list of `pyproject.toml` on every update);
- `.env` and other files made from a `*.dist` file are skipped by cruft
  (secrets): a setting that still has the old default of the `*.dist` file gets
  the new one; values changed by hand and secrets stay; new or removed settings
  are shown, not added.

Files on the skip list of a template are instance-owned and not updated (for
ui `src/Env.js`, which also holds `ifddVersion`). Your own committed changes in
an instance are merged (`git apply -3`). An instance needs a clean working
tree; at the first conflict `update` stops and leaves that instance
uncommitted, the following ones untouched. `update` builds and starts nothing:
run `build`, `up` and `test` afterwards. To undo an update of one instance:
`git -C <instance> reset --hard HEAD~1`, then compare `.env` with `dotenv.dist`.

## Host names and the local CA

```bash
uv run ifdd-meta --settings path/to/ifdd-meta.yaml local-setup [--remove]
```

On a test machine (`tls: local-ca`, or short names) the host names go into
`/etc/hosts` and the local CA into the trust store of the operating system
(macOS: the System keychain; Debian/Ubuntu: `update-ca-certificates`). Both need
root, so `local-setup` only prints the commands with the paths of this
installation; nothing is run. The `/etc/hosts` block is marked with the
installation name. `--remove` prints the commands that undo it; run them before
`down --purge`, which deletes the CA file.

## Build, start, stop

```bash
uv run ifdd-meta --settings path/to/ifdd-meta.yaml build
uv run ifdd-meta --settings path/to/ifdd-meta.yaml up            # in order, waits for health
uv run ifdd-meta --settings path/to/ifdd-meta.yaml status
uv run ifdd-meta --settings path/to/ifdd-meta.yaml logs --output logs/
uv run ifdd-meta --settings path/to/ifdd-meta.yaml down [--purge]
```

`up` first checks that the Docker daemon sees the work directory (a throwaway
container of the Traefik image looks for the state file; a daemon in a VM such
as Colima sees only shared directories), then creates the Docker networks,
starts Traefik alone, then keycloak, be, ui,
schema, type, unit, model, the whole proxy (oauth2-proxy needs the realm), wf, ts,
ts-api and lab, each with `docker compose up --wait` or the instance's `adm_02_run.sh`.
`down --purge` also removes volumes, networks and the instances, but keeps
`proxy/letsencrypt/acme.json` in the persist directory (`create` puts it back)
and the Docker volume `<lab>_dind-data` with the homes of the lab users (a new
lab instance of the same name finds it again; `docker volume rm` removes it).

lab (JupyterHub, `ifdd-de/lab`) is optional: an installation gets it only when
its description lists it under `services`. `compile` then sets its Keycloak
client (`ifdd-lab`) and lets the proxy join its internal network `lab-net`;
without lab both stay empty. Its user image needs about 3 GB, twice with
Docker-in-Docker. lab has no release 26.11.2: a description whose
`templates.ref` is v26.11.2 needs a reference of lab under `templates.refs`
(as in `config/demo/installation.yaml`), also without lab, because
`config check` reads every template.

wf keeps its flows in Git. By default (`wf_git: local`) ifdd-meta runs a small
Gitea for that behind the proxy under the host `git`; `up` starts it before wf
and creates the user, the repository and the token for wf. With
`wf_git: external` the description names the repository in its overrides and
`IFDD_META_WF_GIT_TOKEN` (environment or `.env`) goes into wf as
`SECRET_GIT_TOKEN`.

## Demo data

```bash
uv run ifdd-meta --settings path/to/ifdd-meta.yaml demo load
```

Creates the user `ifdd-demo` (groups: readers, writers, privileged, the wf
group with wf, the lab users with lab, and the context root; with ts the realm
role of a Grafana viewer),
or sets its password and adds missing groups and roles, and loads the fake energy grid and its time series with the ct pipelines
`generate-energy-grid` and `generate-time-series` (fixed seed: a second run adds
nothing). The password is `IFDD_META_DEMO_PASSWORD` from the environment or the
`.env` next to the settings; without one a new password is written into that
`.env` (mode 0600). It is never shown. The services must be running.

The workflow `integration.yml` runs `create`, `build` and `up` on a fresh GitHub
runner with the real images and checks that every service answers through the
proxy, runs `local-setup` (its printed commands), `demo load` twice, then
`ifdd-meta test` with `tests: write`, and at the end `rebuild` as the demo
server runs it; the JUnit report, logs,
image digests and the use of disk and memory are kept as artefacts.

## Rebuild

```bash
uv run ifdd-meta --settings path/to/ifdd-meta.yaml rebuild [--demo] [--test] [--prune]
```

Tears the installation down (`down --purge`, the persist paths stay) and runs
`create`, `build` and `up` again; `--demo` then loads the demo data, `--test`
runs the integration tests. `--prune` deletes every image and the build cache
after `down`, while nothing runs, and the build cache again after `build`:
only on a host that runs nothing else. (Images of ct, of the task runner of wf
and of the lab user servers have no container most of the time, so deleting
"unused" images after `up` would remove them.) Each run writes
`rebuild-status.txt` (start, end, result, the failed step) into the persist
directory; exit 1 if a step fails. The public
demo runs it once a week ([`docs/demo-server.md`](docs/demo-server.md)).

## Test

```bash
uv run ifdd-meta --settings path/to/ifdd-meta.yaml test [-k EXPR] [-x] [-v] [--junit FILE]
```

`test` runs the integration tests of the package against the running
installation (pytest): Keycloak, be, ts-api, schema/type/unit, the proxy
headers, Grafana, and with test data the ct pipelines, the Git round trip of
wf and lab (login and groups of the hub; in a user server the notebook helper
reads with the rights of the person logged in, a user without the context
pattern gets 403; the services are reachable, the internet is not). Tests that write run only with the setting `tests: write`
(`IFDD_META_TESTS=write`); the default `read` leaves the data alone. Tests of a
service that is not installed are skipped.

The browser tests (logins into ui, Grafana, wf and lab through Keycloak, and the
demo user after `demo load`) need the extra `browser` and Chromium. On Linux
they trust the local CA through their own NSS database, which needs `certutil`
(Debian/Ubuntu: `libnss3-tools`). On macOS Chromium uses the System keychain:
without the local CA in it (`local-setup`) the browser tests are skipped with a
hint. Without Playwright they are skipped as well.

```bash
uv sync --extra browser && uv run python -m playwright install --with-deps chromium  # Linux
uv sync --extra browser && uv run python -m playwright install chromium              # macOS
uv run ifdd-meta --settings path/to/ifdd-meta.yaml test --screenshots shots/
```

## Add a service client

```bash
uv run ifdd-meta --settings path/to/ifdd-meta.yaml keycloak add-client my-script \
    --role ifdd:ifdd_nodes_ro --role ifdd:ops.example.ifdd.de --create-roles \
    --default-scope ifdd_nodes_ro --secret-file my-script.secret
```

The client gets client credentials only, no full scope, the roles in its service
account and in its scope mapping. The secret goes into a new file with mode
0600 and is never shown.

## Add a user

```bash
uv run ifdd-meta --settings path/to/ifdd-meta.yaml keycloak add-user jane \
    --group ifdd_nodes_readers --group ifdd_ctx_ifdd.de --realm-role readonly \
    --password-file jane.password [--temporary]
```

The password is generated and written into a new file with mode 0600; it is
never shown. `--temporary` makes Keycloak ask for a new one at the first login.

## Configuration

| File | Content | In Git |
|---|---|---|
| `ifdd-meta.yaml` | settings of the tool (template source and reference, services, work directory, …) | example only |
| variables file, e.g. `cookiecutter_all.yaml` | the template variables, as a cookiecutter user config | example only |
| `.env` next to `ifdd-meta.yaml` | secrets and machine-local settings | never |

Every setting can also come from an environment variable `IFDD_META_<NAME>`
(for instance `IFDD_META_TEMPLATES_REF`) or, for some, from an option. The
precedence, highest first: command line, environment variable, `.env`,
`ifdd-meta.yaml`, built-in default. `ifdd-meta config show` prints each value
with its source.

Paths in `ifdd-meta.yaml` and its `.env` are relative to that file; paths from
the command line or the environment are relative to the working directory.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

## AI assistance

Some parts of this repository, in particular the tests, the test data and the
documentation, were developed or improved with the help of large language
models (LLMs). All changes were reviewed and released by the maintainer.

## License

BSD-3-Clause, see [`LICENSE`](LICENSE).
