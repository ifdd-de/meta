"""keycloak secrets: the client secrets of a realm for the server that uses it."""

import json

import pytest
from typer.testing import CliRunner

from ifdd_meta import keycloak
from ifdd_meta.cli import app
from ifdd_meta.create import STATE_DIR, STATE_FILE
from ifdd_meta.settings import load

ENV = """\
IFDD_KEYCLOAK_ADMIN_PASSWORD=admin-secret
IFDD_KEYCLOAK_CT_CLIENT_SECRET=ct-main
IFDD_KEYCLOAK_API_CLIENT_SECRET=api-main
IFDD_KEYCLOAK_USERADMIN_CLIENT_SECRET=never
IFDD_KEYCLOAK_CT_CLIENT_SECRET_STAGE_B=ct-b
IFDD_KEYCLOAK_TSINGEST_CLIENT_SECRET_STAGE_B=ingest-b
IFDD_KEYCLOAK_USERADMIN_CLIENT_SECRET_STAGE_B=never-b
"""


@pytest.fixture
def settings(tmp_path):
    (tmp_path / "vars.yaml").write_text(
        "default_context:\n  ifdd_keycloak_realm: realm-a\n  ifdd_keycloak_extra_realms: stage-b\n"
    )
    (tmp_path / "ifdd-meta.yaml").write_text("variables: vars.yaml\n")
    state = tmp_path / "work" / STATE_DIR
    state.mkdir(parents=True)
    (state / STATE_FILE).write_text(json.dumps({"keycloak": "keycloak"}))
    (tmp_path / "work" / "keycloak").mkdir()
    (tmp_path / "work" / "keycloak" / ".env").write_text(ENV)
    return load(cwd=tmp_path, environ={})


def test_secrets_of_the_own_realm(settings):
    lines = keycloak.export_secrets(settings, None)
    assert "IFDD_KEYCLOAK_CT_CLIENT_SECRET=ct-main" in lines
    assert "IFDD_KEYCLOAK_API_CLIENT_SECRET=api-main" in lines
    assert not any("USERADMIN" in line or "ADMIN_PASSWORD" in line for line in lines)


def test_secrets_of_a_further_realm_get_the_plain_names(settings):
    lines = keycloak.export_secrets(settings, "stage-b")
    assert sorted(lines) == [
        "IFDD_KEYCLOAK_CT_CLIENT_SECRET=ct-b",
        "IFDD_KEYCLOAK_TSINGEST_CLIENT_SECRET=ingest-b",
    ]


def test_unknown_realm(settings):
    with pytest.raises(keycloak.KeycloakError, match="not a realm of this Keycloak"):
        keycloak.export_secrets(settings, "stage-c")


def test_cli_writes_to_a_pipe_only(settings, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IFDD_META_SETTINGS", raising=False)
    result = CliRunner().invoke(app, ["keycloak", "secrets", "--realm", "stage-b"])
    assert result.exit_code == 0
    assert "IFDD_KEYCLOAK_CT_CLIENT_SECRET=ct-b\n" in result.stdout
    monkeypatch.setattr("ifdd_meta.cli._stdout_is_terminal", lambda: True)
    result = CliRunner().invoke(app, ["keycloak", "secrets"])
    assert result.exit_code == 1
    assert "ct-main" not in result.stdout


# add-user --realm: the admin API of another realm of the same Keycloak


def test_admin_of_a_further_realm(settings, monkeypatch):
    calls = []

    def from_instance(cls, keycloak_dir, realm, ca_file):
        calls.append((keycloak_dir.name, realm))
        return "admin"

    monkeypatch.setattr(keycloak.Admin, "from_instance", classmethod(from_instance))
    assert keycloak.Admin.from_settings(settings) == "admin"
    assert keycloak.Admin.from_settings(settings, "stage-b") == "admin"
    assert keycloak.Admin.from_settings(settings, "realm-a") == "admin"
    assert calls == [("keycloak", "realm-a"), ("keycloak", "stage-b"), ("keycloak", "realm-a")]
    with pytest.raises(
        keycloak.KeycloakError, match=r"'stage-c' is not a realm .*realm-a, stage-b"
    ):
        keycloak.Admin.from_settings(settings, "stage-c")


def test_realm_of_gives_the_secret_suffix(settings):
    assert keycloak.realm_of(settings, None) == ("realm-a", "")
    assert keycloak.realm_of(settings, "stage-b") == ("stage-b", "STAGE_B")


def test_grafana_roles_come_from_the_variables(tmp_path):
    (tmp_path / "vars.yaml").write_text(
        "default_context:\n  ifdd_ts_role_admin: boss\n  ifdd_ts_role_editor: writer\n"
        "  ifdd_ts_role_viewer: reader\n"
    )
    (tmp_path / "ifdd-meta.yaml").write_text("variables: vars.yaml\n")
    settings = load(cwd=tmp_path, environ={})
    assert [keycloak.grafana_role(settings, r) for r in ("admin", "editor", "viewer")] == [
        "boss",
        "writer",
        "reader",
    ]
    with pytest.raises(keycloak.KeycloakError, match="one of admin, editor, viewer"):
        keycloak.grafana_role(settings, "Admin")
