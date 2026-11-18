"""Command line of ifdd-meta."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from . import __version__, check, compiler, create, services
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


def main() -> None:
    app()
