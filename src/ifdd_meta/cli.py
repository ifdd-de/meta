"""Command line of ifdd-meta."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Annotated

import typer

from . import (
    __version__,
    check,
    compiler,
    copysecret,
    create,
    demo,
    keycloak,
    localsetup,
    services,
    testrun,
)
from .copysecret import SecretError
from .description import DescriptionError
from .description import load as load_description
from .runner import Run, StepError
from .settings import ENVIRONMENTS, SERVICES, SettingsError, load
from .yamltext import LayoutError

app = typer.Typer(
    help="Build, run and test a complete IFDD installation from the ifdd-de templates.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
config_app = typer.Typer(help="Inspect the configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")
keycloak_app = typer.Typer(
    help="Change the realm through the Keycloak admin API.", no_args_is_help=True
)
app.add_typer(keycloak_app, name="keycloak")
demo_app = typer.Typer(help="Demo user and demo data.", no_args_is_help=True)
app.add_typer(demo_app, name="demo")


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def options(
    ctx: typer.Context,
    settings: Annotated[
        Path | None,
        typer.Option(
            "--settings",
            "-s",
            help="Settings file (default: $IFDD_META_SETTINGS, else ./ifdd-meta.yaml).",
        ),
    ] = None,
    ref: Annotated[
        str | None, typer.Option(help="Git reference (tag, branch, commit) of all templates.")
    ] = None,
    source: Annotated[
        str | None, typer.Option(help="URL base or directory that holds the templates.")
    ] = None,
    workdir: Annotated[
        Path | None, typer.Option(help="Directory in which the instances are created.")
    ] = None,
    environment: Annotated[
        str | None, typer.Option(help=f"One of: {', '.join(ENVIRONMENTS)}.")
    ] = None,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show the version."),
    ] = False,
) -> None:
    ctx.obj = {
        "settings_file": settings,
        "cli": {
            "templates.ref": ref,
            "templates.source": source,
            "workdir": workdir,
            "environment": environment,
        },
    }


def _settings(ctx: typer.Context):
    try:
        return load(
            settings_file=ctx.obj["settings_file"],
            cli=ctx.obj["cli"],
            environ=os.environ,
        )
    except SettingsError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """Show the effective settings and where each value comes from."""
    settings = _settings(ctx)
    typer.echo(f"settings file: {settings.file or '(none, defaults only)'}")
    rows = settings.rows()
    width_name = max(len(name) for name, _, _ in rows)
    width_value = max(len(value) for _, value, _ in rows)
    for name, value, source in rows:
        typer.echo(f"{name:<{width_name}}  {value:<{width_value}}  [{source}]")


@config_app.command("check")
def config_check(ctx: typer.Context) -> None:
    """Check the variables file against the templates (read-only)."""
    settings = _settings(ctx)
    typer.echo(f"variables file: {settings['variables']}")
    report = check.run(settings)
    for finding in report.findings:
        typer.echo(f"error: {finding.message}", err=True)
    if report.findings:
        typer.echo(f"config check: {len(report.findings)} error(s)", err=True)
        raise typer.Exit(1)
    refs = sorted({settings.ref(service) for service in SERVICES})
    typer.echo(
        f"config check: ok ({report.variables} variables, {report.templates} templates "
        f"at {', '.join(refs)})"
    )


@app.command("compile")
def compile_command(
    description: Annotated[Path, typer.Argument(help="Description of the installation (YAML).")],
    output: Annotated[
        Path | None,
        typer.Option(help="Directory of the generated files (default: next to the description)."),
    ] = None,
    check_only: Annotated[
        bool,
        typer.Option("--check", help="Only compare the files on disk with a fresh compilation."),
    ] = False,
) -> None:
    """Generate the variables file and the other files from a description."""
    try:
        result = compiler.compile_description(load_description(description))
    except (DescriptionError, compiler.CompileError, LayoutError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None
    directory = output or description.parent
    for warning in result.warnings:
        typer.echo(f"warning: {warning}", err=True)
    if check_only:
        stale = compiler.differences(result, directory)
        for name in stale:
            typer.echo(f"error: {directory / name} is missing or out of date", err=True)
        if stale:
            typer.echo(f"run: ifdd-meta compile {description}", err=True)
            raise typer.Exit(1)
        typer.echo(f"compile --check: {len(result.files)} files up to date in {directory}")
        return
    for path in compiler.write(result, directory):
        typer.echo(f"wrote {path}")


def _only(value: str | None) -> list[str] | None:
    return [item.strip() for item in value.split(",") if item.strip()] if value else None


@app.command("create")
def create_command(
    ctx: typer.Context,
    only: Annotated[
        str | None, typer.Option(help="Comma-separated services; default: all of the settings.")
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show the log.")] = False,
) -> None:
    """Create the instances with cruft, the local CA and the client secrets."""
    settings = _settings(ctx)
    run = Run(settings["workdir"] / "logs", "create", typer.echo, verbose)
    try:
        state = create.create(settings, run, _only(only))
    except (create.CreateError, StepError, SecretError, OSError) as error:
        typer.echo(f"error: {error}", err=True)
        typer.echo(f"log: {run.log}", err=True)
        raise typer.Exit(1) from None
    run.overview(f"{len(state)} instances in {settings['workdir']}; log: {run.log}")


OnlyOption = Annotated[
    str | None, typer.Option("--only", help="Comma-separated services; default: all.")
]
VerboseOption = Annotated[bool, typer.Option("--verbose", "-v", help="Show the log.")]


def _run_step(ctx: typer.Context, name: str, verbose: bool, step) -> None:
    settings = _settings(ctx)
    run = Run(settings["workdir"] / "logs", name, typer.echo, verbose)
    try:
        step(settings, run)
    except (create.CreateError, StepError, SecretError, OSError) as error:
        typer.echo(f"error: {error}", err=True)
        typer.echo(f"log: {run.log}", err=True)
        raise typer.Exit(1) from None
    run.overview(f"{name}: done; log: {run.log}")


@app.command("build")
def build_command(ctx: typer.Context, only: OnlyOption = None, verbose: VerboseOption = False):
    """Build the images and fetch what the services need (Grafana plugins, upstream images)."""
    _run_step(ctx, "build", verbose, lambda s, r: services.build(s, r, _only(only)))


@app.command("up")
def up_command(ctx: typer.Context, only: OnlyOption = None, verbose: VerboseOption = False):
    """Start the services in order and wait until they are healthy."""
    _run_step(ctx, "up", verbose, lambda s, r: services.up(s, r, _only(only)))


@app.command("down")
def down_command(
    ctx: typer.Context,
    only: OnlyOption = None,
    purge: Annotated[
        bool,
        typer.Option(
            help="Also delete volumes, networks and the instances (keeps the persist paths)."
        ),
    ] = False,
    verbose: VerboseOption = False,
):
    """Stop the services."""
    _run_step(ctx, "down", verbose, lambda s, r: services.down(s, r, _only(only), purge))


@app.command("local-setup")
def local_setup_command(
    ctx: typer.Context,
    remove: Annotated[
        bool, typer.Option(help="The commands that undo it (before down --purge).")
    ] = False,
) -> None:
    """Print the commands (sudo) for /etc/hosts and the local CA; runs nothing."""
    settings = _settings(ctx)
    try:
        lines = localsetup.commands(settings, remove=remove)
    except (create.CreateError, OSError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None
    for line in lines:
        typer.echo(line)


@app.command("status")
def status_command(ctx: typer.Context, only: OnlyOption = None) -> None:
    """State and health of every container; exit 1 if one is not running or not healthy."""
    settings = _settings(ctx)
    try:
        rows = services.status(settings, _only(only))
    except create.CreateError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    for row in rows:
        typer.echo(
            "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row[:3])) + f"  {row[3]}"
        )
    unhealthy = [row for row in rows if row[2] != "running" or row[3] not in ("", "healthy")]
    if unhealthy:
        raise typer.Exit(1)


@app.command("logs")
def logs_command(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option(help="Directory for one log file per service.")],
) -> None:
    """Write the container logs of every service into a directory."""
    settings = _settings(ctx)
    run = Run(settings["workdir"] / "logs", "logs", typer.echo)
    try:
        written = services.logs(settings, run, output)
    except create.CreateError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{len(written)} log files in {output}")


@app.command("test")
def test_command(
    ctx: typer.Context,
    junit: Annotated[
        Path | None, typer.Option(help="Write a JUnit XML report to this file.")
    ] = None,
    keyword: Annotated[
        str | None, typer.Option("-k", help="Only the tests that match this pytest expression.")
    ] = None,
    exitfirst: Annotated[bool, typer.Option("-x", help="Stop at the first failure.")] = False,
    screenshots: Annotated[
        Path | None, typer.Option(help="Directory for screenshots of failed browser tests.")
    ] = None,
    verbose: VerboseOption = False,
) -> None:
    """Test the running installation (pytest). Writes data only with the setting tests: write."""
    settings = _settings(ctx)
    try:
        code = testrun.run(
            settings, ctx.obj["cli"], screenshots=screenshots, junit=junit, keyword=keyword,
            exitfirst=exitfirst, verbose=verbose,
        )  # fmt: skip
    except ValueError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None
    raise typer.Exit(code)


def _roles(values: list[str] | None) -> dict[str, list[str]]:
    """CLIENT:ROLE values, grouped by client."""
    roles: dict[str, list[str]] = {}
    for value in values or []:
        owner, separator, role = value.partition(":")
        if not (separator and owner and role):
            raise typer.BadParameter(f"expected CLIENT:ROLE, got {value!r}", param_hint="--role")
        roles.setdefault(owner, []).append(role)
    return roles


def _new_file(path: Path) -> None:
    if path.exists():
        typer.echo(f"error: {path} exists already", err=True)
        raise typer.Exit(1)


RoleOption = Annotated[
    list[str] | None,
    typer.Option(help="CLIENT:ROLE, e.g. ifdd:ifdd_nodes_ro or ifdd:ops.example.ifdd.de."),
]
CreateRolesOption = Annotated[
    bool, typer.Option(help="Create missing client roles (e.g. a new context pattern).")
]


@keycloak_app.command("add-client")
def keycloak_add_client(
    ctx: typer.Context,
    client_id: Annotated[str, typer.Argument(help="Id of the new service client.")],
    secret_file: Annotated[
        Path, typer.Option(help="New file (mode 0600) for the client secret; it is never shown.")
    ],
    role: RoleOption = None,
    default_scope: Annotated[
        list[str] | None, typer.Option(help="Default client scope, e.g. ifdd_nodes_ro.")
    ] = None,
    create_roles: CreateRolesOption = False,
) -> None:
    """Add a service client (client credentials) with client roles and scopes."""
    settings = _settings(ctx)
    roles = _roles(role)
    _new_file(secret_file)
    try:
        admin = keycloak.Admin.from_settings(settings)
        client = admin.add_service_client(
            client_id, roles, default_scopes=default_scope or [], create_roles=create_roles
        )
        try:
            copysecret.write_file(secret_file, admin.client_secret(client), new_mode=0o600)
        except (SecretError, OSError):
            admin.delete_client(client)
            raise
    except (keycloak.KeycloakError, SecretError, OSError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None
    for created_owner, created_role in admin.created_roles:
        typer.echo(f"created role {created_role} of {created_owner}")
    typer.echo(f"created client {client_id}; secret in {secret_file}")


@keycloak_app.command("add-user")
def keycloak_add_user(
    ctx: typer.Context,
    username: Annotated[str, typer.Argument(help="Name of the new user.")],
    password_file: Annotated[
        Path,
        typer.Option(help="New file (mode 0600) for the generated password; it is never shown."),
    ],
    group: Annotated[
        list[str] | None, typer.Option(help="Group, e.g. ifdd_nodes_readers or ifdd_wf_admins.")
    ] = None,
    realm_role: Annotated[
        list[str] | None, typer.Option(help="Realm role, e.g. readonly (Grafana Viewer).")
    ] = None,
    role: RoleOption = None,
    create_roles: CreateRolesOption = False,
    temporary: Annotated[
        bool, typer.Option(help="The user has to change the password at the first login.")
    ] = False,
) -> None:
    """Add a user with a generated password, groups, realm and client roles."""
    settings = _settings(ctx)
    roles = _roles(role)
    _new_file(password_file)
    password = secrets.token_urlsafe(18)
    try:
        admin = keycloak.Admin.from_settings(settings)
        copysecret.write_file(password_file, password, new_mode=0o600)
        try:
            admin.add_user(
                username, password, groups=group or [], realm_roles=realm_role or [],
                roles=roles, create_roles=create_roles, temporary=temporary,
            )  # fmt: skip
        except keycloak.KeycloakError:
            password_file.unlink()
            raise
    except (keycloak.KeycloakError, SecretError, OSError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None
    for created_owner, created_role in admin.created_roles:
        typer.echo(f"created role {created_role} of {created_owner}")
    typer.echo(f"created user {username}; password in {password_file}")


@demo_app.command("load")
def demo_load(ctx: typer.Context, verbose: VerboseOption = False) -> None:
    """Demo user ifdd-demo and the fake data of the ct pipelines; the services must run."""
    settings = _settings(ctx)
    run = Run(settings["workdir"] / "logs", "demo-load", typer.echo, verbose)
    try:
        demo.load(settings, run)
    except (create.CreateError, StepError, SecretError, keycloak.KeycloakError, OSError) as error:
        typer.echo(f"error: {error}", err=True)
        typer.echo(f"log: {run.log}", err=True)
        raise typer.Exit(1) from None
    run.overview(f"demo load: done; log: {run.log}")


def main() -> None:
    app()
