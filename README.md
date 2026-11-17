# meta

Build, run and test a complete IFDD installation from the templates of the
GitHub organisation [`ifdd-de`](https://github.com/ifdd-de).

The command `ifdd-meta` creates the service instances with `cruft`, builds and
starts them in the right order, loads demo data and runs integration tests. The
same tool serves the CI of this repository, a demo server and the servers of an
operator. The design, with everything planned, is in
[`docs/design.md`](docs/design.md).

**Status:** in development. Available so far: `compile`, `config show`, `config check`.

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
The only example is the description of the public demo,
[`config/demo/installation.yaml`](config/demo/installation.yaml); its host names
lie below `example.ifdd.de`, which is reserved for examples.

`config check` compares the variables file with the `cookiecutter.json` of
every template at the chosen reference: a missing variable, one that no template
defines, a version, a secret-like name, a malformed choice or Jinja that refers
to another variable is an error.

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
