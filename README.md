# meta

Build, run and test a complete IFDD installation from the templates of the
GitHub organisation [`ifdd-de`](https://github.com/ifdd-de).

The command `ifdd-meta` creates the service instances with `cruft`, builds and
starts them in the right order, loads demo data and runs integration tests. The
same tool serves the CI of this repository, a demo server and the servers of an
operator. The design, with everything planned, is in
[`docs/design.md`](docs/design.md).

**Status:** in development. Available so far: settings and `config show`.

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
