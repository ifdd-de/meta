from pathlib import Path

import pytest

from ifdd_meta.description import DescriptionError, load, realm_files
from ifdd_meta.settings import DEFAULT_SERVICES


def describe(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "installation.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_minimal_description_takes_the_defaults(tmp_path):
    description = load(describe(tmp_path, "installation: ci\n"))
    assert description.suffix == ""
    assert description.host("id") == "id"
    assert description.tls == "local-ca"
    assert description.realm == "ifdd"
    assert description.services == DEFAULT_SERVICES
    assert description.ref == "main"


def test_demo_description_of_the_repository():
    path = Path(__file__).parents[2] / "config" / "demo" / "installation.yaml"
    description = load(path)
    assert description.host("rest") == "rest.demo.grid.example.ifdd.de"
    assert description.tls == "letsencrypt"
    assert description.ref == "v26.11.2"
    assert description.refs["schema"] == "v26.11.1"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("installation: Demo\n", "lower-case name"),
        ("installation: x\nfoo: 1\n", "unknown keys: foo"),
        ("installation: x\nhosts:\n  suffix: nodot\n", "domain name"),
        ("installation: x\ntls: acme\n", "tls must be one of"),
        ("installation: x\ntls: letsencrypt\n", "needs public names"),
        ("installation: x\ncontact: nobody\n", "e-mail"),
        ("installation: x\ntemplates:\n  refs: [be]\n", "templates.refs must be a mapping"),
        ("installation: x\ntemplates:\n  refs:\n    web: main\n", "unknown service 'web'"),
        ("installation: x\ntemplates:\n  refs:\n    be: ' '\n", "templates.refs.be must be"),
        ("installation: x\ntemplates:\n  branch: main\n", "takes only 'ref' and 'refs'"),
        ("installation: x\nrealm: master\n", "invalid realm"),
        ("installation: x\nextra_realms: [ifdd]\n", "must not repeat"),
        ("installation: x\nservices: [be, frontend]\n", "unknown services: frontend"),
        ("installation: x\ncontext_root: IFDD.de\n", "lower-case dotted"),
        ("installation: x\noverrides:\n  ifdd_be_enable_auth: no\n", "in quotes"),
        ("installation: x\noverrides:\n  ifdd_x: {a: b}\n", "text or a list"),
        ("- a\n", "mapping at the top level"),
    ],
)
def test_invalid_descriptions(tmp_path, text, message):
    with pytest.raises(DescriptionError, match=message):
        load(describe(tmp_path, text))


def test_numbers_in_overrides_become_text(tmp_path):
    description = load(
        describe(tmp_path, "installation: x\noverrides:\n  ifdd_be_max_workers: 8\n")
    )
    assert description.overrides == {"ifdd_be_max_workers": "8"}


DEV = """\
installation: dev
tls: own
realm: dev
templates:
  ref: v1
services:
  proxy: {}
  be: {host: data.dev.cache.example.org}
  ui: {host: ui.dev.cache.example.org}
  ts: {host: ts.dev.cache.example.org}
  ts-api: {}
  ct: {}
  keycloak: {host: oidc.example.org, external: true, ref: v2}
  schema: {host: schema.model.example.org, external: true}
  type: {host: type.model.example.org, external: true}
  unit: {host: unit.model.example.org, external: true}
"""


def test_service_blocks_local_external_hosts_and_refs(tmp_path):
    d = load(describe(tmp_path, DEV))
    assert d.services == ("proxy", "be", "ui", "ct", "ts", "ts-api")
    assert d.external == ("keycloak", "schema", "type", "unit")
    assert d.host("id") == "oidc.example.org"
    assert d.host("rest") == "data.dev.cache.example.org"
    assert d.refs == {"keycloak": "v2"}


