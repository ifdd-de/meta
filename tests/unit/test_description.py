from pathlib import Path

import pytest

from ifdd_meta.description import DescriptionError, load
from ifdd_meta.settings import SERVICES


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
    assert description.services == SERVICES
    assert description.ref == "main"


def test_demo_description_of_the_repository():
    path = Path(__file__).parents[2] / "config" / "demo" / "installation.yaml"
    description = load(path)
    assert description.host("rest") == "rest.demo.grid.example.ifdd.de"
    assert description.tls == "letsencrypt"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("installation: Demo\n", "lower-case name"),
        ("installation: x\nfoo: 1\n", "unknown keys: foo"),
        ("installation: x\nhosts:\n  suffix: nodot\n", "domain name"),
        ("installation: x\ntls: acme\n", "tls must be one of"),
        ("installation: x\ntls: letsencrypt\n", "needs hosts.suffix"),
        ("installation: x\ncontact: nobody\n", "e-mail"),
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
