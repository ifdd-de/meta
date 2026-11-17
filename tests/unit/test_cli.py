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