def test_model_has_a_host_of_its_own(tmp_path):
    text = DEV.replace("  ct: {}\n", "  ct: {}\n  model: {host: linkml.dev.cache.example.org}\n")
    d = load(describe(tmp_path, text))
    assert "model" in d.services
    assert d.host("model") == "linkml.dev.cache.example.org"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("  ct: {}\n", "  ct: {external: true}\n"), "only a service with a host"),
        (("  proxy: {}\n", "  proxy: {external: true}\n"), "only a service with a host"),
        (("  ct: {}\n", "  ct: {host: ct.example.org}\n"), "has no host of its own"),
        (("  ct: {}\n", "  ct: {port: 1}\n"), "unknown keys: port"),
        (("external: true}\n", "external: yes please}\n"), "true or false"),
        (("  schema: {host: schema.model.example.org, external: true}\n", ""), "be needs schema"),
        (("  ts: {host: ts.dev.cache.example.org}\n", "  ts: {host: ts.x.org, external: true}\n"),
         "ts-api needs ts on the same server"),
        (("  ui: {host: ui.dev.cache.example.org}\n", "  ui: {}\n"), "no host for ui"),
        # model is a web service with a host since 26.11.6
        (("  ct: {}\n", "  ct: {}\n  model: {}\n"), "no host for model"),
        (("  ref: v1\n", "  ref: v1\n  refs: {be: v3}\n"), "put each ref into its block"),
        (("  ct: {}\n", "  ct: {}\n  git: {host: git.example.org, external: true}\n"),
         "takes only a host"),
    ],
)  # fmt: skip
def test_invalid_service_blocks(tmp_path, change, message):
    old, new = change
    assert old in DEV
    with pytest.raises(DescriptionError, match=message):
        load(describe(tmp_path, DEV.replace(old, new, 1)))


def test_short_form_also_checks_dependencies(tmp_path):
    with pytest.raises(DescriptionError, match="be needs keycloak, schema"):
        load(describe(tmp_path, "installation: x\nservices: [proxy, be]\n"))


def test_ca_certs_must_exist_and_differ(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "ca.crt").write_text("x")
    (tmp_path / "ca.crt").write_text("x")
    ok = load(describe(tmp_path, "installation: x\nca_certs: [a/ca.crt]\n"))
    assert ok.ca_certs == ("a/ca.crt",)
    with pytest.raises(DescriptionError, match="file not found: b.crt"):
        load(describe(tmp_path, "installation: x\nca_certs: [b.crt]\n"))
    with pytest.raises(DescriptionError, match="names must differ"):
        load(describe(tmp_path, "installation: x\nca_certs: [a/ca.crt, ca.crt]\n"))


# files: private files into the instances (create copies them)

PROD = "installation: prod\nrealm: prod\nextra_realms: [dev, int]\nservices: [proxy, keycloak]\n"


def values_file(tmp_path: Path, name: str, realm: str) -> None:
    (tmp_path / name).write_text(f'[realm]\nname = "{realm}"\ndisplay_name = "x"\n')


def prod_files(tmp_path: Path) -> str:
    values_file(tmp_path, "realm-dev.toml", "dev")
    values_file(tmp_path, "realm-int.toml", "int")
    (tmp_path / "theme.css").write_text("/* own */\n")
    return (
        "files:\n"
        "  - {from: realm-dev.toml, to: keycloak/realm/values_dev.toml}\n"
        "  - {from: realm-int.toml, to: keycloak/realm/values_int.toml}\n"
        "  - {from: theme.css, to: keycloak/themes/ifdd/login/resources/css/ifdd.css}\n"
    )


def test_files_with_a_values_file_per_further_realm(tmp_path):
    d = load(describe(tmp_path, PROD + prod_files(tmp_path)))
    assert d.files == (
        ("keycloak/realm/values_dev.toml", "realm-dev.toml"),
        ("keycloak/realm/values_int.toml", "realm-int.toml"),
        ("keycloak/themes/ifdd/login/resources/css/ifdd.css", "theme.css"),
    )
    assert realm_files(d.files) == [
        "keycloak/realm/values_dev.toml",
        "keycloak/realm/values_int.toml",
    ]


def test_files_also_with_service_blocks(tmp_path):
    (tmp_path / "token").write_text("t")
    text = DEV + "files:\n  - {from: token, to: ts/secrets/token}\n"
    assert load(describe(tmp_path, text)).files == (("ts/secrets/token", "token"),)


