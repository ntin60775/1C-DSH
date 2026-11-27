#!/usr/bin/env python3
"""Reconcile a project role registry with the delivery matrix.

The delivery matrix lives in this bundle (`roles.py`: ROLE_ROUTES,
POOL_ROUTES) and equals the project registry of the OntoShip repository
itself. A consumer's `.agents/config.toml` carries the same mechanics: every
core role section gets the matrix route and level, the `[validation]` pool
gets the matrix routes. Knowledge — project pack roles, unknown sections,
comments, ordering — is preserved byte for byte.

Usage:
    python3 sync_config.py <config.toml>          # rewrite, print changes
    python3 sync_config.py --check <config.toml>  # exit 1 on drift, no write

Exit codes: 0 — reconciled or already equal; 1 — drift under --check,
unreadable input or broken section; 2 — usage error.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from roles import POOL_ROUTES, ROLE_ROUTES  # noqa: E402

USAGE = "usage: python3 sync_config.py [--check] <config.toml>"


def _section_bounds(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Section header -> (header index, end index); end is the next header."""
    heads = [(index, line.strip()[1:-1].strip())
             for index, line in enumerate(lines)
             if line.lstrip().startswith("[") and line.rstrip().endswith("]")]
    bounds: dict[str, tuple[int, int]] = {}
    for position, (index, name) in enumerate(heads):
        end = heads[position + 1][0] if position + 1 < len(heads) else len(lines)
        bounds[name] = (index, end)
    return bounds


def _set_key(lines: list[str], start: int, end: int, key: str, value: str) -> list[str]:
    """Set `key = "value"` inside lines[start:end]; append when absent."""
    for index in range(start + 1, end):
        if lines[index].strip().startswith(f"{key} ="):
            indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
            lines[index] = f'{indent}{key} = "{value}"\n'
            return lines
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, f'{key} = "{value}"\n')
    return lines


def _pool_block() -> list[str]:
    block = ["pool = [\n"]
    block += [f'  {{ model = "{model}", level = "{level}" }},\n'
              for model, level in POOL_ROUTES]
    block.append("]\n")
    return block


def _replace_pool(lines: list[str], start: int, end: int) -> list[str]:
    """Replace the `pool = [...]` block in [validation]; append when absent."""
    first = None
    for index in range(start + 1, end):
        if lines[index].strip().startswith("pool ="):
            first = index
            break
    if first is None:
        insert_at = end
        while insert_at > start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines[insert_at:insert_at] = _pool_block()
        return lines
    last = first
    if "[" in lines[first]:
        while last < len(lines) and "]" not in lines[last]:
            last += 1
    lines[first:last + 1] = _pool_block()
    return lines


def reconcile(text: str) -> tuple[str, list[str]]:
    """Return the reconciled text and the list of applied changes."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    changes: list[str] = []

    bounds = _section_bounds(lines)
    for role, model, level in ROLE_ROUTES:
        name = f"agents.{role}"
        if name not in bounds:
            lines += ["\n", f"[{name}]\n", f'model = "{model}"\n', f'level = "{level}"\n']
            changes.append(f"{role}: section added")
        else:
            start, end = bounds[name]
            before = lines[start:end]
            lines = _set_key(lines, start, end, "model", model)
            start, end = _section_bounds(lines)[name]
            lines = _set_key(lines, start, end, "level", level)
            if lines[start:end] != before:
                changes.append(f"{role}: route set to {model}/{level}")

    bounds = _section_bounds(lines)
    if "validation" not in bounds:
        lines += ["\n", "[validation]\n"] + _pool_block()
        changes.append("validation: section added")
    else:
        start, end = bounds["validation"]
        before = lines[start:end]
        lines = _replace_pool(lines, start, end)
        if lines[start:_section_bounds(lines)["validation"][1]] != before:
            changes.append("validation: pool set to the delivery matrix")

    return "".join(lines), changes


def main(argv: list[str]) -> int:
    args = [arg for arg in argv[1:] if not arg.startswith("-")]
    check = "--check" in argv[1:]
    if len(args) != 1 or any(arg not in ("--check",) for arg in argv[1:] if arg.startswith("-")):
        print(USAGE, file=sys.stderr)
        return 2
    path = Path(args[0])
    try:
        original = path.read_text("utf-8")
    except OSError as exc:
        print(f"sync_config: cannot read {path}: {exc}", file=sys.stderr)
        return 1
    try:
        reconciled, changes = reconcile(original)
    except (KeyError, IndexError) as exc:
        print(f"sync_config: broken registry {path}: {exc}", file=sys.stderr)
        return 1
    if not changes:
        print(f"sync_config: {path} already matches the delivery matrix")
        return 0
    for change in changes:
        print(f"sync_config: {change}")
    if check:
        print(f"sync_config: {path} drifts from the delivery matrix", file=sys.stderr)
        return 1
    path.write_text(reconciled, "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))