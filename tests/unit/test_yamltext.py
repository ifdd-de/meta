import pytest
import yaml

from ifdd_meta import compiler, yamltext

SAMPLE = """# head
default_context:

  # a comment
  a: "x"
  # a choice
  b:
    - "one"
    - "two"
  c: >-
    folded
    text
"""


def test_parse_keeps_comments_and_finds_keys():
    document = yamltext.parse(SAMPLE)
    assert document.keys == ["a", "b", "c"]


def test_render_replaces_values_and_keeps_comments():
    document = yamltext.parse(SAMPLE)
    text = yamltext.render(
        document, {"a": 'say "hi"', "b": ["two", "one"], "c": "short"}, ["default_context:"]
    )
    assert "  # a comment" in text and "  # a choice" in text
    assert yaml.safe_load(text)["default_context"] == {
        "a": 'say "hi"',
        "b": ["two", "one"],
        "c": "short",
    }
    assert "  a: 'say \"hi\"'" in text


def test_long_policies_are_folded_but_json_is_not():
    policy = "default-src 'self'; " * 8
    lines = yamltext.emit("p", policy.strip())
    assert lines[0] == "  p: >-" and len(lines) > 2
    json_value = '{"products": "product", ' * 5 + '"x": "y"}'
    assert len(yamltext.emit("j", json_value)) == 1


def test_render_requires_a_value_for_every_key():
    document = yamltext.parse(SAMPLE)
    with pytest.raises(yamltext.LayoutError, match="missing"):
        yamltext.render(document, {"a": "x", "b": ["one"]}, ["default_context:"])


@pytest.mark.parametrize(
    "text",
    ["no context line\n", "default_context:\nfoo: bar\n", 'default_context:\n  a: "1"\n  a: "2"\n'],
)
def test_layout_errors(text):
    with pytest.raises(yamltext.LayoutError):
        yamltext.parse(text)


def test_defaults_render_unchanged_values():
    """The packaged defaults survive a round trip through parse and render."""
    text = compiler.defaults_text()
    values = yaml.safe_load(text)["default_context"]
    rendered = yamltext.render(yamltext.parse(text), values, ["default_context:"])
    assert yaml.safe_load(rendered)["default_context"] == values
