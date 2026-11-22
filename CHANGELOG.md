# Changelog

The version scheme follows the other IFDD repositories: `YY.MM.N`.

## [26.11.3] - 2026-11-21

First release, in the release round of keycloak, proxy and lab 26.11.3.

- lab (JupyterHub, `ifdd-de/lab`) as an optional service: only when the
  description lists it. `compile` derives its host, the Keycloak client
  `ifdd-lab` and `ifdd_proxy_extra_networks` (its internal network `lab-net`),
  all empty without lab; overrides are now visible to the derived values.
  `create` copies the client secret into `lab/secrets/oidc_client_secret`
  (new, 0600) and the local CA into `lab/certs/`; `up` creates `lab-net` as an
  internal network and starts lab last; `down --purge` keeps the volume
  `dind-data` (the homes of the users). `demo load` puts `ifdd-demo` into the
  lab users group. Integration tests: login and groups of the hub, the notebook
  helper reads with the user's rights (403 without the context pattern), the
  services are reachable from a user server, the internet is not.
- `config/demo`: keycloak, proxy and lab at v26.11.3; a description whose
  `templates.ref` has no tag of lab needs a reference of lab under
  `templates.refs` for `config check`, also without lab.
- CI: GitHub Actions pinned to commit SHAs (version as comment),
  `persist-credentials: false` on every checkout.
- `ifdd-meta update [--check] [--only …]`: later template changes into the
  existing instances with `cruft update`, one commit per instance; the result
  equals a fresh `create` at the new references (shared values from the
  variables file, changed defaults the template owns such as `ifdd_ui_version`,
  the skip list of `create` kept against a growing list in cruft 2.16.0, new
  `*.dist` defaults into `.env` where the old default is still set). Stops at
  the first conflict and before anything if an instance has uncommitted changes
  or no Git repository.
- CI job `update (demo)`: create at v26.11.1, update to the demo's references,
  compare with a fresh create (`tools/ci/compare_instances.py`).

- Local test installation on macOS with Colima: guide `docs/local-macos-colima.md`.
- `create` makes every instance a local Git repository (one commit, no remote)
  for later `cruft update`; Git name and e-mail come from the configuration
  only. It stops, without a repository, if the template's `.gitignore` would let
  a secret-like file (`.env`, `secrets/*`, keys) into Git.
- `hosts.txt` is a marked block for `/etc/hosts` (`# BEGIN/END ifdd-meta <name>`)
  for short names and for `tls: local-ca`, also with a host suffix such as
  `demo.test`; DNS records only for public names.
- `ifdd-meta local-setup [--remove]`: prints the `sudo` commands for the
  `/etc/hosts` block and for trusting the local CA (macOS System keychain,
  Debian/Ubuntu `update-ca-certificates`), and the commands that undo them
  (the keychain entry is named by its SHA-256 fingerprint); runs nothing. The integration run executes the printed commands.
- `ifdd-meta demo load`: demo user `ifdd-demo` (created, or password set and
  missing groups added) and the ct pipelines `generate-energy-grid` and
  `generate-time-series`; the password comes from `IFDD_META_DEMO_PASSWORD`, a
  new one is written into `.env` (0600) and never shown. The integration run
  loads the demo data twice.
- `ifdd-meta up` first checks with a throwaway container of the Traefik image
  that the Docker daemon sees the work directory; a daemon in a VM (Colima)
  sees only shared directories and would mount empty directories instead.
- The demo user gets the realm role of a Grafana viewer (`ifdd_ts_role_viewer`)
  with ts; a browser test signs in to Grafana as the demo user.
- Browser tests outside Linux use the system trust store and are skipped with a
  hint if Chromium does not trust the local CA; a new test signs in as the demo
  user.
- The local Git server for wf runs in offline mode and without Gitea's weekly
  version check (`cron.update_checker`, on by default); an integration test
  reads its `app.ini`.

- Browser test of the ui without a read role: waits for the ui's hint
  (`role="status"`, ui 26.11.2) instead of a quiet network, then checks that
  no aspect is offered; the reader must not see the hint.
- The admin API helper logs in again once when its token has expired (401);
  a test session longer than the short-lived master token failed while
  deleting the test client (found by a counter-check run at v26.11.1).
- Templates at release 26.11.2 (keycloak, proxy, be, ui, ts-api, wf); the
  others stay at 26.11.1. The description takes `templates.refs` (service: ref)
  for templates without the tag of `templates.ref`; `compile` writes it into
  `ifdd-meta.yaml`, and both workflows use the reference of each template.
