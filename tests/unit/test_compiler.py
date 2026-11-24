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


def realm_file(tmp_path: Path, realm: str) -> str:
    """A values file of a further realm, as files: copies it to keycloak/realm/."""
    (tmp_path / f"realm-{realm}.toml").write_text(f'[realm]\nname = "{realm}"\n', encoding="utf-8")
    return f"  - {{from: realm-{realm}.toml, to: keycloak/realm/values_{realm}.toml}}\n"


def test_public_names_with_lets_encrypt(tmp_path):
    files = "files:\n" + realm_file(tmp_path, "r2") + realm_file(tmp_path, "r3")
    result = compiler.compile_description(
        describe(
            tmp_path,
            "installation: demo\nhosts:\n  suffix: a.example.org\ntls: letsencrypt\n"
            "contact: ops@example.org\nrealm: r1\nextra_realms: [r2, r3]\n" + files,
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
    assert v["ifdd_model_host"] == "model.a.example.org"
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
    labels = ["id", "rest", "ui", "schema", "type", "unit", "ts", "wf", "model", "git"]
    names = [f"{label}.a.example.org" for label in labels]
    assert result.files["hosts.txt"].splitlines()[2:] == names  # no dashboard host


def test_without_wf_there_is_no_login_gate(tmp_path):
    result = compiler.compile_description(
        describe(
            tmp_path, "installation: x\nservices: [proxy, keycloak, be, ui, schema, type, unit]\n"
        )
    )
    assert result.values["ifdd_proxy_enable_oauth2_proxy"] == "n"
    # no host behind the gate; keycloak accepts an empty value since 26.11.2
    assert result.values["ifdd_keycloak_proxy_gate_hosts"] == ""
    assert result.values["ifdd_proxy_internal_hosts"] == "id, rest, ui, schema, type, unit"
    lines = result.files["hosts.txt"].splitlines()
    assert (lines[0], lines[-2], lines[-1]) == (
        "# BEGIN ifdd-meta x",
        "127.0.0.1 id rest ui schema type unit",
        "# END ifdd-meta x",
    )
    assert all(line.startswith("#") for line in lines[1:-2])
    assert "IFDD_META_WF_GIT_TOKEN" not in result.files[".env.example"]


def test_template_refs_reach_the_settings_file(tmp_path):
    text = "installation: x\ntemplates:\n  ref: v26.11.2\n  refs:\n    ts: v26.11.1\n    be: main\n"
    result = compiler.compile_description(describe(tmp_path, text))
    settings = yaml.safe_load(result.files["ifdd-meta.yaml"])
    # in catalogue order, whatever the order of the description
    assert settings["templates"] == {
        "source": "https://github.com/ifdd-de",
        "ref": "v26.11.2",
        "refs": {"be": "main", "ts": "v26.11.1"},
    }
    assert list(settings["templates"]["refs"]) == ["be", "ts"]
    plain = compiler.compile_description(describe(tmp_path, "installation: x\n"))
    assert "refs" not in yaml.safe_load(plain.files["ifdd-meta.yaml"])["templates"]


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
    assert v["ifdd_proxy_internal_hosts"].split(", ")[-3:] == [
        "wf.a.example.org",
        "model.a.example.org",
        "git.a.example.org",
    ]
    line = result.files["hosts.txt"].splitlines()[-2]
    assert line.endswith(" wf.a.example.org model.a.example.org git.a.example.org")
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
    without_wf = "installation: x\nservices: [proxy, keycloak, be, schema]\n"
    assert not compiler.compile_description(describe(tmp_path, without_wf)).warnings


def test_local_ca_with_a_suffix_gives_an_etc_hosts_block(tmp_path):
    """A test machine with names like id.demo.test: no DNS, a block for /etc/hosts."""
    text = "installation: notebook\nhosts:\n  suffix: demo.test\ntls: local-ca\n"
    lines = compiler.compile_description(describe(tmp_path, text)).files["hosts.txt"].splitlines()
    begin, end = compiler.hosts_markers("notebook")
    assert lines[0] == begin and lines[-1] == end  # the whole file is the block
    names = lines[-2].split()
    assert names[0] == "127.0.0.1"
    assert names[1:] == [
        f"{label}.demo.test"
        for label in ("id", "rest", "ui", "schema", "type", "unit", "ts", "wf", "model", "git")
    ]
    assert "proxy.demo.test" not in names  # the dashboard has no host entry


def test_public_names_with_own_certificates_give_dns_records(tmp_path):
    text = "installation: x\nhosts:\n  suffix: a.example.org\ntls: own\n"
    hosts = compiler.compile_description(describe(tmp_path, text)).files["hosts.txt"]
    assert "127.0.0.1" not in hosts and "BEGIN" not in hosts
    assert hosts.splitlines()[-1] == "git.a.example.org"


def test_lab_gets_its_client_network_and_host(tmp_path):
    text = (
        "installation: x\nhosts:\n  suffix: a.test\nservices: [proxy, keycloak, be, schema, lab]\n"
    )
    v = compiler.compile_description(describe(tmp_path, text)).values
    assert v["ifdd_keycloak_lab_client_id"] == "ifdd-lab"
    assert v["ifdd_proxy_extra_networks"] == v["ifdd_lab_network"] == "lab-net"
    assert v["ifdd_lab_host"] == "lab.a.test"
    assert "lab.a.test" in v["ifdd_proxy_internal_hosts"].split(", ")
    assert v["ifdd_oidc_realm"] == "https://id.a.test/realms/ifdd"
    assert v["ifdd_be_url"] == "https://rest.a.test"
    assert v["ifdd_lab_internet"][0] == "n"  # no internet by default


def test_without_lab_no_client_and_no_network(tmp_path):
    text = "installation: x\nhosts:\n  suffix: a.test\n"
    v = compiler.compile_description(describe(tmp_path, text)).values
    assert v["ifdd_keycloak_lab_client_id"] == ""
    assert v["ifdd_proxy_extra_networks"] == ""
    assert "lab.a.test" not in v["ifdd_proxy_internal_hosts"]


def test_a_renamed_lab_network_reaches_the_proxy(tmp_path):
    text = (
        "installation: x\nservices: [proxy, keycloak, be, schema, lab]\n"
        "overrides:\n  ifdd_lab_network: other-net\n"
    )
    v = compiler.compile_description(describe(tmp_path, text)).values
    assert v["ifdd_proxy_extra_networks"] == v["ifdd_lab_network"] == "other-net"


def test_external_services_give_urls_but_no_local_hosts(tmp_path):
    from test_description import DEV as dev_text

    result = compiler.compile_description(describe(tmp_path, dev_text))
    v = result.values
    assert v["ifdd_oidc_realm"] == "https://oidc.example.org/realms/dev"
    assert v["ifdd_be_url"] == "https://data.dev.cache.example.org"
    assert v["ifdd_schema_url"] == "https://schema.model.example.org/v1"
    internal = set(v["ifdd_proxy_internal_hosts"].split(", "))
    assert {"data.dev.cache.example.org"} <= internal
    assert not {"oidc.example.org", "schema.model.example.org"} & internal
    hosts_lines = set(result.files["hosts.txt"].splitlines())
    assert {"data.dev.cache.example.org"} <= hosts_lines
    assert hosts_lines.isdisjoint({"oidc.example.org"})
    assert {"https://oidc.example.org"} <= set(v["ifdd_proxy_csp_ui"].split())
    settings_text = result.files["ifdd-meta.yaml"]
    assert "external: [keycloak, schema, type, unit]\n" in settings_text
    assert "keycloak_secrets: keycloak-secrets.env\n" in settings_text
    assert "    keycloak: v2\n" in settings_text
    assert "services: [proxy, be, ui, ct, ts, ts-api]\n" in settings_text


def test_files_reach_the_settings_and_resolve_next_to_them(tmp_path):
    files = "files:\n" + realm_file(tmp_path, "dev")
    (tmp_path / "my theme.css").write_text("/* own */\n")
    files += '  - {from: "my theme.css", to: keycloak/themes/ifdd/login/resources/css/ifdd.css}\n'
    text = "installation: prod\nrealm: prod\nextra_realms: [dev]\n" + files
    result = compiler.compile_description(describe(tmp_path, text))
    compiler.write(result, tmp_path)
    loaded = settings.load(settings_file=tmp_path / "ifdd-meta.yaml", cwd=tmp_path, environ={})
    assert loaded["files"] == {
        "keycloak/realm/values_dev.toml": tmp_path / "realm-dev.toml",
        "keycloak/themes/ifdd/login/resources/css/ifdd.css": tmp_path / "my theme.css",
    }
    assert (
        "files:"
        not in compiler.compile_description(describe(tmp_path, "installation: x\n")).files[
            "ifdd-meta.yaml"
        ]
    )


@pytest.mark.parametrize(
    ("tls", "ca_certs", "expected"),
    [
        ("local-ca", False, "ca.crt"),
        ("own", False, "ca.crt"),
        ("letsencrypt", False, ""),
        ("local-ca", True, "ca-bundle.crt"),
        ("own", True, "ca-bundle.crt"),
        ("letsencrypt", True, "ca-bundle.crt"),
    ],
)
def test_oauth2_proxy_trusts_the_bundle_with_ca_certs(tmp_path, tls, ca_certs, expected):
    (tmp_path / "corp.crt").write_text("x")
    text = f"installation: x\nhosts:\n  suffix: a.example.org\ntls: {tls}\n"
    text += "ca_certs: [corp.crt]\n" if ca_certs else ""
    values = compiler.compile_description(describe(tmp_path, text)).values
    assert values["ifdd_proxy_oauth2_proxy_ca_file"] == expected


DEV_BLOCKS = """installation: dev
tls: {tls}
services:
  proxy: {{host: proxy.dev.cache.example.org}}
  be: {{host: data.dev.cache.example.org}}
  ui: {{host: ui.dev.cache.example.org}}
  keycloak: {{host: oidc.example.org, external: true}}
  schema: {{host: schema.model.example.org, external: true}}
  type: {{host: type.model.example.org, external: true}}
  unit: {{host: unit.model.example.org, external: true}}
"""


@pytest.mark.parametrize("tls", ["own", "letsencrypt"])
def test_an_explicit_proxy_host_gets_a_dns_record(tmp_path, tls):
    result = compiler.compile_description(describe(tmp_path, DEV_BLOCKS.format(tls=tls)))
    assert result.files["hosts.txt"].splitlines()[2:] == [
        "data.dev.cache.example.org",
        "ui.dev.cache.example.org",
        "proxy.dev.cache.example.org",
    ]
    assert result.values["ifdd_proxy_host"] == "proxy.dev.cache.example.org"
    # no network alias: no container calls the dashboard
    assert "proxy.dev" not in result.values["ifdd_proxy_internal_hosts"]


def test_an_explicit_proxy_host_is_in_the_etc_hosts_block(tmp_path):
    result = compiler.compile_description(describe(tmp_path, DEV_BLOCKS.format(tls="local-ca")))
    names = result.files["hosts.txt"].splitlines()[-2].split()
    assert names == [
        "127.0.0.1",
        "data.dev.cache.example.org",
        "ui.dev.cache.example.org",
        "proxy.dev.cache.example.org",
    ]


def test_protected_reaches_the_settings(tmp_path):
    result = compiler.compile_description(describe(tmp_path, "installation: x\nprotected: true\n"))
    compiler.write(result, tmp_path)
    assert settings.load(settings_file=tmp_path / "ifdd-meta.yaml", environ={})["protected"]
    plain = compiler.compile_description(describe(tmp_path, "installation: x\n"))
    assert "protected" not in plain.files["ifdd-meta.yaml"]
