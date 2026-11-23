"""config check against real, local Git repositories of fake templates."""

import json
import subprocess
from pathlib import Path

import pytest

from ifdd_meta import check
from ifdd_meta.settings import SERVICES, load

GIT = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.org"]


def make_templates(root: Path, extra: dict[str, dict] | None = None) -> None:
    """One repository per service with a cookiecutter.json, committed and tagged v1."""
    for service in SERVICES:
        repo = root / service
        repo.mkdir(parents=True)
        key = service.replace("-", "_")
        data = {
            f"ifdd_{key}_prj_name": service,
            f"ifdd_{key}_prj_slug": f"{{{{ cookiecutter.ifdd_{key}_prj_name|lower }}}}",
            "_copy_without_render": [".github"],
        }
        data.update((extra or {}).get(service, {}))
        (repo / "cookiecutter.json").write_text(json.dumps(data))
        subprocess.run([*GIT, "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run([*GIT, "-C", str(repo), "add", "."], check=True)
        subprocess.run([*GIT, "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
        subprocess.run([*GIT, "-C", str(repo), "tag", "v1"], check=True)


def variables_for(extra: dict[str, dict] | None = None) -> dict:
    context = {}
    for service in SERVICES:
        key = service.replace("-", "_")
        context[f"ifdd_{key}_prj_name"] = service
        context[f"ifdd_{key}_prj_slug"] = f"{{{{ cookiecutter.ifdd_{key}_prj_name|lower }}}}"
    for values in (extra or {}).values():
        context.update({k: v for k, v in values.items() if not k.endswith("_version")})
    return context


def run_check(tmp_path, context, extra=None, services=None):
    templates = tmp_path / "templates"
    if not templates.exists():
        make_templates(templates, extra)
    (tmp_path / "vars.yaml").write_text(json.dumps({"default_context": context}))
    settings_text = f"variables: vars.yaml\ntemplates:\n  source: {templates}\n  ref: v1\n"
    if services:
        settings_text += f"services: [{', '.join(services)}]\n"
    (tmp_path / "ifdd-meta.yaml").write_text(settings_text)
    settings = load(cwd=tmp_path, environ={})
    return [finding.message for finding in check.run(settings).findings]


EXTRA = {
    "proxy": {"ifdd_proxy_mode": ["a", "b"], "ifdd_shared": "x"},
    "be": {"ifdd_shared": "x", "ifdd_be_version": "1.0"},
}


def test_complete_file_passes(tmp_path):
    assert run_check(tmp_path, variables_for(EXTRA), EXTRA) == []


def test_missing_variable_is_an_error_and_versions_are_exempt(tmp_path):
    context = variables_for(EXTRA)
    del context["ifdd_shared"]
    assert run_check(tmp_path, context, EXTRA) == [
        "ifdd_shared (template proxy) is missing",
        "ifdd_shared (template be) is missing",
    ]


def test_missing_variable_of_an_unselected_service_is_fine(tmp_path):
    context = variables_for(EXTRA)
    del context["ifdd_proxy_mode"]
    assert run_check(tmp_path, context, EXTRA, services=["be", "ui"]) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"ifdd_typo": "x"}, "ifdd_typo is defined by no template"),
        ({"ifdd_be_version": "2.0"}, "ifdd_be_version must not be set"),
        ({"ifdd_proxy_mode": ["a"]}, "ifdd_proxy_mode is a choice"),
        ({"ifdd_proxy_mode": "a"}, "ifdd_proxy_mode is a choice"),
        ({"ifdd_shared": "{{ cookiecutter.ifdd_be_prj_name }}"}, "ifdd_shared uses Jinja"),
        (
            {"ifdd_ui_prj_slug": "{{ cookiecutter.ifdd_be_prj_name }}"},
            "ifdd_ui_prj_slug uses Jinja",
        ),
        ({"ifdd_shared": True}, "ifdd_shared must be a text"),
    ],
)
def test_wrong_values(tmp_path, change, message):
    context = variables_for(EXTRA) | change
    findings = run_check(tmp_path, context, EXTRA)
    assert any(finding.startswith(message) for finding in findings), findings


def test_secret_like_names_are_errors(tmp_path):
    extra = {"be": {"ifdd_be_db_password": "x"}}
    findings = run_check(tmp_path, variables_for(extra), extra)
    assert findings == ["ifdd_be_db_password looks like a secret; secrets belong in .env"]


def test_choice_in_another_order_passes(tmp_path):
    context = variables_for(EXTRA) | {"ifdd_proxy_mode": ["b", "a"]}
    assert run_check(tmp_path, context, EXTRA) == []


def test_unknown_reference_is_reported(tmp_path):
    make_templates(tmp_path / "templates", EXTRA)
    (tmp_path / "vars.yaml").write_text(json.dumps({"default_context": variables_for(EXTRA)}))
    (tmp_path / "ifdd-meta.yaml").write_text(
        f"variables: vars.yaml\ntemplates:\n  source: {tmp_path / 'templates'}\n  ref: v9\n"
    )
    report = check.run(load(cwd=tmp_path, environ={}))
    # lab is not among the default services: a note, no error
    assert len(report.findings) == len(SERVICES) - 1
    assert report.findings[0].message.startswith("proxy@v9: git -C")
    assert report.notes == ("template lab (not used) not found at v9: ifdd_lab_* unchecked",)


def test_unused_template_without_the_reference_leaves_only_its_variables_unchecked(tmp_path):
    make_templates(tmp_path / "templates", EXTRA)
    subprocess.run([*GIT, "-C", str(tmp_path / "templates" / "lab"), "tag", "-d", "v1"],
                   check=True, capture_output=True)  # fmt: skip
    context = variables_for(EXTRA)
    context["ifdd_nobody"] = "x"
    (tmp_path / "vars.yaml").write_text(json.dumps({"default_context": context}))
    (tmp_path / "ifdd-meta.yaml").write_text(
        f"variables: vars.yaml\ntemplates:\n  source: {tmp_path / 'templates'}\n  ref: v1\n"
    )
    report = check.run(load(cwd=tmp_path, environ={}))
    assert [f.message for f in report.findings] == [
        "ifdd_nobody is defined by no template; cookiecutter would ignore it"
    ]
    assert report.notes == ("template lab (not used) not found at v1: ifdd_lab_* unchecked",)
    # used (here: external), the missing template is an error again
    (tmp_path / "ifdd-meta.yaml").write_text(
        f"variables: vars.yaml\ntemplates:\n  source: {tmp_path / 'templates'}\n  ref: v1\n"
        "external: [lab]\n"
    )
    findings = check.run(load(cwd=tmp_path, environ={})).findings
    assert findings[0].message.startswith("lab@v1: git -C")


def test_missing_variables_file(tmp_path):
    (tmp_path / "ifdd-meta.yaml").write_text("variables: none.yaml\n")
    findings = check.run(load(cwd=tmp_path, environ={})).findings
    assert findings[0].message.startswith("variables file")
