"""The examples of docs/multi-server.md: the stage descriptions and the tools vServer script."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ifdd_meta import compiler, settings
from ifdd_meta.description import load

EXAMPLES = Path(__file__).parents[2] / "docs" / "examples"
SCRIPT = EXAMPLES / "tools-vserver.sh"
STAGES = ("prod", "int", "dev")


@pytest.fixture
def installations(tmp_path):
    """installations/ as on the tools vServer, with stand-ins for the private files."""
    root = tmp_path / "installations"
    shutil.copytree(EXAMPLES / "stages", root)
    (root / "certs").mkdir()
    for name in ("company-root-ca", "company-sub-ca", "web-proxy-ca"):
        (root / "certs" / f"{name}.crt").write_text(f"-----{name}-----\n")
    description = yaml.safe_load((root / "prod" / "installation.yaml").read_text())
    for entry in description["files"]:
        source = root / "prod" / entry["from"]
        source.parent.mkdir(parents=True, exist_ok=True)
        realm = re.fullmatch(r"realms/values_(.+)\.toml", entry["from"])
        source.write_text(f'[realm]\nname = "{realm[1]}"\n' if realm else "x\n")
    return root


def compiled(installations, stage):
    result = compiler.compile_description(load(installations / stage / "installation.yaml"))
    compiler.write(result, installations / stage)
    return result, settings.load(settings_file=installations / stage / "ifdd-meta.yaml", environ={})


@pytest.mark.parametrize("stage", STAGES)
def test_every_stage_description_compiles(installations, stage):
    result, loaded = compiled(installations, stage)
    v = result.values
    assert loaded["protected"] is True
    assert v["ifdd_oidc_realm"] == f"https://oidc.example.org/realms/ifdd-{stage}"
    assert v["ifdd_be_url"] == f"https://data.{stage}.cache.example.org"
    assert v["ifdd_type_url"] == "https://type.model.example.org/v1"
    assert v["ifdd_proxy_oauth2_proxy_ca_file"] == "ca-bundle.crt"
    assert len(loaded["ca_certs"]) == 3
    hosts = result.files["hosts.txt"].splitlines()
    assert f"proxy.{stage}.cache.example.org" in hosts  # the dashboard of every stage
    assert f"wf.{stage}.cache.example.org" in hosts


def test_prod_runs_keycloak_and_the_static_services_for_all(installations):
    _, prod = compiled(installations, "prod")
    assert {"keycloak", "schema", "type", "unit", "model"} <= set(prod["services"])
    assert prod["external"] == ()
    assert sorted(prod["files"]) == [
        "keycloak/realm/values_ifdd-demo.toml",
        "keycloak/realm/values_ifdd-dev.toml",
        "keycloak/realm/values_ifdd-int.toml",
        "keycloak/themes/ifdd/login/resources/css/ifdd.css",
        "keycloak/themes/ifdd/login/resources/img/logo.svg",
        "keycloak/themes/ifdd/login/theme.properties",
    ]
    for stage in ("int", "dev"):
        _, other = compiled(installations, stage)
        assert set(other["external"]) == {"keycloak", "schema", "type", "unit", "model"}
        assert other["files"] == {}


def test_the_names_are_invented_examples():
    """Examples name no real host, domain, address or organisation: only example.org."""
    files = (".toml", ".yaml", ".env", ".crt", ".css", ".svg", ".properties", ".md", ".sh", ".git")
    for path in [SCRIPT, *(EXAMPLES / "stages").rglob("*.yaml")]:
        text = path.read_text()
        names = set(re.findall(r"[a-z0-9.-]+\.[a-z]{2,}\b", text.lower()))
        real = {n for n in names if not n.endswith(("example.org", *files))}
        assert real == set(), (path, real)
        assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", text), path


def test_the_realms_of_the_script_are_those_of_the_descriptions():
    script = SCRIPT.read_text()
    realms = dict(re.findall(r"\[(\w+)\]=(ifdd-\w+)", script))
    assert realms == {
        stage: yaml.safe_load((EXAMPLES / "stages" / stage / "installation.yaml").read_text())[
            "realm"
        ]
        for stage in STAGES
    }


# tools-vserver.sh: once with --dry-run, once with stand-ins for ssh and rsync

FAKE_SSH = r"""#!/usr/bin/env python3
import json, os, sys
command = sys.argv[-1]
record = {"tool": "ssh", "argv": sys.argv[1:]}
if "keycloak secrets" in command:
    print("IFDD_KEYCLOAK_CT_CLIENT_SECRET=s3cret")
elif "cat > keycloak-secrets.env" in command:
    record["stdin"] = sys.stdin.read()
    record["umask"] = "umask 077" in command
else:
    print("==> done")
with open(os.environ["CALLS"], "a") as out:
    out.write(json.dumps(record) + "\n")
