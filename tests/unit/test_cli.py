from typer.testing import CliRunner

from ifdd_meta.cli import app

runner = CliRunner()


def test_help_lists_the_config_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "config" in result.output


def test_config_show_prints_values_and_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IFDD_META_SETTINGS", raising=False)
    (tmp_path / "ifdd-meta.yaml").write_text("templates:\n  ref: v26.11.1\n")
    result = runner.invoke(app, ["--workdir", "w", "config", "show"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == f"settings file: {tmp_path / 'ifdd-meta.yaml'}"
    assert any(
        line.startswith("templates.ref") and "v26.11.1" in line and "[ifdd-meta.yaml]" in line
        for line in lines
    )
    assert any(line.startswith("workdir") and "[command line]" in line for line in lines)


def test_invalid_settings_exit_with_1(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IFDD_META_SETTINGS", raising=False)
    result = runner.invoke(app, ["--environment", "staging", "config", "show"])
    assert result.exit_code == 1
    assert "error: environment (command line)" in result.output


def test_compile_writes_and_check_compares(tmp_path):
    description = tmp_path / "installation.yaml"
    description.write_text("installation: x\n")
    result = runner.invoke(app, ["compile", str(description)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "cookiecutter_all.yaml").is_file()
    assert runner.invoke(app, ["compile", "--check", str(description)]).exit_code == 0
    (tmp_path / "hosts.txt").write_text("edited by hand\n")
    result = runner.invoke(app, ["compile", "--check", str(description)])
    assert result.exit_code == 1
    assert "hosts.txt is missing or out of date" in result.output


def test_compile_reports_an_invalid_description(tmp_path):
    description = tmp_path / "installation.yaml"
    description.write_text("installation: x\ntls: acme\n")
    result = runner.invoke(app, ["compile", str(description)])
    assert result.exit_code == 1
    assert "error:" in result.output


def test_unknown_option_is_a_usage_error():
    result = runner.invoke(app, ["--nope", "config", "show"])
    assert result.exit_code == 2


def test_test_command_needs_a_settings_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IFDD_META_SETTINGS", raising=False)
    result = runner.invoke(app, ["test"])
    assert result.exit_code == 1
    assert "need a settings file" in result.output


class FakeAdmin:
    def __init__(self):
        self.added = []
        self.deleted = []
        self.created_roles = [("ifdd", "ops.example.ifdd.de")]

    def add_service_client(self, client_id, roles, default_scopes, create_roles):
        self.added.append((client_id, roles, default_scopes, create_roles))
        return "uuid-1"

    def client_secret(self, uuid):
        return "generated-secret"

    def add_user(self, username, password, **kwargs):
        if username == "taken":
            from ifdd_meta.keycloak import KeycloakError

            raise KeycloakError("user taken exists already")
        self.added.append((username, password, kwargs))
        return "user-1"

    def delete_client(self, uuid):
        self.deleted.append(uuid)


def add_client(tmp_path, monkeypatch, *args, command="add-client"):
    from ifdd_meta import keycloak

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IFDD_META_SETTINGS", raising=False)
    fake = FakeAdmin()
    monkeypatch.setattr(keycloak.Admin, "from_settings", classmethod(lambda cls, s: fake))
    return fake, runner.invoke(app, ["keycloak", command, *args])


def test_add_client_writes_the_secret_only_into_the_file(tmp_path, monkeypatch):
    fake, result = add_client(
        tmp_path, monkeypatch, "probe", "--secret-file", "probe.secret",
        "--role", "ifdd:ifdd_nodes_ro", "--role", "ifdd:ops.example.ifdd.de",
        "--role", "ifdd-ts-api:ifdd_ts_ro", "--default-scope", "ifdd_nodes_ro", "--create-roles",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert fake.added == [
        (
            "probe",
            {"ifdd": ["ifdd_nodes_ro", "ops.example.ifdd.de"], "ifdd-ts-api": ["ifdd_ts_ro"]},
            ["ifdd_nodes_ro"],
            True,
        )
    ]
    secret = tmp_path / "probe.secret"
    assert secret.read_text() == "generated-secret"
    assert secret.stat().st_mode & 0o777 == 0o600
    assert "generated-secret" not in result.output
    assert "created role ops.example.ifdd.de of ifdd" in result.output


def test_add_client_refuses_an_existing_secret_file_and_bad_roles(tmp_path, monkeypatch):
    (tmp_path / "probe.secret").write_text("old")
    fake, result = add_client(tmp_path, monkeypatch, "probe", "--secret-file", "probe.secret")
    assert result.exit_code == 1
    assert "exists already" in result.output
    assert fake.added == []
    fake, result = add_client(
        tmp_path, monkeypatch, "probe", "--secret-file", "new.secret", "--role", "ifdd_nodes_ro"
    )
    assert result.exit_code == 2
    assert fake.added == []


def test_add_client_removes_the_client_if_the_secret_cannot_be_written(tmp_path, monkeypatch):
    fake, result = add_client(tmp_path, monkeypatch, "probe", "--secret-file", "no/dir/x.secret")
    assert result.exit_code == 1
    assert "target directory missing" in result.output
    assert fake.deleted == ["uuid-1"]


def test_add_user_writes_a_generated_password_only_into_the_file(tmp_path, monkeypatch):
    fake, result = add_client(
        tmp_path, monkeypatch, "reader", "--password-file", "reader.password",
        "--group", "ifdd_nodes_readers", "--realm-role", "readonly",
        "--role", "ifdd:ops.example.ifdd.de", "--create-roles", "--temporary",
        command="add-user",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    [(username, password, options)] = fake.added
    assert username == "reader" and len(password) >= 20
    assert options == {
        "groups": ["ifdd_nodes_readers"],
        "realm_roles": ["readonly"],
        "roles": {"ifdd": ["ops.example.ifdd.de"]},
        "create_roles": True,
        "temporary": True,
    }
    stored = tmp_path / "reader.password"
    assert stored.read_text() == password
    assert stored.stat().st_mode & 0o777 == 0o600
    assert password not in result.output


def test_add_user_removes_the_password_file_if_keycloak_refuses(tmp_path, monkeypatch):
    fake, result = add_client(
        tmp_path, monkeypatch, "taken", "--password-file", "taken.password", command="add-user"
    )
    assert result.exit_code == 1
    assert "exists already" in result.output
    assert not (tmp_path / "taken.password").exists()


def test_local_setup_prints_commands_and_reports_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IFDD_META_SETTINGS", raising=False)
    description = tmp_path / "installation.yaml"
    description.write_text("installation: nb\nhosts:\n  suffix: demo.test\n")
    assert runner.invoke(app, ["compile", str(description)]).exit_code == 0
    result = runner.invoke(app, ["local-setup"])
    assert result.exit_code == 0, result.output
    assert f"< {tmp_path / 'hosts.txt'} > /dev/null" in result.output
    assert "No proxy instance yet" in result.output
    (tmp_path / "hosts.txt").unlink()
    result = runner.invoke(app, ["local-setup", "--remove"])
    assert result.exit_code == 1 and "hosts.txt missing" in result.output


def test_demo_load_needs_instances(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IFDD_META_SETTINGS", raising=False)
    (tmp_path / "ifdd-meta.yaml").write_text("variables: vars.yaml\n")
    result = runner.invoke(app, ["demo", "load"])
    assert result.exit_code == 1
    assert "no keycloak instance" in result.output


def test_update_needs_instances(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IFDD_META_SETTINGS", raising=False)
    (tmp_path / "vars.yaml").write_text("default_context: {}\n")
    (tmp_path / "ifdd-meta.yaml").write_text("variables: vars.yaml\n")
    for argv in (["update"], ["update", "--check"]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 1
        assert "no instances" in result.output
