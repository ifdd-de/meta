# ifdd-meta – design

- **Status:** accepted after review (2026-11-17); implemented step by step
- **Date:** 2026-11-17

`ifdd-meta` builds, runs and tests a complete IFDD installation from the templates of
the GitHub organisation `ifdd-de`. The same tool serves three places:

| Place | What it does there |
|---|---|
| CI of this repository | integration run on a fresh runner: create, build, start, load demo data, test, collect logs, tear down |
| a demo server | rebuilds the public demo from a fixed release at regular intervals |
| an operator's own servers | creates and updates the instances of one server, with a private configuration |

## 1. Scope

**In scope:** creating instances with `cruft create`, updating them with
`cruft update`, building images, starting and stopping services in the right
order, distributing the local CA and the client secrets, Keycloak helpers (users,
clients, context roles), demo data, integration tests, the instance chapter of the
documentation.

**Out of scope:** remote control of other servers (one run = one server),
orchestrators other than Docker Compose, a secret store, instances as Git
repositories of their own (open question G1–G9 of the handover; the tool neither
needs nor prevents them).

## 2. Principles

1. **Use the real paths.** The tool calls `cruft`, the `adm_*` scripts of the
   instances and `docker compose` the way an operator would. It does not
   reimplement what an instance already does.
2. **One file, one purpose.** Template variables, tool settings and secrets live in
   separate files.
3. **Explicit configuration.** Every template variable is set in the variables
   file (rule B.3 of the handover); versions of the templates are the only
   exception.
4. **Secrets never in YAML** and never on the console or in logs.
5. **KISS.** No option without a concrete need. Few dependencies.
6. **Neutral names.** The repository holds example values only (`.test`,
   `example.org`, `example.ifdd.de`); real host names and addresses stay in private
   files.

## 3. Inputs

| Input | Content | In Git |
|---|---|---|
| variables file, e.g. `cookiecutter_all.yaml` | template variables as a cookiecutter user config (`default_context:`) | example only |
| settings file `ifdd-meta.yaml` | settings of the tool (section 4) | example only |
| `.env` next to the settings file | secrets and machine-local settings | never |
| environment variables `IFDD_META_*` | settings and secrets | – |
| command line | settings for one run | – |

**Precedence of settings:** command line → environment variable → `.env` →
`ifdd-meta.yaml` → built-in default.

**Template variables** come from the variables file only. One run may override a
value with `--set name=value`; the tool passes the file with `--config-file` and the
overrides with `--extra-context`, so the file reaches cruft unchanged.

**Secrets** the tool itself needs (for instance the password of the demo user or a
Git token for wf) come from `.env` or the environment. `config check` rejects
variable names ending in `_secret`, `_password` or `_token` in the variables file;
no template variable has such a name today.

## 4. Settings file

```yaml
# ifdd-meta.yaml – settings of one installation (one server)
installation: example               # name; work directory and log names
variables: cookiecutter_all.yaml    # variables file, relative to this file
environment: production             # production | development
templates:
  source: https://github.com/ifdd-de   # URL base or directory of local clones
  ref: v26.11.1                         # tag, branch or commit for all templates
  refs: {}                              # exceptions, e.g. {be: main}
services: [proxy, keycloak, be, ui, schema, type, unit, ct, ts, ts-api, wf, model]  # run in catalogue order
workdir: work/example               # instances are created here
persist: state/example              # survives "down --purge" (ACME state, backups)
wheels: {}                          # optional unreleased builds, e.g. {be-core: ../be-core/dist}
wait_timeout: 300                   # seconds per service start
```

Each key except the mappings `templates.refs` and `wheels` has an environment
variable (`IFDD_META_TEMPLATES_REF`, `IFDD_META_SERVICES` as a comma-separated
list, …) and, where it
makes sense for one run, an option (`--ref`, `--workdir`, …).

## 5. Service catalogue

What the tool knows about each template is data in the package, not configuration:

