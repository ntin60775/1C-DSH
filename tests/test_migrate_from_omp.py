#!/usr/bin/env python3
"""Тесты migrate_from_omp.py: прогон как процесса на fake-omp-проекте.

Мигратор гоняется в tmp-проекте с git-историей (tracked-файлы обязательны —
шаги делают git rm/mv). HOME подменяется, чтобы шаг 1 не трогал реальный
~/.omp. Запуск: python3 tests/test_migrate_from_omp.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MIGRATE = REPO / "scripts" / "migrate_from_omp.py"

V8_LEGACY = """\
name: fakeproj
builder: ibcmd
infobase:
  connection: File=/tmp/fake1c
tests:
  yaxunit:
    source-set: TestExt
  va:
    features: features
  execution_timeout_seconds: 300
va:
  path: tools/vanessa
"""

AGENTS_FAKE = """\
# Проект

Правила агента для репо. Разовый обход гейта — через список эскалаций.

<!-- KB (ontoship) -->
База знаний проекта, ведётся онтоКБ, контуром не владеется.
<!-- /KB -->
"""

INIT_SH = """\
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "$HOME/.omp/plugins/1c-omp/skills/1c-project-bootstrap/scripts/init-worktree.sh" "$@"
"""


def make_project(root: Path, vendored: bool = True):
	"""Fake-omp-проект со всеми артефактами старого контура."""
	root.mkdir(parents=True)
	(root / "v8project.yaml").write_text(V8_LEGACY, encoding="utf-8")
	(root / "AGENTS.md").write_text(AGENTS_FAKE, encoding="utf-8")
	(root / ".gitignore").write_text(".omp/plugins/\n.omp/backup/\nnode_modules/\n",
	                                 encoding="utf-8")
	omp = root / ".omp"
	(omp / "rules").mkdir(parents=True)
	(omp / "rules" / "style.md").write_text("# Стиль\nКод только в src/.\n",
	                                        encoding="utf-8")
	(omp / "unica-gate-escalations.txt").write_text(
		"2026-01-01 причина-обхода\n", encoding="utf-8")
	(omp / "contour").write_text("test\n", encoding="utf-8")
	(omp / "plugins").mkdir()
	(omp / "plugins" / "hidden.bin").write_bytes(b"\x00\x01")
	(root / "features" / "sub").mkdir(parents=True)
	(root / "features" / "one.feature").write_text(
		"# language: ru\nФункционал: А\nСценарий: б\n", encoding="utf-8")
	(root / "features" / "sub" / "two.feature").write_text(
		"# language: ru\nФункционал: В\n", encoding="utf-8")
	(root / "fixtures" / "шаблоны-фич").mkdir(parents=True)
	(root / "fixtures" / "шаблоны-фич" / "Шаблон.feature").write_text(
		"Функционал: болванка\n", encoding="utf-8")
	(root / "tools").mkdir()
	(root / "tools" / "VAParams.json").write_text("{}\n", encoding="utf-8")
	(root / "tasks").mkdir()
	(root / "tasks" / "init-worktree.sh").write_text(INIT_SH, encoding="utf-8")
	# вендоренный контур: настоящие wire_config/hooks/фрагменты + шаблон секции
	z = root / ".zcode"
	(z / "1c" / "scripts").mkdir(parents=True)
	(z / "1c" / "templates").mkdir(parents=True)
	if vendored:
		(z / "1c" / "scripts" / "wire_config.py").write_text(
			(REPO / "scripts" / "wire_config.py").read_text(encoding="utf-8"),
			encoding="utf-8")
		(z / "1c" / "scripts" / "mcp_fragments.json").write_text(
			(REPO / "scripts" / "mcp_fragments.json").read_text(encoding="utf-8"),
			encoding="utf-8")
		(z / "hooks").mkdir()
		(z / "hooks" / "hooks.json").write_text(
			(REPO / "reference" / "gates" / "hooks.json").read_text(encoding="utf-8"),
			encoding="utf-8")
		(z / "1c" / "templates" / "agents-section.md").write_text(
			(REPO / "templates" / "agents-section.md").read_text(encoding="utf-8"),
			encoding="utf-8")
	# вендоренный слой с легитимными упоминаниями — скан чистоты не должен
	# считать их остатком старого контура
	(z / "skills" / "1c-testpilot").mkdir(parents=True)
	(z / "skills" / "1c-testpilot" / "NOTE.md").write_text(
		"Канон: Vanessa-фичи не исполняются.\n", encoding="utf-8")
	# остаток старого контура вне вендоренного слоя — скан обязан найти
	(root / "docs").mkdir()
	(root / "docs" / "legacy-note.md").write_text(
		"Напоминание: yaxunit-расширение снести после переноса.\n",
		encoding="utf-8")
	git(root, "init", "-q", "-b", "main")
	git(root, "add", "-A")
	git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")


def git(root: Path, *args):
	return subprocess.run(["git", "-C", str(root), *args],
	                      capture_output=True, text=True)


def run_migrate(root: Path, home: Path, *flags):
	return subprocess.run(
		[sys.executable, str(MIGRATE), str(root), *flags],
		capture_output=True, text=True, cwd=str(root),
		env=dict(os.environ, HOME=str(home)))


def snapshot(root: Path):
	out = []
	for base, dirs, files in os.walk(root):
		dirs[:] = [d for d in dirs if d != ".git"]
		for f in files:
			out.append(str(Path(base, f).relative_to(root)))
	return sorted(out)


def main() -> int:
	tmp = Path(tempfile.mkdtemp(prefix="migrate-test-"))
	# fake-HOME: журнал аудита старого контура — шаг 1 обязан его заархивировать
	logs = tmp / ".omp" / "logs"
	logs.mkdir(parents=True)
	(logs / "rule-audit.jsonl").write_text(
		'{"rule":"doc-gate","ts":"2026-01-01"}\n', encoding="utf-8")

	# ── dry-run: план, ноль изменений ──
	p_dry = tmp / "proj-dry"
	make_project(p_dry)
	before = snapshot(p_dry)
	r = run_migrate(p_dry, tmp)
	check("dry-run: exit 0", r.returncode == 0, r.stdout[-300:] + r.stderr[-300:])
	check("dry-run: дерево не изменено", snapshot(p_dry) == before, "файлы изменились")
	check("dry-run: файловая ИБ без providers — подсказки о провайдере нет",
	      "серверная ИБ без providers" not in r.stdout, r.stdout[-300:])

	# ── префлайт: без вендоренного контура --apply прерывается до мутаций ──
	p_pre = tmp / "proj-preflight"
	make_project(p_pre)
	(p_pre / ".zcode" / "1c" / "templates" / "agents-section.md").unlink()
	(p_pre / ".zcode" / "1c" / "scripts" / "wire_config.py").unlink()
	before = snapshot(p_pre)
	r = run_migrate(p_pre, tmp, "--apply")
	check("префлайт: exit 1", r.returncode == 1, r.stdout[-300:])
	check("префлайт: дерево не изменено", snapshot(p_pre) == before, "файлы изменились")
	check("префлайт: причина названа", "не вендорен" in r.stdout, r.stdout[:300])

	# ── полный --apply ──
	p = tmp / "proj-full"
	make_project(p)
	r = run_migrate(p, tmp, "--apply")
	check("apply: exit 0", r.returncode == 0, r.stdout[-500:] + r.stderr[-300:])
	check("apply: .omp/ снесён", not (p / ".omp").exists(), "остался")
	check("apply: правила в архиве",
	      (p / "docs" / "omp-migrated" / "rules" / "style.md").is_file(), "нет")
	check("apply: фичи в архиве (корень)",
	      (p / "docs" / "omp-migrated" / "tests" / "features" / "one.feature").is_file(), "нет")
	check("apply: фичи в архиве (вложенные)",
	      (p / "docs" / "omp-migrated" / "tests" / "features" / "sub" / "two.feature").is_file(), "нет")
	check("apply: VAParams в архиве",
	      (p / "docs" / "omp-migrated" / "tests" / "VAParams.json").is_file(), "нет")
	check("apply: features/ снесён", not (p / "features").exists(), "остался")
	check("apply: tools/ снесён", not (p / "tools").exists(), "остался")
	check("apply: fixtures/ снесён", not (p / "fixtures").exists(), "остался")
	check("apply: эскалации перенесены",
	      (p / ".zcode" / "unica-gate-escalations.txt").read_text(encoding="utf-8")
	      .startswith("2026-01-01"), "нет")
	check("apply: архив аудита создан из fake-HOME",
	      (p / "docs" / "ops" / "gate-audit-archive.jsonl").is_file(), "нет")

	agents = (p / "AGENTS.md").read_text(encoding="utf-8")
	check("apply: секция контура вставлена",
	      "<!-- BEGIN 1C CONTOUR (1c-zcode) -->" in agents
	      and "<!-- END 1C CONTOUR -->" in agents, "нет маркеров")
	check("apply: KB-секция не тронута", "<!-- KB (ontoship) -->" in agents, "потеряна")

	v8 = (p / "v8project.yaml").read_text(encoding="utf-8")
	local = (p / "v8project.local.yaml").read_text(encoding="utf-8")
	check("apply: builder убран", "builder" not in v8, v8[:200])
	check("apply: привязка ИБ ушла из общего файла",
	      "infobase" not in v8 and "File=/tmp/fake1c" not in v8, v8[:300])
	check("apply: infobases.origin в local-слое",
	      "infobases:" in local and "origin:" in local
	      and "File=/tmp/fake1c" in local, local[:300])
	check("apply: tests-блок снят", "yaxunit" not in v8 and "execution_timeout_seconds" not in v8, v8[:300])
	check("apply: va-подблок снят", "vanessa" not in v8 and "\nva:" not in v8, v8[:300])
	check("apply: имя проекта живо", "name: fakeproj" in v8, v8[:200])
	check("apply: файловая ИБ — каркас providers: push: ibcmd",
	      "providers:" in v8 and "  push: ibcmd" in v8, v8[:400])

	# ── local с одним infobase.user: привязка из main переезжает и сливается ──
	p2 = tmp / "proj-local-user"
	make_project(p2)
	(p2 / "v8project.local.yaml").write_text(
		"infobase:\n  user: ТестовыйПользователь\n", encoding="utf-8")
	r = run_migrate(p2, tmp, "--apply")
	check("local user: apply exit 0", r.returncode == 0, r.stdout[-400:])
	v8_2 = (p2 / "v8project.yaml").read_text(encoding="utf-8")
	local_2 = (p2 / "v8project.local.yaml").read_text(encoding="utf-8")
	check("local user: connection переехал в infobases.origin",
	      "infobases:\n  origin:" in local_2 and "File=/tmp/fake1c" in local_2, local_2[:400])
	check("local user: user сохранён под origin",
	      "user: ТестовыйПользователь" in local_2, local_2[:400])
	check("local user: infobase в main убран", "infobase" not in v8_2, v8_2[:300])
	check("local user: ровно один origin, без дублей",
	      local_2.count("origin:") == 1, local_2[:400])

	gitignore = (p / ".gitignore").read_text(encoding="utf-8")
	check("apply: omp-блок .gitignore убран",
	      ".omp" not in gitignore and "node_modules/" in gitignore, gitignore)

	init_sh = (p / "tasks" / "init-worktree.sh").read_text(encoding="utf-8")
	check("apply: init-worktree перепривязан",
	      ".zcode/1c/scripts/init_worktree.sh" in init_sh and "1c-omp" not in init_sh,
	      init_sh[:200])

	config = json.loads((p / ".zcode" / "config.json").read_text(encoding="utf-8"))
	check("apply: config.json разведён wire'ом",
	      config.get("hooks", {}).get("enabled") is True
	      and "1c-testpilot" in config.get("mcp", {}).get("servers", {}), "нет")

	# разбираем именно секцию остаточных (в нотах шагов те же имена легальны)
	dirty_section = r.stdout.split("остаточные упоминания", 1)[-1].split("Осталось вручную", 1)[0]
	check("apply: скан чистоты — остаток найден",
	      "docs/legacy-note.md" in dirty_section, dirty_section[:400])
	check("apply: скан чистоты — вендоренный слой пропущен",
	      "NOTE.md" not in dirty_section, dirty_section[:400])
	check("apply: скан чистоты — секция AGENTS.md пропущена",
	      "AGENTS.md" not in dirty_section, dirty_section[:400])
	check("apply: скан чистоты — перенесённые правила не числятся",
	      "style.md" not in dirty_section, dirty_section[:400])

	# ── --test-stack-only: только шаг 5.5 ──
	p_t = tmp / "proj-stack"
	make_project(p_t)
	r = run_migrate(p_t, tmp, "--test-stack-only", "--apply")
	check("test-stack-only: exit 0", r.returncode == 0, r.stdout[-300:] + r.stderr[-200:])
	check("test-stack-only: .omp/ жив", (p_t / ".omp").is_dir(), "снесён")
	check("test-stack-only: фичи в архиве",
	      (p_t / "docs" / "omp-migrated" / "tests" / "features" / "one.feature").is_file(), "нет")
	check("test-stack-only: контур не разводился",
	      not (p_t / ".zcode" / "config.json").exists(), "появился")
	check("test-stack-only: AGENTS.md без секции",
	      "1C CONTOUR" not in (p_t / "AGENTS.md").read_text(encoding="utf-8"), "вставлена")

	# ── миграция не нужна ──
	r = run_migrate(tmp / "proj-full", tmp)
	check("без .omp/: exit 1", r.returncode == 1, r.stdout[-200:])
	check("без .omp/: причина названа", "следа старого контура нет" in r.stdout, r.stdout[:200])

	# ── непустой tools/ не сносится ──
	p_k = tmp / "proj-keep"
	make_project(p_k)
	(p_k / "tools" / "keep.txt").write_text("нужное\n", encoding="utf-8")
	git(p_k, "add", "-A")
	git(p_k, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "keep")
	r = run_migrate(p_k, tmp, "--apply")
	check("tools/ непустой: exit 0", r.returncode == 0, r.stdout[-300:])
	check("tools/ непустой: каталог жив",
	      (p_k / "tools" / "keep.txt").is_file(), "снесён вместе с нужным")

	# ── серверная ИБ без providers: подсказка о провайдере в плане (#44) ──
	p_s = tmp / "proj-server-ib"
	make_project(p_s)
	(p_s / "v8project.yaml").write_text(
		"name: fakeproj\n"
		"infobase:\n"
		"  connection: Srvr=srv-01;Ref=fake_base\n",
		encoding="utf-8")
	git(p_s, "add", "-A")
	git(p_s, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "server-ib")
	r = run_migrate(p_s, tmp)
	check("серверная ИБ: подсказка о провайдере в плане",
	      "серверная ИБ без providers" in r.stdout
	      and "infobases.origin.dbms" in r.stdout, r.stdout[-400:])

	# providers задан — подсказки нет (даже на серверной ИБ)
	p_p = tmp / "proj-with-providers"
	make_project(p_p)
	(p_p / "v8project.yaml").write_text(
		"name: fakeproj\n"
		"providers:\n"
		"  push: ibcmd\n"
		"infobase:\n"
		"  connection: Srvr=srv-01;Ref=fake_base\n",
		encoding="utf-8")
	git(p_p, "add", "-A")
	git(p_p, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "providers")
	r = run_migrate(p_p, tmp)
	check("providers задан: подсказки нет",
	      "серверная ИБ без providers" not in r.stdout, r.stdout[-300:])

	# ── сироты, проза, tools.va (дельты #49) ──
	p_o = tmp / "proj-orphans"
	make_project(p_o)
	(p_o / "features" / "AGENTS.md").write_text(
		"# Каталог features — сценарные тесты (Vanessa Automation)\n", encoding="utf-8")
	(p_o / "tests" / "epf").mkdir(parents=True)
	(p_o / "tests" / "epf" / "AGENTS.md").write_text(
		"# Каталог tests/epf — тестовые обработки (xUnit-подобные, Vanessa-ADD)\n",
		encoding="utf-8")
	(p_o / "tests" / "cfe").mkdir(parents=True)
	(p_o / "tests" / "cfe" / "AGENTS.md").write_text(
		"# Каталог tests/cfe — тестовые расширения (YAxUnit)\n", encoding="utf-8")
	(p_o / "tests" / "cfe" / "Тесты").mkdir()
	(p_o / "tests" / "cfe" / "Тесты" / "Module.bsl").write_text("// легаси\n",
	                                                           encoding="utf-8")
	agents_o = (p_o / "AGENTS.md").read_text(encoding="utf-8")
	(p_o / "AGENTS.md").write_text(
		agents_o
		+ "KB-поиск: `python3 .omp/plugins/node_modules/ontoship/skills/kb-search/gitmark.py search`.\n"
		+ "| `features/` | Сценарные тесты Vanessa Automation |\n", encoding="utf-8")
	(p_o / "v8project.local.yaml").write_text(
		"tools:\n  va:\n    epf_path: build/tools/vanessa-automation-single.epf\n",
		encoding="utf-8")
	git(p_o, "add", "-A")
	git(p_o, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "orphans")

	r = run_migrate(p_o, tmp)
	check("сироты: в плане снос features/AGENTS.md",
	      "снести сироту: features/AGENTS.md" in r.stdout, r.stdout[-500:])
	check("сироты: в плане снос tests/cfe/AGENTS.md",
	      "снести сироту: tests/cfe/AGENTS.md" in r.stdout, r.stdout[-500:])
	check("сироты: в плане правка прозы",
	      "проза AGENTS.md: снести строку" in r.stdout
	      and "Vanessa" in r.stdout, r.stdout[-600:])
	check("сироты: в плане снос tools.va с бэкапом",
	      "tools.va из v8project.local.yaml" in r.stdout
	      and "бэкап" in r.stdout, r.stdout[-400:])

	r = run_migrate(p_o, tmp, "--apply")
	check("сироты: apply exit 0", r.returncode == 0, r.stdout[-500:] + r.stderr[-300:])
	check("сироты: features/AGENTS.md снесён",
	      not (p_o / "features" / "AGENTS.md").exists(), "остался")
	check("сироты: tests/epf/AGENTS.md снесён",
	      not (p_o / "tests" / "epf" / "AGENTS.md").exists(), "остался")
	check("сироты: tests/cfe/AGENTS.md снесён, код расширения не тронут",
	      not (p_o / "tests" / "cfe" / "AGENTS.md").exists()
	      and (p_o / "tests" / "cfe" / "Тесты" / "Module.bsl").is_file(), "не то")
	agents_o2 = (p_o / "AGENTS.md").read_text(encoding="utf-8")
	check("сироты: мёртвые строки прозы снесены",
	      "Vanessa" not in agents_o2 and ".omp/" not in agents_o2, agents_o2[:400])
	check("сироты: секция контура и KB живы",
	      "1C CONTOUR" in agents_o2 and "<!-- KB (ontoship) -->" in agents_o2,
	      agents_o2[:400])
	local_o = (p_o / "v8project.local.yaml").read_text(encoding="utf-8")
	check("сироты: tools.va снесён из local",
	      "va:" not in local_o and "epf_path" not in local_o, local_o[:300])
	check("сироты: бэкап local лежит рядом с блоком",
	      (p_o / "v8project.local.yaml.bak").is_file()
	      and "epf_path" in (p_o / "v8project.local.yaml.bak").read_text(encoding="utf-8"),
	      "нет")

	# идемпотентность 5.5: dry-run после apply не планирует действий
	r = run_migrate(p_o, tmp, "--test-stack-only")
	check("идемпотентность: exit 0", r.returncode == 0, r.stdout[-300:])
	check("идемпотентность: сирот в плане нет",
	      "снести сироту" not in r.stdout, r.stdout[-400:])
	check("идемпотентность: прозы в плане нет",
	      "снести строку" not in r.stdout, r.stdout[-400:])
	check("идемпотентность: tools.va в плане нет",
	      "tools.va" not in r.stdout, r.stdout[-400:])

	return summary()


if __name__ == "__main__":
	sys.exit(main())
