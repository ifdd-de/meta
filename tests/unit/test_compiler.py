from pathlib import Path

import pytest
import yaml

from ifdd_meta import compiler, settings
from ifdd_meta.description import load

DEMO = Path(__file__).parents[2] / "config" / "demo"


def describe(tmp_path: Path, text: str):
    path = tmp_path / "installation.yaml"
    path.write_text(text, encoding="utf-8")
    return load(path)


def defaults() -> dict:
    return yaml.safe_load(compiler.defaults_text())["default_context"]


def test_short_names_reproduce_the_defaults(tmp_path):
    """The defaults are the values for short host names and a local CA."""
    result = compiler.compile_description(describe(tmp_path, "installation: ci\n"))
    assert result.values == defaults()


def test_public_names_with_lets_encrypt(tmp_path):
    result = compiler.compile_description(
        describe(
            tmp_path,
            "installation: demo\nhosts:\n  suffix: a.example.org\ntls: letsencrypt\n"
            "contact: ops@example.org\nrealm: r1\nextra_realms: [r2, r3]\n",
        )
    )
    v = result.values
    assert v["ifdd_oidc_realm"] == "https://id.a.example.org/realms/r1"
    assert v["ifdd_proxy_oidc_issuer"] == v["ifdd_ts_oidc_issuer"] == v["ifdd_oidc_realm"]
    assert v["ifdd_oidc_jwks_url"].endswith("/realms/r1/protocol/openid-connect/certs")
    assert v["ifdd_keycloak_host"] == "id.a.example.org"
    assert v["ifdd_keycloak_realm"] == "r1"
    assert v["ifdd_keycloak_extra_realms"] == "r2, r3"
    assert v["ifdd_be_url"] == v["ifdd_ts_be_url"] == "https://rest.a.example.org"
    assert v["ifdd_type_url"] == "https://type.a.example.org/v1"
    assert v["ifdd_ts_api_url"] == "https://ts.a.example.org/tsapi"
    assert v["ifdd_proxy_enable_letsencrypt"] == "y"
    assert v["ifdd_proxy_letsencrypt_email"] == "ops@example.org"
    assert v["ifdd_proxy_oauth2_proxy_ca_file"] == ""
    assert v["ifdd_keycloak_proxy_gate_hosts"] == "wf.a.example.org"
    # whole values, not substrings: exact policies and host lists
    assert v["ifdd_proxy_csp_api_docs"] == (
        "default-src 'self'; frame-ancestors 'none'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "connect-src 'self' https://id.a.example.org; worker-src 'self' blob:"
    )
    assert v["ifdd_proxy_csp_ui"] == (
        "default-src 'self'; frame-ancestors 'none'; frame-src https://id.a.example.org; "
        "img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; connect-src 'self' https://id.a.example.org "
        "https://rest.a.example.org https://type.a.example.org https://unit.a.example.org"
    )
    labels = ["id", "rest", "ui", "schema", "type", "unit", "ts", "wf", "git"]
    names = [f"{label}.a.example.org" for label in labels]
    assert result.files["hosts.txt"].splitlines()[2:] == names  # no dashboard host


def test_without_wf_there_is_no_login_gate(tmp_path):
    result = compiler.compile_description(
        describe(tmp_path, "installation: x\nservices: [proxy, keycloak, be, ui]\n")
    )
    assert result.values["ifdd_proxy_enable_oauth2_proxy"] == "n"
    # keycloak needs a value even without the gate (its hook rejects an empty one)
    assert result.values["ifdd_keycloak_proxy_gate_hosts"] == "wf"
    assert result.values["ifdd_proxy_internal_hosts"] == "id, rest, ui"
    assert result.files["hosts.txt"].splitlines()[-1] == "127.0.0.1 id rest ui"
    assert "IFDD_META_WF_GIT_TOKEN" not in result.files[".env.example"]