def test_realm_files_are_only_the_values_files_of_keycloak():
    files = [
        ("keycloak/realm/values_a.toml", "a"),
        ("keycloak/realm/values.toml", "b"),
        ("keycloak/realm/x/values_c.toml", "c"),
        ("keycloak/realm/values_d.json", "d"),
        ("wf/realm/values_e.toml", "e"),
    ]
    assert realm_files(files) == ["keycloak/realm/values_a.toml"]


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ("  - {from: theme.css}\n", "exactly the keys from and to"),
        ("  - {from: theme.css, to: proxy/x, mode: 1}\n", "exactly the keys from and to"),
        ("  - {from: nothing.css, to: proxy/x.css}\n", "file not found: nothing.css"),
        ("  - {from: theme.css, to: ''}\n", "non-empty texts"),
        ("  - {from: theme.css, to: /etc/x}\n", "to must be <service>/<path"),
        ("  - {from: theme.css, to: proxy}\n", "to must be <service>/<path"),
        ("  - {from: theme.css, to: proxy/../be/x}\n", "to must be <service>/<path"),
        ("  - {from: theme.css, to: proxy/./x}\n", "to must be <service>/<path"),
        ("  - {from: theme.css, to: proxy//x}\n", "to must be <service>/<path"),
        ("  - {from: theme.css, to: 'proxy\\x'}\n", "to must be <service>/<path"),
        ("  - {from: theme.css, to: be/x}\n", "'be' is not a local service"),
        ("  - {from: theme.css, to: proxy/.env}\n", "written by create, cruft or Git"),
        ("  - {from: theme.css, to: proxy/.cruft.json}\n", "written by create"),
        ("  - {from: theme.css, to: proxy/.git/config}\n", "written by create"),
        ("  - {from: theme.css, to: proxy/x}\n  - {from: theme.css, to: proxy/x}\n",
         "a target appears twice"),
    ],
)  # fmt: skip
def test_invalid_files(tmp_path, entries, message):
    text = PROD + prod_files(tmp_path) + entries
    with pytest.raises(DescriptionError, match=message):
        load(describe(tmp_path, text))


def test_files_must_be_a_list(tmp_path):
    with pytest.raises(DescriptionError, match="files must be a list"):
        load(describe(tmp_path, "installation: x\nfiles: {a: b}\n"))


@pytest.mark.parametrize(
    ("realm", "message"),
    [
        ("prod", "for realm 'prod', the realm of the installation"),
        ("test", "for realm 'test', not in extra_realms"),
        ("dev", "realm 'dev' has two values files"),
        (1, "for realm 1, not in extra_realms"),
    ],
)
def test_values_file_for_a_wrong_realm(tmp_path, realm, message):
    if isinstance(realm, str):
        values_file(tmp_path, "realm-x.toml", realm)
    else:
        (tmp_path / "realm-x.toml").write_text(f"[realm]\nname = {realm}\n")
    text = (
        PROD + prod_files(tmp_path) + "  - {from: realm-x.toml, to: keycloak/realm/values_x.toml}\n"
    )
    with pytest.raises(DescriptionError, match=message):
        load(describe(tmp_path, text))


def test_further_realm_without_values_file(tmp_path):
    text = PROD + prod_files(tmp_path).replace(
        "  - {from: realm-int.toml, to: keycloak/realm/values_int.toml}\n", ""
    )
    with pytest.raises(DescriptionError, match="extra_realms int without a values file"):
        load(describe(tmp_path, text))


@pytest.mark.parametrize(
    "content", ["[realm\n", "[roles]\npublic = 'x'\n", "realm = 'dev'\n", "[realm]\ntitle = 'x'\n"]
)
def test_values_file_without_realm_name(tmp_path, content):
    (tmp_path / "broken.toml").write_text(content)
    text = (
        PROD + prod_files(tmp_path) + "  - {from: broken.toml, to: keycloak/realm/values_b.toml}\n"
    )
    with pytest.raises(DescriptionError, match=r"broken.toml: no realm name"):
        load(describe(tmp_path, text))


def test_realm_files_are_not_checked_without_a_local_keycloak(tmp_path):
    """extra_realms belong to the server of Keycloak; another server only names them."""
    text = DEV.replace("installation: dev\n", "installation: dev\nextra_realms: [x]\n")
    assert "extra_realms: [x]" in text
    assert load(describe(tmp_path, text)).files == ()
