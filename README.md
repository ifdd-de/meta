# meta

Build, run and test a complete IFDD installation from the templates of the
GitHub organisation [`ifdd-de`](https://github.com/ifdd-de).

The command `ifdd-meta` creates the service instances with `cruft`, builds and
starts them in the right order, loads demo data and runs integration tests. The
same tool serves the CI of this repository, a demo server and the servers of an
operator. The design, with everything planned, is in
[`docs/design.md`](docs/design.md).

**Status:** in development. Available so far: `compile`, `config show`, `config check`,
`create`, `build`, `up`, `down`, `status`, `logs`.

## Requirements

- Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) (or pip)
- Git
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
| `hosts.txt` | host names for DNS records, or a line for `/etc/hosts` with short names |

The description sets the installation name, a host suffix (empty: short names
`id`, `rest`, `ui`, …), the TLS mode (`local-ca`, `letsencrypt`, `own`), a
contact address, the realms, the context root, the services, the template
reference and, under `overrides`, any template variable. Values not derived from
it come from [`src/ifdd_meta/data/defaults.yaml`](src/ifdd_meta/data/defaults.yaml).
Short names work for a quick local test, but not with wf in production: Java
clients such as Kestra send no server name (SNI) for names without a dot, and
the proxy refuses such handshakes (`sniStrict`). `compile` warns; use a suffix
such as `ci.test`. The only example is the description of the public demo,
[`config/demo/installation.yaml`](config/demo/installation.yaml); its host names
lie below `example.ifdd.de`, which is reserved for examples.

`config check` compares the variables file with the `cookiecutter.json` of
every template at the chosen reference: a missing variable, one that no template
defines, a version, a secret-like name, a malformed choice or Jinja that refers
to another variable is an error.

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

## Build, start, stop

```bash
uv run ifdd-meta --settings path/to/ifdd-meta.yaml build
uv run ifdd-meta --settings path/to/ifdd-meta.yaml up            # in order, waits for health
uv run ifdd-meta --settings path/to/ifdd-meta.yaml status
uv run ifdd-meta --settings path/to/ifdd-meta.yaml logs --output logs/
uv run ifdd-meta --settings path/to/ifdd-meta.yaml down [--purge]
```

`up` creates the Docker networks, starts Traefik alone, then keycloak, be, ui,
schema, type, unit, the whole proxy (oauth2-proxy needs the realm), wf, ts and
ts-api, each with `docker compose up --wait` or the instance's `adm_02_run.sh`.
`down --purge` also removes volumes, networks and the instances, but keeps
`proxy/letsencrypt/acme.json` in the persist directory; `create` puts it back.
wf keeps its flows in Git. By default (`wf_git: local`) ifdd-meta runs a small
Gitea for that behind the proxy under the host `git`; `up` starts it before wf
and creates the user, the repository and the token for wf. With
`wf_git: external` the description names the repository in its overrides and
`IFDD_META_WF_GIT_TOKEN` (environment or `.env`) goes into wf as
`SECRET_GIT_TOKEN`.

The workflow `integration.yml` runs `create`, `build` and `up` on a fresh GitHub
runner with the real images and checks that every service answers through the
proxy, then runs `ifdd-meta test` with `tests: write`; the JUnit report, logs,
image digests and the use of disk and memory are kept as artefacts.

## Test

```bash
uv run ifdd-meta --settings path/to/ifdd-meta.yaml test [-k EXPR] [-x] [-v] [--junit FILE]
```

`test` runs the integration tests of the package against the running
installation (pytest): Keycloak, be, ts-api, schema/type/unit, the proxy
headers, Grafana, and with test data the ct pipelines and the Git round trip of
wf. Tests that write run only with the setting `tests: write`
(`IFDD_META_TESTS=write`); the default `read` leaves the data alone. Tests of a
service that is not installed are skipped.

## Add a service client

```bash
uv run ifdd-meta --settings path/to/ifdd-meta.yaml keycloak add-client my-script \
    --role ifdd:ifdd_nodes_ro --role ifdd:ops.example.ifdd.de --create-roles \
    --default-scope ifdd_nodes_ro --secret-file my-script.secret
```

The client gets client credentials only, no full scope, the roles in its service
account and in its scope mapping. The secret goes into a new file with mode
0600 and is never shown.

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
