"""``ifdd-meta local-setup``: the machine-wide steps of a test installation.

The host names go into ``/etc/hosts`` and the local CA into the trust store of
the operating system. Both need root, so ifdd-meta never runs them: it prints
the commands with the paths of this installation, for the operator to run.
``--remove`` prints the commands that undo them; run those before
``down --purge``, which deletes the CA file.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import ssl
import sys
from pathlib import Path

from .compiler import HOSTS_FILE, hosts_markers
from .create import CreateError, load_state
from .settings import Settings

ETC_HOSTS = Path("/etc/hosts")
KEYCHAIN = "/Library/Keychains/System.keychain"
LINUX_CA_DIRECTORY = "/usr/local/share/ca-certificates"
_NAME = re.compile(r"[A-Za-z0-9_-]+")


class LocalSetupError(CreateError):
    """The commands cannot be put together."""


def commands(
    settings: Settings,
    remove: bool = False,
    platform: str = sys.platform,
    etc_hosts: Path = ETC_HOSTS,
) -> list[str]:
    """Shell lines (comments and commands) for this machine; nothing is changed."""
    installation = settings["installation"]
    if not _NAME.fullmatch(installation):
        raise LocalSetupError(f"installation name {installation!r}: letters, digits, - and _ only")
    if settings.file is None:
        raise LocalSetupError("no settings file; the hosts file lies next to it")
    lines = _hosts(settings.file.parent / HOSTS_FILE, installation, remove, platform, etc_hosts)
    if settings["tls"] == "local-ca":
        lines += _ca(settings, installation, remove, platform)
    else:
        lines += [f"# TLS mode {settings['tls']}: no local CA to trust"]
    return lines


def _hosts(hosts: Path, installation: str, remove: bool, platform: str, etc_hosts: Path):
    begin, end = hosts_markers(installation)
    if not hosts.is_file():
        raise LocalSetupError(f"{hosts} missing; run 'ifdd-meta compile' first")
    if begin not in hosts.read_text(encoding="utf-8").splitlines():
        return [f"# {hosts} lists DNS records (public names): create them in the DNS"]
    present = etc_hosts.is_file() and begin in etc_hosts.read_text(encoding="utf-8").splitlines()
    if remove:
        if not present:
            return [f"# {etc_hosts} holds no block of {installation}"]
        pattern = f"/^{begin}$/,/^{end}$/d"
        in_place = ["-i", ""] if platform == "darwin" else ["-i"]
        command = ["sudo", "sed", *in_place, pattern, str(etc_hosts)]
        return [f"# Remove the host names of {installation} from {etc_hosts}", shlex.join(command)]
    if present:
        return [f"# {etc_hosts} holds the block of {installation} already"]
    return [
        f"# Host names of {installation} into {etc_hosts}",
        f"sudo tee -a {shlex.quote(str(etc_hosts))} < {shlex.quote(str(hosts))} > /dev/null",
    ]


def _ca(settings: Settings, installation: str, remove: bool, platform: str) -> list[str]:
    state = load_state(settings)
    if "proxy" not in state:
        return ["# No proxy instance yet: run 'ifdd-meta create', then local-setup again"]
    ca = settings["workdir"] / state["proxy"] / "certs" / "ca.crt"
    if not ca.is_file():
        return [f"# {ca} missing: the local CA comes with 'ifdd-meta create'"]
    quoted = shlex.quote(str(ca))
    if platform == "darwin":
        if remove:
            return [
                "# Remove the local CA from the System keychain",
                f"sudo security remove-trusted-cert -d {quoted}",
                f"sudo security delete-certificate -Z {sha1(ca)} {KEYCHAIN}",
            ]
        return [
            "# Trust the local CA in the System keychain (browsers, curl, ifdd-meta test)",
            f"sudo security add-trusted-cert -d -r trustRoot -k {KEYCHAIN} {quoted}",
        ]
    if platform.startswith("linux"):
        target = f"{LINUX_CA_DIRECTORY}/ifdd-meta-{installation}.crt"
        if remove:
            return [
                "# Remove the local CA from the system store (Debian, Ubuntu)",
                f"sudo rm -f {target} && sudo update-ca-certificates --fresh",
            ]
        return [
            "# Trust the local CA in the system store (Debian, Ubuntu); browsers on Linux",
            "# use their own store, ifdd-meta test sets up its browser itself",
            f"sudo cp {quoted} {target} && sudo update-ca-certificates",
        ]
    return [f"# No commands for {platform}: trust {ca} by hand"]


def sha1(ca: Path) -> str:
    """SHA-1 fingerprint of a PEM certificate, as 'security delete-certificate -Z' takes it."""
    der = ssl.PEM_cert_to_DER_cert(ca.read_text(encoding="ascii"))
    return hashlib.sha1(der, usedforsecurity=False).hexdigest().upper()
