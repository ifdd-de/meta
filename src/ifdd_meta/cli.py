"""Command line of ifdd-meta."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .settings import ENVIRONMENTS, SettingsError, load

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


def main() -> None:
    app()
