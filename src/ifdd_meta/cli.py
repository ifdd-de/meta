"""Command line of ifdd-meta."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from . import __version__, check, compiler
from .description import DescriptionError
from .description import load as load_description
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


def main() -> None:
    app()