- Without wf, `compile` leaves `ifdd_keycloak_proxy_gate_hosts` empty: no host
  behind the login gate (keycloak 26.11.2 accepts that; 26.11.1 refused it).
- Design of the tool (`docs/design.md`).
- Package `ifdd-meta` with the command `ifdd-meta` (typer).
- Settings from the command line, `IFDD_META_*` environment variables, `.env`,
  `ifdd-meta.yaml` and built-in defaults, in this order; `ifdd-meta config show`
  lists every value with its source.
- Unit tests and the workflow `test.yml` (ruff, pytest on Python 3.11 to 3.14).
- `ifdd-meta compile`: writes `cookiecutter_all.yaml`, `ifdd-meta.yaml`,
  `.env.example` and `hosts.txt` from a short description; values not derived
  come from `src/ifdd_meta/data/defaults.yaml` (all template variables of
  v26.11.1 except the versions); `--check` compares only.
- `ifdd-meta config check`: the variables file against the `cookiecutter.json`
  of every template at the chosen reference.
- `config/demo/`: description of the public demo and its generated files.
- `ifdd-meta create [--only …] [--verbose]`: instances with `cruft create`
  and the skip lists of the templates, optional wheels, local CA, client
  secrets from `keycloak/.env` (taken over from `copy_secrets.py`); a log per
  run in `<workdir>/logs/`.
- Setting `tls` (`local-ca`, `letsencrypt`, `own`); `compile` writes it.
- `build`, `up`, `down [--purge]`, `status`, `logs --output DIR`; `down --purge`
  keeps the ACME state of the proxy, `create` restores it.
- `create`: `IFDD_META_WF_GIT_TOKEN` into wf as `SECRET_GIT_TOKEN`.
- Local Git server for the flows of wf (`wf_git: local`, the default of a
  description with wf): Gitea (`gitea/gitea:1.27.3-rootless`, SQLite, no SSH,
  no registration, sign-in required) behind the proxy under the host `git`.
  `compile` adds the host to the internal hosts, `hosts.txt` and
  `ifdd_wf_git_url`; `create` writes its Compose project; `up` starts it before
  wf and creates user, repository (first commit on the branch) and token
  (scopes `write:repository,write:user`; into `wf/secrets/kestra.env`), never
  showing the token. `wf_git: external`
  keeps `IFDD_META_WF_GIT_TOKEN` and the overrides.
- Integration run: wf starts too; host names below `ci.test`; checks of
  `https://wf.ci.test/` (login gate) and `https://git.ci.test/api/healthz`; on
  failure the error lines of the containers and the Kestra logs of the Git
  flows, with secrets masked (`tools/ci/show_errors.py`, `kestra_logs.py`).
- `compile` warns about short host names with wf: Java clients send no SNI for
  names without a dot, and the production proxy refuses such handshakes.
- Workflow `integration.yml`: create, build and start an installation with the
  real images on a GitHub runner (wf not started yet), check every service
  through the proxy, keep logs and measurements; `tools/ci/scrub_secrets.py`
  removes artefacts that contain a generated secret.
- `compile`: `ifdd_keycloak_proxy_gate_hosts` always names the wf host, because
  the keycloak hook of v26.11.1 rejects an empty value.
- `ifdd-meta test [-k] [-x] [-v] [--junit FILE]`: integration tests in the
  package (`src/ifdd_meta/integration/`, pytest without assertion rewriting):
  Keycloak, be, ts-api, schema/type/unit, proxy headers and dashboard, Grafana
  data sources, ct pipelines, Git round trip of wf. Setting `tests`
  (`read` by default, `compile` writes it; `write` adds test data and a
  temporary service client with the pattern `ops.example.ifdd.de`).
- Browser tests (Playwright, extra `browser`): logins through Keycloak into ui,
  Grafana and wf with two temporary users, each check with its counter-check;
  `ifdd-meta test --screenshots DIR` keeps screenshots of failures.
- `ifdd-meta keycloak add-user`: user with a generated password (new 0600
  file), groups, realm and client roles, `--temporary`.
- `ifdd-meta keycloak add-client`: service client with client roles, scope
  mappings and default scopes through the admin API; secret into a new 0600 file.
- pytest is a runtime dependency; the compose file of the Git server is package
  data.
- Integration run: `ifdd-meta test` with `IFDD_META_TESTS=write`, JUnit report as
  artefact.
- Workflow `test.yml`, job `config (demo)`: generated files up to date,
  `config check` against GitHub, every template renders with the generated
  variables.
