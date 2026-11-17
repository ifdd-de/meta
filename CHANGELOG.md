# Changelog

The version scheme follows the other IFDD repositories: `YY.MM.N`. The first
release follows the first successful integration run.

## [Unreleased]

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
- `compile`: `ifdd_keycloak_proxy_gate_hosts` always names the wf host, because
  the keycloak hook of v26.11.1 rejects an empty value.
- Workflow `test.yml`, job `config (demo)`: generated files up to date,
  `config check` against GitHub, every template renders with the generated
  variables.