def test_overrides_win_and_are_checked(tmp_path):
    result = compiler.compile_description(
        describe(
            tmp_path,
            "installation: x\noverrides:\n  ifdd_ts_retention: 1y\n"
            "  ifdd_wf_docker_mode: [socket, dind]\n",
        )
    )
    assert result.values["ifdd_ts_retention"] == "1y"
    assert result.values["ifdd_wf_docker_mode"] == ["socket", "dind"]
    with pytest.raises(compiler.CompileError, match="unknown variables: ifdd_nope"):
        compiler.compile_description(
            describe(tmp_path, "installation: x\noverrides:\n  ifdd_nope: a\n")
        )
    with pytest.raises(compiler.CompileError, match="same entries"):
        compiler.compile_description(
            describe(tmp_path, "installation: x\noverrides:\n  ifdd_wf_docker_mode: [socket]\n")
        )
    with pytest.raises(compiler.CompileError, match="expected a list"):
        compiler.compile_description(
            describe(tmp_path, "installation: x\noverrides:\n  ifdd_wf_docker_mode: socket\n")
        )


def test_generated_settings_file_is_valid(tmp_path):
    result = compiler.compile_description(
        describe(tmp_path, "installation: x\ntemplates:\n  ref: v26.11.1\n")
    )
    out = tmp_path / "out"
    compiler.write(result, out)
    loaded = settings.load(settings_file=out / "ifdd-meta.yaml", cwd=tmp_path, environ={})
    assert loaded["templates.ref"] == "v26.11.1"
    assert loaded["variables"] == out / "cookiecutter_all.yaml"
    assert yaml.safe_load((out / "cookiecutter_all.yaml").read_text())["default_context"] == (
        result.values
    )


def test_differences_finds_missing_and_changed_files(tmp_path):
    result = compiler.compile_description(describe(tmp_path, "installation: x\n"))
    out = tmp_path / "out"
    assert set(compiler.differences(result, out)) == set(compiler.OUTPUT_FILES)
    compiler.write(result, out)
    assert compiler.differences(result, out) == []
    (out / "hosts.txt").write_text("changed\n")
    assert compiler.differences(result, out) == ["hosts.txt"]


def test_the_demo_files_of_the_repository_are_up_to_date():
    result = compiler.compile_description(load(DEMO / "installation.yaml"))
    assert compiler.differences(result, DEMO) == []


def test_local_git_server_for_wf(tmp_path):
    result = compiler.compile_description(
        describe(tmp_path, "installation: x\nhosts:\n  suffix: a.example.org\n")
    )
    v = result.values
    assert v["ifdd_wf_git_url"] == "https://git.a.example.org/kestra/flows.git"
    assert v["ifdd_wf_git_username"] == "kestra"
    assert v["ifdd_proxy_internal_hosts"].split(", ")[-2:] == [
        "wf.a.example.org",
        "git.a.example.org",
    ]
    assert result.files["hosts.txt"].splitlines()[-1] == "git.a.example.org"
    generated = yaml.safe_load(result.files["ifdd-meta.yaml"])
    assert (generated["wf_git"], generated["git_host"]) == ("local", "git.a.example.org")
    assert "IFDD_META_WF_GIT_TOKEN" not in result.files[".env.example"]


def test_external_git_server_for_wf(tmp_path):
    result = compiler.compile_description(
        describe(
            tmp_path,
            "installation: x\nwf_git: external\noverrides:\n"
            "  ifdd_wf_git_url: https://git.example.org/team/flows.git\n"
            "  ifdd_wf_git_username: bot\n",
        )
    )
    assert result.values["ifdd_wf_git_url"] == "https://git.example.org/team/flows.git"
    assert "git" not in result.values["ifdd_proxy_internal_hosts"].split(", ")
    assert "IFDD_META_WF_GIT_TOKEN=" in result.files[".env.example"].splitlines()
    assert yaml.safe_load(result.files["ifdd-meta.yaml"])["wf_git"] == "external"


def test_warning_for_short_names_with_wf(tmp_path):
    assert compiler.compile_description(describe(tmp_path, "installation: x\n")).warnings
    with_suffix = "installation: x\nhosts:\n  suffix: ci.test\n"
    assert not compiler.compile_description(describe(tmp_path, with_suffix)).warnings
    without_wf = "installation: x\nservices: [proxy, be]\n"
    assert not compiler.compile_description(describe(tmp_path, without_wf)).warnings
