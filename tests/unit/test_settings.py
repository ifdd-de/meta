from pathlib import Path

import pytest

from ifdd_meta.settings import SERVICES, SettingsError, load


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_without_a_settings_file(tmp_path):
    settings = load(cwd=tmp_path, environ={})
    assert settings.file is None
    assert settings["templates.ref"] == "main"
    assert settings["services"] == SERVICES
    assert settings["variables"] == tmp_path / "cookiecutter_all.yaml"
    assert set(settings.sources.values()) == {"default"}
    assert settings["tests"] == "read"


def test_precedence_cli_environment_dotenv_file_default(tmp_path):
    """Each source overrides the ones below it."""
    write(
        tmp_path / "ifdd-meta.yaml",
        "templates:\n  ref: from-file\n  source: file-source\ninstallation: file-name\n"
        "workdir: file-work\n",
    )
    write(
        tmp_path / ".env",
        "IFDD_META_TEMPLATES_REF=from-dotenv\nIFDD_META_TEMPLATES_SOURCE=dotenv-source\n"
        "IFDD_META_WORKDIR=dotenv-work\n",
    )
    environ = {"IFDD_META_TEMPLATES_REF": "from-env", "IFDD_META_WORKDIR": "env-work"}
    settings = load(cwd=tmp_path, environ=environ, cli={"templates.ref": "from-cli"})

    assert (settings["templates.ref"], settings.sources["templates.ref"]) == (
        "from-cli",
        "command line",
    )
    assert (settings["workdir"], settings.sources["workdir"]) == (
        tmp_path / "env-work",
        "environment",
    )
    assert (settings["templates.source"], settings.sources["templates.source"]) == (
        "dotenv-source",
        ".env",
    )
    assert (settings["installation"], settings.sources["installation"]) == (
        "file-name",
        "ifdd-meta.yaml",
    )
    assert settings.sources["environment"] == "default"


def test_paths_in_the_file_are_relative_to_the_file(tmp_path):
    config = write(tmp_path / "config" / "ifdd-meta.yaml", "variables: vars.yaml\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    settings = load(settings_file=config, cwd=elsewhere, environ={})
    assert settings["variables"] == tmp_path / "config" / "vars.yaml"
    # the default is relative to the settings file as well
    assert settings["workdir"] == tmp_path / "config" / "work"


def test_paths_from_the_environment_are_relative_to_the_working_directory(tmp_path):
    config = write(tmp_path / "config" / "ifdd-meta.yaml", "")
    settings = load(settings_file=config, cwd=tmp_path, environ={"IFDD_META_WORKDIR": "w"})
    assert settings["workdir"] == tmp_path / "w"


def test_settings_file_from_the_environment(tmp_path):
    config = write(tmp_path / "a" / "x.yaml", "installation: from-env-file\n")
    settings = load(cwd=tmp_path, environ={"IFDD_META_SETTINGS": str(config)})
    assert settings.file == config
    assert settings["installation"] == "from-env-file"


def test_explicit_settings_file_must_exist(tmp_path):
    with pytest.raises(SettingsError, match="settings file not found"):
        load(settings_file=Path("missing.yaml"), cwd=tmp_path, environ={})


def test_unknown_key_in_the_file_is_an_error(tmp_path):
    write(tmp_path / "ifdd-meta.yaml", "installation: x\nservice: [be]\n")
    with pytest.raises(SettingsError, match="unknown settings: service"):
        load(cwd=tmp_path, environ={})


def test_services_are_validated_and_ordered_like_the_catalogue(tmp_path):
    write(tmp_path / "ifdd-meta.yaml", "services: [ui, proxy, be]\n")
    assert load(cwd=tmp_path, environ={})["services"] == ("proxy", "be", "ui")
    with pytest.raises(SettingsError, match="services .*unknown: nope"):
        load(cwd=tmp_path, environ={"IFDD_META_SERVICES": "proxy,nope"})
    with pytest.raises(SettingsError, match="must not repeat"):
        load(cwd=tmp_path, environ={"IFDD_META_SERVICES": "be,be"})


def test_services_from_the_environment_are_comma_separated(tmp_path):
    settings = load(cwd=tmp_path, environ={"IFDD_META_SERVICES": "keycloak, proxy"})
    assert settings["services"] == ("proxy", "keycloak")


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({"IFDD_META_ENVIRONMENT": "staging"}, "expected one of production, development"),
        ({"IFDD_META_WAIT_TIMEOUT": "soon"}, "expected a whole number"),
        ({"IFDD_META_WAIT_TIMEOUT": "0"}, "expected a positive whole number"),
        ({"IFDD_META_TEMPLATES_REF": " "}, "expected a non-empty string"),
        ({"IFDD_META_TESTS": "all"}, "expected one of read, write"),
    ],
)
def test_invalid_values_name_field_and_source(tmp_path, environ, message):
    with pytest.raises(SettingsError, match=f"\\(environment\\): {message}"):
        load(cwd=tmp_path, environ=environ)


def test_per_template_refs(tmp_path):
    write(tmp_path / "ifdd-meta.yaml", "templates:\n  ref: v26.11.1\n  refs:\n    be: main\n")
    settings = load(cwd=tmp_path, environ={})
    assert settings.ref("be") == "main"
    assert settings.ref("ui") == "v26.11.1"


def test_unknown_service_in_refs_and_unknown_wheel(tmp_path):
    write(tmp_path / "ifdd-meta.yaml", "templates:\n  refs:\n    frontend: main\n")
    with pytest.raises(SettingsError, match="unknown service 'frontend'"):
        load(cwd=tmp_path, environ={})
    write(tmp_path / "ifdd-meta.yaml", "wheels:\n  ui-core: dist\n")
    with pytest.raises(SettingsError, match="unknown package 'ui-core'"):
        load(cwd=tmp_path, environ={})


def test_wheel_paths_are_relative_to_the_file(tmp_path):
    write(tmp_path / "c" / "ifdd-meta.yaml", "wheels:\n  be-core: ../be-core/dist\n")
    settings = load(settings_file=tmp_path / "c" / "ifdd-meta.yaml", cwd=tmp_path, environ={})
    assert settings["wheels"] == {"be-core": tmp_path / "c" / "../be-core/dist"}


def test_invalid_yaml_and_wrong_top_level(tmp_path):
    write(tmp_path / "ifdd-meta.yaml", "a: [\n")
    with pytest.raises(SettingsError, match="not valid YAML"):
        load(cwd=tmp_path, environ={})
    write(tmp_path / "ifdd-meta.yaml", "- a\n")
    with pytest.raises(SettingsError, match="mapping at the top level"):
        load(cwd=tmp_path, environ={})
