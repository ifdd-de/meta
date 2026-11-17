import pytest

from ifdd_meta import envfile


def test_parse_reads_plain_quoted_and_exported_values():
    text = """
# comment
A=1
export B = two
C='three four'
D="five"
E=
F=$HOME
"""
    assert envfile.parse(text) == {
        "A": "1",
        "B": "two",
        "C": "three four",
        "D": "five",
        "E": "",
        "F": "$HOME",
    }


def test_later_lines_win():
    assert envfile.parse("A=1\nA=2\n") == {"A": "2"}


@pytest.mark.parametrize("line", ["no separator", "1A=x", "=x", "A B=x"])
def test_parse_rejects_malformed_lines(line):
    with pytest.raises(envfile.EnvFileError, match=":1: expected KEY=VALUE"):
        envfile.parse(line)


def test_read_missing_file_is_empty(tmp_path):
    assert envfile.read(tmp_path / ".env") == {}
