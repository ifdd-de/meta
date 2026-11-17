import base64
import stat

import pytest

from ifdd_meta import copysecret


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_read_env_takes_the_last_value_and_unquotes(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=old\nexport A='new'\nB=\n")
    assert copysecret.read_env(env, "A") == "new"
    with pytest.raises(copysecret.SecretError, match="B is empty"):
        copysecret.read_env(env, "B")
    with pytest.raises(copysecret.SecretError, match="C missing"):
        copysecret.read_env(env, "C")
    with pytest.raises(copysecret.SecretError, match="source missing"):
        copysecret.read_env(tmp_path / "none", "A")


def test_write_file_new_unchanged_and_existing_mode(tmp_path):
    path = tmp_path / "secret"
    assert copysecret.write_file(path, "s3cret") is True
    assert path.read_text() == "s3cret" and mode(path) == 0o644
    assert copysecret.write_file(path, "s3cret") is False
    path.chmod(0o600)
    assert copysecret.write_file(path, "other") is True
    assert mode(path) == 0o600
    with pytest.raises(copysecret.SecretError, match="directory missing"):
        copysecret.write_file(tmp_path / "no" / "secret", "x")


def test_write_env_replaces_only_its_line(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nA=\nB=keep\n")
    env.chmod(0o600)
    assert copysecret.write_env(env, "A", "v1") is True
    assert env.read_text() == "# comment\nA=v1\nB=keep\n"
    assert mode(env) == 0o600
    assert copysecret.write_env(env, "A", "v1") is False
    with pytest.raises(copysecret.SecretError, match="variable C missing"):
        copysecret.write_env(env, "C", "v")
    with pytest.raises(copysecret.SecretError, match="would need quotes"):
        copysecret.write_env(env, "A", "two words")


def test_write_env_kestra_is_base64(tmp_path):
    env = tmp_path / "kestra.env"
    env.write_text("SECRET_X=\n")
    copysecret.write_env(env, "X", "value", kestra=True)
    encoded = env.read_text().strip().split("=", 1)[1]
    assert base64.b64decode(encoded).decode() == "value"


def test_errors_never_contain_the_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=\n")
    with pytest.raises(copysecret.SecretError) as error:
        copysecret.write_env(env, "A", "top secret")
    assert "top secret" not in str(error.value)
