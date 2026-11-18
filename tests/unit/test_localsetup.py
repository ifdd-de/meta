"""local-setup prints the sudo commands; the Linux ones are run here without sudo."""

import json
import shlex
import subprocess

import pytest

from ifdd_meta import compiler, localsetup
from ifdd_meta.create import STATE_DIR, STATE_FILE
from ifdd_meta.description import load as load_description
from ifdd_meta.settings import load


@pytest.fixture
def ca_file(tmp_path_factory):
    """A real self-signed CA (openssl), and its SHA-256 fingerprint from openssl."""
    directory = tmp_path_factory.mktemp("ca")
    ca = directory / "ca.crt"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-nodes", "-keyout", str(directory / "ca.key"), "-out", str(ca), "-subj", "/CN=test",
         "-days", "1"],
        check=True, capture_output=True,
    )  # fmt: skip
    out = subprocess.run(
        ["openssl", "x509", "-in", str(ca), "-noout", "-fingerprint", "-sha256"],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return ca, out.strip().split("=", 1)[1].replace(":", "")


def installation(tmp_path, ca=None, tls="local-ca", suffix="demo.test"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    description = tmp_path / "installation.yaml"
    description.write_text(
        f"installation: notebook\nhosts:\n  suffix: {suffix}\ntls: {tls}\n"
        + ("contact: ops@example.org\n" if tls == "letsencrypt" else "")
    )
    compiler.write(compiler.compile_description(load_description(description)), tmp_path)
    if ca is not None:
        work = tmp_path / "work"
        (work / STATE_DIR).mkdir(parents=True)
        (work / STATE_DIR / STATE_FILE).write_text(json.dumps({"proxy": "proxy"}))
        (work / "proxy" / "certs").mkdir(parents=True)
        (work / "proxy" / "certs" / "ca.crt").write_bytes(ca.read_bytes())
    return load(cwd=tmp_path, environ={})


def commands(lines):
    return [line for line in lines if not line.startswith("#")]


def run_without_sudo(line: str) -> None:
    subprocess.run(["bash", "-c", line.replace("sudo ", "")], check=True)


def test_linux_hosts_block_added_once_and_removed(tmp_path, ca_file):
    settings = installation(tmp_path, ca_file[0])
    hosts = tmp_path / "etc-hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    add = commands(localsetup.commands(settings, platform="linux", etc_hosts=hosts))
    run_without_sudo(add[0])
    text = hosts.read_text()
    assert "127.0.0.1 localhost\n" in text and "\n127.0.0.1 id.demo.test rest.demo.test" in text
    again = localsetup.commands(settings, platform="linux", etc_hosts=hosts)
    assert again[0].endswith("holds the block of notebook already")
    remove = commands(localsetup.commands(settings, True, platform="linux", etc_hosts=hosts))
    run_without_sudo(remove[0])
    assert hosts.read_text() == "127.0.0.1 localhost\n"  # exactly as before
    # the CA: copied under the name of the installation, removed again
    assert add[1].endswith("ifdd-meta-notebook.crt && sudo update-ca-certificates")
    assert remove[1].startswith(
        "sudo rm -f /usr/local/share/ca-certificates/ifdd-meta-notebook.crt"
    )


def test_macos_commands(tmp_path, ca_file):
    ca, fingerprint = ca_file
    settings = installation(tmp_path, ca)
    hosts = tmp_path / "etc-hosts"
    hosts.write_text("")
    add = commands(localsetup.commands(settings, platform="darwin", etc_hosts=hosts))
    path = shlex.quote(str(tmp_path / "work" / "proxy" / "certs" / "ca.crt"))
    assert add[1] == (
        f"sudo security add-trusted-cert -d -r trustRoot -k {localsetup.KEYCHAIN} {path}"
    )
    hosts.write_text((tmp_path / "hosts.txt").read_text())
    remove = commands(localsetup.commands(settings, True, platform="darwin", etc_hosts=hosts))
    assert remove[0].startswith("sudo sed -i '' ")  # BSD sed: -i takes a suffix
    assert remove[1] == f"sudo security remove-trusted-cert -d {path}"
    assert remove[2] == f"sudo security delete-certificate -Z {fingerprint} {localsetup.KEYCHAIN}"


def test_before_create_and_without_a_local_ca(tmp_path):
    lines = localsetup.commands(installation(tmp_path), platform="linux", etc_hosts=tmp_path / "x")
    assert lines[-1].startswith("# No proxy instance yet")
    settings = installation(tmp_path / "le", tls="letsencrypt", suffix="a.example.org")
    lines = localsetup.commands(settings, platform="darwin", etc_hosts=tmp_path / "x")
    hosts = tmp_path / "le" / "hosts.txt"
    assert lines == [
        f"# {hosts} lists DNS records (public names): create them in the DNS",
        "# TLS mode letsencrypt: no local CA to trust",
    ]


def test_needs_the_compiled_hosts_file(tmp_path):
    (tmp_path / "ifdd-meta.yaml").write_text("installation: x\n")
    with pytest.raises(localsetup.LocalSetupError, match="hosts.txt missing"):
        localsetup.commands(load(cwd=tmp_path, environ={}))
