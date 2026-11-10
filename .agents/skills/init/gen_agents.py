#!/usr/bin/env python3
"""OntoShip core — generate zcode agent definitions from the role registry.

usage: skills/init/gen_agents.py <project>

The generator travels inside the init skill bundle, so an installed project
runs it from the skill-bundle root: .agents/skills/init/gen_agents.py
(installer and marketplace plugin since v0.2.0). The registry is
<project>/.agents/config.toml: one `[agents.<name>]` section
per role with the required key `model` and the optional key `level`. The
generator keeps exactly one
definition per role in <project>/.zcode/agents/<name>.md with `model:` and,
when `level` is set, `thoughtLevel:` taken
from the config; a role without `level` gets no such line. The level value is
not validated against a catalogue of known levels — an unknown value passes
through to the launch-time check; a non-string, blank or non-token `level`
(a bare token: letters, digits, '.', '_', '-', the same shape as a role name)
is invalid. A definition whose content differs from the rendered one —
a changed model above all — is reported (old and new model, unquoted) and
rewritten; unchanged definitions are left untouched, so a rerun with an
unchanged config never rewrites files. No config file means nothing to do and
nothing written. Each definition names the generator that ran it: relative
to the project inside it, absolute when run from elsewhere.

A file already sitting on a definition path but carrying no generated marker
is foreign: the run refuses to overwrite it (exit 1, the file named) before
writing anything. The marker heuristic treats any file quoting the marker
sentence as generated — a hand-written file that quotes it is still clobbered.

Validation distinguishes unknown from invalid: unknown sections or keys warn
on stderr and do not fail; a role without `model`, a non-string or empty
model, a non-string or blank `level`, an unusable role name, a non-table role
section and broken TOML fail
with a named error (exit 1). Stdlib only (tomllib since Python 3.11).
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MARKER = "Определение производное"


class ConfigError(Exception):
    """Invalid config — refuse with a named error."""


def parse_config(raw: bytes) -> tuple[dict[str, tuple[str, str | None]], list[str]]:
    """Return {role: (model, level)} and the warnings for unknown sections or keys.

    The level is `None` when the section omits `level`. Raise ConfigError on
    anything invalid: broken TOML, a non-table `agents`
    or role section, a role without `model`, a non-string or empty model, a
    non-string or blank `level`, a `level` that is not a bare token, an
    unusable role name.
    """
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"broken TOML: {exc}") from exc

    warnings: list[str] = []
    for key, value in data.items():
        if key == "agents":
            continue
        kind = "section" if isinstance(value, dict) else "key"
        warnings.append(f"unknown {kind} {key!r} ignored")

    roles: dict[str, tuple[str, str | None]] = {}
    agents = data.get("agents")
    if agents is None:
        return roles, warnings
    if not isinstance(agents, dict):
        raise ConfigError("[agents] must be a table of role sections")

    for name, section in agents.items():
        if not isinstance(section, dict):
            raise ConfigError(f"[agents.{name}] must be a table")
        if not NAME_RE.match(name):
            raise ConfigError(
                f"invalid role name {name!r}: use letters, digits, '.', '_' or '-'"
            )
        if "model" not in section:
            raise ConfigError(f"[agents.{name}]: required key 'model' is missing")
        model = section["model"]
        if not isinstance(model, str) or not model.strip():
            raise ConfigError(f"[agents.{name}]: 'model' must be a non-empty string")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in model):
            raise ConfigError(
                f"[agents.{name}]: 'model' must not contain control characters"
            )
        level = section.get("level")
        if level is not None:
            if not isinstance(level, str) or not level.strip():
                raise ConfigError(
                    f"[agents.{name}]: 'level' must be a non-empty string when present"
                )
            if not NAME_RE.match(level):
                raise ConfigError(
                    f"[agents.{name}]: 'level' must be a bare token: letters, "
                    "digits, '.', '_', '-'"
                )
        for key in section:
            if key not in ("model", "level"):
                warnings.append(f"[agents.{name}]: unknown key {key!r} ignored")
        roles[name] = (model, level)
    return roles, warnings


def self_ref(project: Path) -> str:
    """This generator as a definition should name it."""
    gen = Path(__file__).resolve()
    try:
        return str(gen.relative_to(project.resolve()))
    except ValueError:
        return str(gen)


def render(name: str, model: str, level: str | None, gen_ref: str) -> str:
    """One definition: frontmatter carries the configured model and level."""
    quoted = model.replace("\\", "\\\\").replace('"', '\\"')
    level_line = f'thoughtLevel: {level}\n' if level else ""
    return (
        "---\n"
        f'description: Роль OntoShip "{name}".\n'
        f'model: "{quoted}"\n'
        + level_line
        + "---\n"
        "\n"
        f"# {name}\n"
        "\n"
        "Роль OntoShip. Модель назначена конфигом `.agents/config.toml`\n"
        f"(секция `[agents.{name}]`, ключ `model`); канал запуска роли назначен\n"
        "той же секцией конфига. Определение производное — перегенерируется\n"
        f"`{gen_ref}`, не редактируется вручную.\n"
    )


def current_model(path: Path) -> str | None:
    """The `model:` value of an existing definition's frontmatter.

    The two backslash escapes the renderer emits are folded back; any other
    escape passes through unchanged.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        if line.startswith("model:"):
            value = line[len("model:"):].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            return re.sub(r"\\(.)", r"\1", value)
    return None


def sync(project: Path) -> int:
    config = project / ".agents" / "config.toml"
    if not config.is_file():
        print(f"no {config.relative_to(project)} — nothing to generate")
        return 0

    try:
        roles, warnings = parse_config(config.read_bytes())
    except ConfigError as exc:
        print(f"gen_agents: {config.relative_to(project)}: {exc}", file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"gen_agents: warning: {warning}", file=sys.stderr)

    agents_dir = project / ".zcode" / "agents"
    gen_ref = self_ref(project)
    plan = []
    for name in sorted(roles):
        model, level = roles[name]
        plan.append(
            (name, agents_dir / f"{name}.md", render(name, model, level, gen_ref))
        )

    conflicts = []
    for _, target, expected in plan:
        if not target.is_file():
            continue
        current = target.read_text(encoding="utf-8")
        if current != expected and MARKER not in current:
            conflicts.append(target)
    if conflicts:
        for target in conflicts:
            print(
                f"gen_agents: {target.relative_to(project)}: not a generated "
                "definition — refusing to overwrite; rename or remove it",
                file=sys.stderr,
            )
        return 1

    for name, target, expected in plan:
        model, _ = roles[name]
        if target.is_file() and target.read_text(encoding="utf-8") == expected:
            print(f"{name}: ok")
            continue
        if not target.exists():
            print(f"{name}: created")
        else:
            old = current_model(target)
            if old is not None and old != model:
                print(f"{name}: model {old} -> {model} — regenerated")
            else:
                print(f"{name}: content differs — regenerated")
        try:
            if not agents_dir.is_dir():
                agents_dir.mkdir(parents=True)
            target.write_text(expected, encoding="utf-8")
        except OSError as exc:
            print(f"gen_agents: cannot write {target}: {exc}", file=sys.stderr)
            return 1
    print(f"{len(roles)} role(s) in sync")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print("usage: skills/init/gen_agents.py <project>", file=sys.stderr)
        return 2
    project = Path(argv[1])
    if not project.is_dir():
        print(f"gen_agents: no such project directory: {project}", file=sys.stderr)
        return 1
    return sync(project)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