| Template | Skip paths for cruft | Needs the local CA | Build | Start |
|---|---|---|---|---|
| proxy | `.env`, `secrets`, `certs`, `letsencrypt`, `logs`, `traefik/private` | creates it | compose | compose, in two phases |
| keycloak | `.env` | – | compose | compose |
| be | `.env`, `envs`, `certs`, `app/routers`, `app/settings.py`, `tests` | yes | compose | compose |
| ui | `.env`, `envs`, `src/private`, `src/Env.js`, `certs` | yes | compose | compose |
| schema, type, unit | `.env`, `examples` | – | compose | compose |
| ct | `.env`, `certs`, `data`, `plugins`, `private`, `tests/private` | yes | `adm_01_build.sh` | runs on demand |
| ts | `.env`, `secrets`, `grafana/dashboards/private` | yes (`certs/`) | compose + `adm_01_plugins.sh` | `adm_02_run.sh` |
| ts-api | `.env`, `certs` | yes | compose | compose |
| wf | `.env`, `secrets`, `certs` | yes | `adm_01_build.sh` | `adm_02_run.sh` |
| model | `.env`, `src/linkml`, `tests/reference` | – | – | – (`just test`; `just publish` later) |

The skip paths are those the template READMEs recommend (handover B.5); the
catalogue closes the gaps of the old meta repository (wf, ts).

**Start order:** networks → proxy (Traefik only) → keycloak, be, ui, schema, type,
unit → proxy (all services, oauth2-proxy needs the realm) → wf → ts → ts-api.

**Secret distribution** (from `keycloak/.env` to the users, values never printed):
ts client → `time-series/secrets/`, proxy client → `proxy/secrets/`, ct client →
`ct/.env`, API client → `wf/secrets/kestra.env`; one entry per further realm of
`ifdd_keycloak_extra_realms`.

## 6. Commands

| Command | Does | Changes |
|---|---|---|
| `config check` | files found and parsed; every key of the variables file defined by at least one template at the chosen ref; every template variable present (except versions), a missing one is an error; no secret-like names; settings valid | nothing |
| `compile` | writes the variables file and the other files from a description (section 10); `--check` only compares | generated files |
| `config show` | effective settings with the source of each value | nothing |
| `create` | `cruft create` per service with skips and ref; wheels; local CA unless Let's Encrypt or own certificates; CA and secrets distributed; state recorded | work directory |
| `update` | `cruft update` per instance, `--set` passed as `--variables-to-update`; lists `.rej` files and fails on them | instances |
| `build` | images of all or selected services | images |
| `up` / `down` | start in order / stop; `down --purge` also deletes volumes and the work directory, except the persist paths | containers |
| `status`, `logs` | state of all services; logs into a directory (CI artefacts) | nothing / log files |
| `keycloak add-user`, `add-client`, `add-context-role` | the former `.adm_tools` scripts, for every realm of the instance | realm |
| `demo load` | demo user and the ct pipelines `generate-energy-grid` and `generate-time-series` | data |
| `test` | integration tests against the running installation (section 8) | test data |
| `docs render` | the instance chapter (former `.adm_docs`) with the same variables | output directory |

Every command takes `--only SERVICE[,…]`. Commands are idempotent where the
underlying tools allow it; `create` refuses an existing instance.

## 7. Output

The console shows one overview line per step; everything else goes to a log file
(`<workdir>/logs/<command>-<time>.log`, mode 0600). On the first error the tool stops
and prints the last 20 lines of the log. `--verbose` shows everything. Exit codes:
0 success, 1 failure, 2 usage error.

## 8. Tests

| Level | Tool | Checks |
|---|---|---|
| unit | pytest with fakes for `cruft`, `docker` and `git` | the tool itself, without containers |
| plumbing | pytest | health checks, network aliases and TLS (`proxy/adm_check_hosts.sh`), security headers and CSP |
| API | pytest + HTTP client, tokens by client credentials | be: write, read, bitemporal history, 403 for a foreign context · ts-api: write, read, 422 for a wrong unit · schema/type/unit: content, CORS · ct pipeline writes into be and ts-api · wf runs a flow · Grafana data sources healthy |
| browser | Playwright (Chromium) | login through Keycloak (PKCE), ui shows the tabs, oauth2-proxy in front of wf |
| security | `tools-pentest` (passive, outside view) | later |
| upgrade | previous release → demo data → `update` → checks again | later |

Workflows of this repository: `test.yml` (unit tests) on every push;
`integration.yml` (full run on `ubuntu-latest`) by hand, on pushes to branches, on
tags and nightly. A nightly run sets `IFDD_META_TEMPLATES_REF=main`. The wf Git sync
uses a local Git server in the run, not GitHub. Each run keeps as artefacts: JUnit
report, container logs, image list with digests, `docker system df` and
`docker stats`.