"""
FAKE_RSYNC = r"""#!/usr/bin/env python3
import json, os, sys
with open(os.environ["CALLS"], "a") as out:
    out.write(json.dumps({"tool": "rsync", "argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")
"""


def run_script(tmp_path, *args, fake=True):
    env = dict(os.environ)
    if fake:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        for name, text in (("ssh", FAKE_SSH), ("rsync", FAKE_RSYNC)):
            (bin_dir / name).write_text(text)
            (bin_dir / name).chmod(0o755)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env["CALLS"] = str(tmp_path / "calls")
    result = subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False
    )
    calls = []
    if fake and (tmp_path / "calls").exists():
        calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    return result, calls


def target(call) -> str:
    """The stage a call goes to (the ssh destination or the rsync target)."""
    destination = call["argv"][-2] if call["tool"] == "ssh" else call["argv"][-1]
    return destination.split("@")[1].split(".")[0]


def test_setup_installs_prod_first_and_pipes_the_secrets(tmp_path):
    result, calls = run_script(tmp_path, "setup")
    assert result.returncode == 0, result.stderr
    assert [(c["tool"], target(c)) for c in calls] == [
        ("rsync", "prod"), ("ssh", "prod"), ("ssh", "prod"),
        ("rsync", "int"), ("ssh", "int"), ("ssh", "prod"), ("ssh", "int"), ("ssh", "int"),
        ("rsync", "dev"), ("ssh", "dev"), ("ssh", "prod"), ("ssh", "dev"), ("ssh", "dev"),
    ]  # fmt: skip
    commands = [c["argv"][-1] for c in calls if c["tool"] == "ssh"]
    assert "compile installation.yaml" in commands[0] and "config check" in commands[0]
    assert commands[1].endswith(
        "create && uv run --project ~/ifdd-meta ifdd-meta --settings ifdd-meta.yaml build"
        " && uv run --project ~/ifdd-meta ifdd-meta --settings ifdd-meta.yaml up"
    )
    secrets = [c for c in calls if "stdin" in c]
    assert [target(c) for c in secrets] == ["int", "dev"]
    assert all(c["stdin"] == "IFDD_KEYCLOAK_CT_CLIENT_SECRET=s3cret\n" for c in secrets)
    assert all(c["umask"] for c in secrets)
    assert "--realm ifdd-int" in commands[3] and "--realm ifdd-dev" in commands[7]
    # the secret never reaches the console of the tools vServer
    assert "s3cret" not in result.stdout + result.stderr
    assert "[prod] ==> done" in result.stdout


def test_ssh_never_forwards_the_agent(tmp_path):
    _, calls = run_script(tmp_path, "setup")
    for call in calls:
        argv = (
            call["argv"]
            if call["tool"] == "ssh"
            else call["argv"][call["argv"].index("-e") + 1].split()
        )
        assert "-A" not in argv
        assert "ForwardAgent=no" in argv
        assert "BatchMode=yes" in argv


def test_rsync_copies_descriptions_never_secrets(tmp_path):
    _, calls = run_script(tmp_path, "setup", "dev")
    [rsync] = [c for c in calls if c["tool"] == "rsync"]
    assert rsync["cwd"] == str(EXAMPLES)  # the directory of the script
    assert rsync["argv"][-3:] == ["certs", "dev", "ifdd@dev.cache.example.org:installations/"]
    for excluded in (".env", "keycloak-secrets.env", "work/", "state/"):
        assert rsync["argv"][rsync["argv"].index(excluded) - 1] == "--exclude"


def test_update_goes_dev_int_prod_and_status_everywhere(tmp_path):
    result, calls = run_script(tmp_path, "update")
    assert result.returncode == 0, result.stderr
    ssh = [c for c in calls if c["tool"] == "ssh"]
    assert [target(c) for c in ssh] == ["dev", "dev", "int", "int", "prod", "prod"]
    assert all("keycloak secrets" not in c["argv"][-1] for c in ssh)
    assert " update && " in ssh[1]["argv"][-1]
    (tmp_path / "calls").unlink()
    result, calls = run_script(tmp_path, "status")
    assert [target(c) for c in calls] == ["prod", "int", "dev"]
    assert all(c["argv"][-1].endswith("ifdd-meta.yaml status") for c in calls)


def test_dry_run_prints_what_it_would_run_and_runs_nothing(tmp_path):
    result, calls = run_script(tmp_path, "--dry-run", "setup")
    assert result.returncode == 0, result.stderr
    assert calls == []
    lines = result.stdout.splitlines()
    assert len(lines) == 11 and all(line.startswith("+ ") for line in lines)
    assert sum(" | ssh " in line for line in lines) == 2  # the two secret pipes
    real, _ = run_script(tmp_path, "setup")
    assert real.returncode == 0


@pytest.mark.parametrize(
    ("args", "message"),
    [((), "usage:"), (("deploy",), "usage:"), (("setup", "qa"), "unknown stage: qa")],
)
def test_wrong_arguments(tmp_path, args, message):
    result, calls = run_script(tmp_path, "--dry-run", *args)
    assert result.returncode == 2
    assert message in result.stderr
    assert calls == []


@pytest.mark.parametrize("stage", STAGES)
def test_stages_use_the_release_of_the_demo(installations, stage):
    """One release for every template: the ref of the demo, no ref per service."""
    demo = load(Path(__file__).parents[2] / "config" / "demo" / "installation.yaml")
    description = load(installations / stage / "installation.yaml")
    assert description.ref == demo.ref
    assert description.refs == {}
