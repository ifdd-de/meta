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