## 9. Repository layout

```
README.md  LICENSE  CHANGELOG.md  pyproject.toml
src/ifdd_meta/            # package: cli, settings, catalogue, steps, keycloak, docs
config/demo/              # description of the public demo (the only example)
docs/design.md            # this document
docs/instances/           # cookiecutter template of the instance chapter (en, de)
tests/unit/               # tests of the tool
tests/integration/        # tests against a running installation
.github/workflows/        # test.yml, integration.yml
work/  state/             # in .gitignore
```

Python 3.11 or newer; run from a clone with `uv run ifdd-meta …`. Not on PyPI.
The command line uses typer, which cruft depends on anyway; the tool resolves
environment variables itself so that `config show` can name the source of every
value.

## 10. Configuration compiler

Writing the variables file by hand means about 213 template variables, 30 of them
shared, 31 of them host names or URLs that depend on each other (realm URL, OIDC
endpoints, internal hosts, gate hosts, CORS origins, content URLs). The template
defaults do not form a consistent set (for instance `ifdd_be_host` `data`,
`ifdd_unit_url` `http://unit:80`, OIDC URLs with the realm `testrealm`), so they
cannot simply be taken over.

`ifdd-meta compile` turns a short description of an installation into the complete,
explicit files. The values that it does not derive come from a defaults file of
this repository (`src/ifdd_meta/data/defaults.yaml`), not from the templates;
`config check` and `compile --check` in the CI catch a template release that
adds, renames or drops a variable. The only example description in the
repository is the one of the public demo (`config/demo/installation.yaml`): its
host names lie in the namespace that ADR 0002 reserves for examples, and it holds
no address and no secret.

```yaml
# installation.yaml – description of one installation (example)
installation: demo
hosts:
  suffix: demo.grid.example.ifdd.de   # empty: short names (id, rest, ui, …)
tls: letsencrypt                      # local-ca | letsencrypt | own
contact: maintainers@ifdd.de
realm: ifdd
extra_realms: []
context_root: ifdd.de
services: [proxy, keycloak, be, ui, schema, type, unit, ct, ts, ts-api, wf, model]
templates:
  ref: v26.11.1
overrides:                            # any template variable, set explicitly
  ifdd_ts_retention_period: "10y"
```

Output (each file marked as generated):

| File | Content |
|---|---|
| `cookiecutter_all.yaml` | every template variable: derived values, values of `overrides`, the rest from the templates' `cookiecutter.json` at the chosen ref |
| `ifdd-meta.yaml` | settings of the tool (section 4) |
| `.env.example` | names of the secrets the tool needs, without values |
| `hosts.txt` | host names for DNS records, or `/etc/hosts` lines for short names |

The generated variables file stays explicit (rule B.3) and reaches cruft unchanged.
`compile --check` fails if the files on disk differ from a fresh compilation.
Generated files are always overwritten as a whole; changes go into the
description or its `overrides`. One description describes one server; the format
leaves room for a later `servers:` list.

## 11. From the old meta repository

| Old | New |
|---|---|
| `.adm_meta_01_all.sh` | `create`, `build`, `up` (and `demo load`) in sequence; CI and cron call them one by one |
| `.adm_01_create_services.sh` | `create` |
| `.adm_02_build_services.sh` | `build` |
| `.adm_03_up_services.sh` | `up` |
| `.adm_04_demo.sh` | `demo load` |
| `.adm_31_test_services.sh` | unit tests of the instances inside `test` |
| `.adm_32_pentest_source.sh` | later `pentest` |
| `.adm_meta_99_destroy.sh` | `down --purge` |
| `.adm_example_01_product_count.sh` | dropped |
| `.adm_tools/*.py` | `keycloak …`, secret distribution inside `create`, `docs render` |
| `.adm_docs/` | `docs/instances/` |
| `IFDD_TEMPLATE_BASE`, `IFDD_TEMPLATE_REF` | `templates.source`, `templates.ref` |
| `IFDD_BE_CORE_DIST`, `IFDD_TS_CORE_DIST` | `wheels` (optional; the packages are on PyPI) |
