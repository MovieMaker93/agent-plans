# SPDX-License-Identifier: Apache-2.0
"""YAML load (PyYAML) and a tiny deterministic dump for generated files.

Hand-written plans keep their own comments. Generated files go through
`dump_yaml` so a second run produces the same bytes.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

# Plain scalars an agent can read without quotes. Anything with a colon,
# space, or leading digit stays quoted so the text cannot change meaning.
_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_RESERVED = {"true", "false", "null", "yes", "no", "on", "off"}

from planner.util import fmt_hours

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "PyYAML is required. Install it with: pip install -r requirements.txt"
    ) from exc


class YamlError(Exception):
    pass


def load_yaml(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise YamlError(f"cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise YamlError(f"{path}: YAML parse error: {exc}") from exc
    if data is None:
        raise YamlError(f"{path}: file is empty")
    return data


def _is_block(value) -> bool:
    if isinstance(value, dict):
        return len(value) > 0
    if isinstance(value, list):
        return len(value) > 0
    return False


def _scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, Decimal):
        return fmt_hours(value)
    if isinstance(value, float):
        return fmt_hours(Decimal(str(value)))
    if isinstance(value, str):
        if _PLAIN.match(value) and value.lower() not in _RESERVED:
            return value
        escaped = (
            value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        )
        return f'"{escaped}"'
    if isinstance(value, list) and not value:
        return "[]"
    if isinstance(value, dict) and not value:
        return "{}"
    raise TypeError(f"cannot encode {type(value).__name__}")


def _lines(value, indent: int) -> list[str]:
    pad = "  " * indent
    if isinstance(value, list):
        return _list_lines(value, indent)
    if isinstance(value, dict):
        out = []
        for key, child in value.items():
            name = str(key)
            if _is_block(child):
                out.append(f"{pad}{name}:")
                out.extend(_lines(child, indent + 1))
            else:
                out.append(f"{pad}{name}: {_scalar(child)}")
        return out
    return [f"{pad}{_scalar(value)}"]


def _list_lines(items: list, indent: int) -> list[str]:
    pad = "  " * indent
    out = []
    for child in items:
        if isinstance(child, dict):
            pairs = list(child.items())
            if not pairs:
                out.append(f"{pad}- {{}}")
                continue
            first_key, first_val = pairs[0]
            if _is_block(first_val):
                out.append(f"{pad}- {first_key}:")
                out.extend(_lines(first_val, indent + 2))
            else:
                out.append(f"{pad}- {first_key}: {_scalar(first_val)}")
            for key, val in pairs[1:]:
                if _is_block(val):
                    out.append(f"{pad}  {key}:")
                    out.extend(_lines(val, indent + 2))
                else:
                    out.append(f"{pad}  {key}: {_scalar(val)}")
        elif _is_block(child):
            out.append(f"{pad}-")
            out.extend(_lines(child, indent + 1))
        else:
            out.append(f"{pad}- {_scalar(child)}")
    return out


def dump_yaml(value) -> str:
    if not isinstance(value, (dict, list)):
        return _scalar(value) + "\n"
    body = _lines(value, 0)
    return "\n".join(body) + "\n"
