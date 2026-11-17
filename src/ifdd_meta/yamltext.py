"""Rewrite the values of a commented variables file and keep its comments.

The file has the layout of ``data/defaults.yaml``: comment lines, a line
``default_context:`` and below it one entry per variable, indented by two
spaces. An entry is a key line, optionally followed by lines indented by four
spaces (a block list or a folded string). PyYAML would drop the comments, so the
values are replaced line by line instead, and the result is parsed again to
make sure it carries exactly the intended values.
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass

import yaml

_KEY = re.compile(r"^  ([a-z][a-z0-9_]*):(.*)$")
_FOLD_WIDTH = 76
_LONG = 90


class LayoutError(ValueError):
    """The text does not have the expected layout."""


@dataclass
class Document:
    head: list[str]  # lines up to and including "default_context:"
    parts: list[tuple[str | None, list[str]]]  # (key or None for comments, lines)

    @property
    def keys(self) -> list[str]:
        return [key for key, _ in self.parts if key is not None]


def parse(text: str) -> Document:
    lines = text.splitlines()
    try:
        start = lines.index("default_context:")
    except ValueError:
        raise LayoutError("no line 'default_context:'") from None
    parts: list[tuple[str | None, list[str]]] = []
    for line in lines[start + 1 :]:
        match = _KEY.match(line)
        if match:
            parts.append((match.group(1), [line]))
        elif line.startswith("    ") and parts and parts[-1][0] is not None:
            parts[-1][1].append(line)
        elif not line.strip() or line.startswith("  #"):
            parts.append((None, [line]))
        else:
            raise LayoutError(f"unexpected line: {line!r}")
    keys = [key for key, _ in parts if key is not None]
    if len(keys) != len(set(keys)):
        raise LayoutError("a variable appears twice")
    return Document(head=lines[: start + 1], parts=parts)


def render(document: Document, values: dict[str, str | list[str]], head: list[str]) -> str:
    """Return the text with ``values``; every key of the document must have one."""
    missing = set(document.keys) - set(values)
    extra = set(values) - set(document.keys)
    if missing or extra:
        raise LayoutError(f"values do not match the keys: missing {missing}, extra {extra}")
    out = list(head)
    for key, lines in document.parts:
        out.extend(lines if key is None else emit(key, values[key]))
    text = "\n".join(out) + "\n"
    parsed = yaml.safe_load(text)["default_context"]
    if parsed != values:
        wrong = sorted(k for k in values if parsed.get(k) != values[k])
        raise LayoutError(f"rendered values differ for: {', '.join(wrong)}")
    return text


def emit(key: str, value: str | list[str]) -> list[str]:
    """Lines for one entry."""
    if isinstance(value, list):
        return [f"  {key}:"] + [f"    - {_scalar(item)}" for item in value]
    if not isinstance(value, str):
        raise LayoutError(f"{key}: values must be text or lists of text")
    if _foldable(value):
        wrapped = textwrap.wrap(
            value, width=_FOLD_WIDTH, break_long_words=False, break_on_hyphens=False
        )
        return [f"  {key}: >-"] + [f"    {line}" for line in wrapped]
    return [f"  {key}: {_scalar(value)}"]


def _foldable(value: str) -> bool:
    """Long lists and policies are folded; JSON and text without spaces are not."""
    return (
        len(value) > _LONG
        and " " in value
        and "\n" not in value
        and "  " not in value
        and value == value.strip()
        and value[0] not in "{["
    )


def _scalar(value: str) -> str:
    if '"' in value and "'" not in value:
        return f"'{value}'"
    return json.dumps(value, ensure_ascii=False)
