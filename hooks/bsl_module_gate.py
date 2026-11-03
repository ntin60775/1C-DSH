#!/usr/bin/env python3
"""bsl_module_gate.py — гейт контракта носителя 1С-кода (PostToolUse, блокирующий).

Норма владельца (2026-11-03, урок тикета 10 provider-switch project-bp):
контекст исполнения модуля задаётся ТИПОМ НОСИТЕЛЯ, а не директивами.
У вендорского легаси своя правда — наш слой так не пишет. Правила:

  1. directive-in-common-module — директивы компиляции (&НаКлиенте, &НаСервере,
     &НаСервереБезКонтекста, &НаКлиентеНаСервере, &НаКлиентеНаСервереБезКонтекста)
     в **/CommonModules/*/Ext/Module.bsl. Доступность общего модуля задают
     флаги (Клиент/Сервер/ВызовСервера); код из модуля формы переносится
     телами, без директив (перенос директив в общий модуль бесконечно вешает
     тонкого клиента 8.3.27.1859 — доказано юнитом 10).
  2. directive-in-manager-module — те же директивы в **/Ext/ManagerModule.bsl
     (носитель целиком серверный, директивы бессмысленны).
  3. module-level-variable — «Перем» на верхнем уровне (вне тел методов) в
     тех же носителях: разделяемое скрытое состояние, не создают.
  4. role-forbidden-rights — права в **/Roles/*/Ext/Rights.xml на
     Enum/CommonPicture/CommonTemplate: метаданные, на которые роли не
     назначаются никогда (живая эмпирика парка: 0 вхождений при сотнях тысяч
     объектов прав). Права ищутся строго по object/name — слово «Перечисление»
     в текстах ограничений того же файла законно и не находка.

Блокируются только нарушения В ИЗМЕНЁННЫХ строках (канон контура: старый
код — фон в stderr, чинится при случае). Rights.xml проверяется целиком —
запретный класс по всему парку не встречается, шума нет.

Каналы правки:
  1. Write/Edit/ApplyPatch — прямые правки (эскалации unica-gate, tests/epf);
     здесь же проверяются Rights.xml (файл целиком).
  2. unica.apply — штатный канал: план несёт тексты BSL-ops, исполнение
     проверяет файлы, свежие на момент плана (mtime-кандидаты вместо полной
     перечитки дерева — стайл-гейт уже читает всё, дублировать не нужно);
     план без контекста файла не проверяется вовсе — правила зависят от
     носителя, а носитель виден только на диске.
Названный пробел v1: права через XML-ops unica не покрываются (адресация
вне .Body); правка Rights.xml через прямую Write/Edit проверяется.

Эскалация (осознанное исключение, слово владельца):
.zcode/bsl-module-gate-escalations.txt — строки «<rule-id>:<подстрока пути>»
или «<подстрока пути>» (все правила). Файл-прототип: unica-gate-escalations.txt.

Доставка блока — stderr + stdout additionalContext (рантайм ZCode не доставляет
модели stderr блокирующего PostToolUse — пруф в шапке bsl_style_gate.py).

Журнал: rule-audit.jsonl, rule: "bsl-module-gate".
"""
import json
import re
import sys
import time
from pathlib import Path

from contour_common import (
	SOURCE_PREFIXES,
	audit,
	normalize_source_path,
	project_root,
	read_event,
	session_id,
	session_state_dir,
	unica_args,
	unica_verb,
)
# Общая механика правок/планов — тот же каталог хуков, дублировать не нужно.
from bsl_style_gate import (
	APPLY_PATCH_FILE_RE,
	_span_of_new_text,
	bsl_targets,
	changed_lines,
	plan_bsl_texts,
)

RULE = "bsl-module-gate"

ESCALATION_FILE = ".zcode/bsl-module-gate-escalations.txt"

BRIDGE_DIR = "module-bridge"
BRIDGE_TTL_S = 20 * 60
MTIME_SLACK_S = 60  # часы файловой системы могут отставать от часов процесса

DIRECTIVE_RE = re.compile(
	r"^&\s*(НаКлиенте|НаСервере|НаСервереБезКонтекста"
	r"|НаКлиентеНаСервере|НаКлиентеНаСервереБезКонтекста)\s*$",
	re.IGNORECASE)
PEREM_RE = re.compile(r"^Перем\s+\S", re.IGNORECASE)
METHOD_START_RE = re.compile(r"^(Процедура|Функция)\s", re.IGNORECASE)
METHOD_END_RE = re.compile(r"^(КонецПроцедуры|КонецФункции)\s*$", re.IGNORECASE)

COMMON_MODULE_RE = re.compile(r"(^|/)CommonModules/[^/]+/Ext/Module\.bsl$")
MANAGER_MODULE_RE = re.compile(r"/Ext/ManagerModule\.bsl$")
RIGHTS_RE = re.compile(r"(^|/)Roles/[^/]+/Ext/Rights\.xml$")

FORBIDDEN_RIGHT_PREFIXES = ("Enum.", "CommonPicture.", "CommonTemplate.")
OBJECT_RE = re.compile(r"<object>\s*<name>([^<]+)</name>(.*?)</object>", re.S)
RIGHT_TRUE_RE = re.compile(r"<right>\s*<name>[^<]+</name>\s*<value>\s*true\s*</value>", re.S)

RULE_NORMS = {
	"directive-in-common-module":
		"директивы компиляции в общем модуле не используются — доступность "
		"задают флаги (Клиент/Сервер/ВызовСервера); код из модуля формы "
		"переносится телами, без директив",
	"directive-in-manager-module":
		"директивы в модуле менеджера не используются — контекст исполнения "
		"задаёт тип носителя (сервер)",
	"module-level-variable":
		"переменные модуля на верхнем уровне — разделяемое скрытое состояние; "
		"состояние передаётся параметрами",
	"role-forbidden-rights":
		"права ролей на перечисления, общие картинки и общие макеты не "
		"назначаются никогда",
}


# ── носители и правила ───────────────────────────────────────────────────────

def rule_id_for(rel: str, line_kind: str) -> str:
	if line_kind == "module-level-variable":
		return "module-level-variable"
	if COMMON_MODULE_RE.search(rel):
		return "directive-in-common-module"
	return "directive-in-manager-module"


def check_bsl(rel: str, text: str, spans):
	"""Нарушения BSL-носителя: [(строка, rule_id, hot)];
	spans None = весь файл (всё hot), иначе hot только в диапазонах правки."""
	if not (COMMON_MODULE_RE.search(rel) or MANAGER_MODULE_RE.search(rel)):
		return []
	found = []
	depth = 0
	for number, raw in enumerate(text.splitlines(), 1):
		stripped = raw.strip()
		if stripped.startswith("//"):
			continue
		in_span = spans is None or any(a <= number <= b for a, b in spans)
		if DIRECTIVE_RE.match(stripped):
			found.append((number, rule_id_for(rel, "directive"), in_span))
		elif depth == 0 and PEREM_RE.match(stripped):
			found.append((number, "module-level-variable", in_span))
		if METHOD_START_RE.match(stripped):
			depth += 1
		elif METHOD_END_RE.match(stripped):
			depth = max(0, depth - 1)
	return found


def check_rights(text: str):
	"""Права на никогда-не-назначаемые метаданные: [(имя объекта, rule_id)]."""
	found = []
	for m in OBJECT_RE.finditer(text):
		name = m.group(1)
		if not name.startswith(FORBIDDEN_RIGHT_PREFIXES):
			continue
		if RIGHT_TRUE_RE.search(m.group(2)):
			found.append((name, "role-forbidden-rights"))
	return found


# ── эскалации ────────────────────────────────────────────────────────────────

def escalation_skips(root: str):
	"""[(rule_id или None, подстрока пути)] из файла эскалаций; [] — нет."""
	path = Path(root) / ESCALATION_FILE
	try:
		lines = path.read_text(encoding="utf-8").splitlines()
	except OSError:
		return []
	out = []
	for ln in lines:
		ln = ln.strip()
		if not ln or ln.startswith("#"):
			continue
		if ":" in ln:
			rule, _, needle = ln.partition(":")
			out.append((rule.strip(), needle.strip()))
		else:
			out.append((None, ln))
	return out


def is_escalated(skips, rel: str, rule: str) -> bool:
	for esc_rule, needle in skips:
		if (esc_rule is None or esc_rule == rule) and needle in rel:
			return True
	return False


# ── отчёт ────────────────────────────────────────────────────────────────────

def gate_verdict(entries: list, root: str, action: str, origin: str) -> int:
	"""entries — [(файл, строка, rule, hot)]. hot без эскалации → блок (exit 2);
	старое (cold) и под эскалацией — фон в stderr, не блок."""
	if not entries:
		return 0
	skips = escalation_skips(root)
	hot = [e for e in entries if e[3] and not is_escalated(skips, e[0], e[2])]
	background = [e for e in entries if not (e[3] and not is_escalated(skips, e[0], e[2]))]
	if not hot:
		audit(RULE, action,
		      f"нарушения носителя вне правки/под эскалациями ({len(background)})",
		      "advisory")
		sys.stderr.write(
			f"1c-zcode гейт носителя: {len(background)} нарушений вне твоей правки "
			"(старый код или эскалация — не блокируют, чинить при случае):\n"
			+ "\n".join(f"{f}:{n}: [{r}] {RULE_NORMS[r]}" for f, n, r, _h in background)
			+ "\n")
		return 0
	lines = [f"{f}:{n}: [{r}] {RULE_NORMS[r]}" for f, n, r, _h in hot]
	rules = sorted({r for _, _, r, _h in hot})
	header = [
		"БЛОК: правка нарушает контракт носителя (гейт bsl-module-gate, "
		f"канал: {origin}).",
		f"Норма контура: {('; '.join(RULE_NORMS[r] for r in rules))}.",
		"Исправь перечисленные строки и повтори правку.",
		"Осознанное исключение — .zcode/bsl-module-gate-escalations.txt: "
		"строка «<rule-id>:<подстрока пути>» или «<подстрока пути>».",
	]
	reason = "\n".join(header + ["", *lines])
	audit(RULE, action, f"нарушения носителя в изменённых строках ({len(hot)})", "blocked")
	sys.stderr.write(reason + "\n")
	sys.stdout.write(json.dumps({"additionalContext": reason}, ensure_ascii=False) + "\n")
	return 2


# ── прямые правки ────────────────────────────────────────────────────────────

def raw_paths_of(event: dict, tool_name: str) -> list:
	tool_input = event.get("tool_input") or {}
	raws = []
	direct = str(tool_input.get("file_path") or tool_input.get("path") or "")
	if direct:
		raws.append(direct)
	if tool_name == "ApplyPatch" or not raws:
		patch = str(tool_input.get("patch") or tool_input.get("input") or "")
		raws.extend(APPLY_PATCH_FILE_RE.findall(patch))
	return raws


def handle_direct(event: dict, tool_name: str, cwd: str, root: str) -> int:
	entries = []
	for raw in raw_paths_of(event, tool_name):
		norm = normalize_source_path(raw, cwd)
		if not norm:
			continue
		rel, _tree = norm
		try:
			text = (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
		except OSError:
			continue
		if RIGHTS_RE.search(rel):
			for _name, rule in check_rights(text):
				entries.append((rel, 0, rule, True))
			continue
		spans = changed_lines(event, tool_name, rel, root)
		for number, rule, in_span in check_bsl(rel, text, spans):
			entries.append((rel, number, rule, in_span))
	return gate_verdict(entries, root, ", ".join(sorted({e[0] for e in entries})) or "-",
	                    "прямая правка Write/Edit/ApplyPatch")


# ── бридж unica.apply ────────────────────────────────────────────────────────

def _bridge_path(sid: str) -> Path:
	return session_state_dir(BRIDGE_DIR) / f"{sid}.json"


def _load_plans(sid: str) -> list:
	try:
		data = json.loads(_bridge_path(sid).read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		return []
	plans = data.get("plans") if isinstance(data, dict) else None
	if not isinstance(plans, list):
		return []
	now = time.time()
	return [p for p in plans
	        if isinstance(p, dict) and isinstance(p.get("expires_ts"), (int, float))
	        and p["expires_ts"] > now]


def _save_plans(sid: str, plans: list) -> None:
	path = _bridge_path(sid)
	if not plans:
		try:
			path.unlink(missing_ok=True)
		except OSError:
			pass
		return
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		tmp = path.with_suffix(".tmp")
		tmp.write_text(json.dumps({"plans": plans}, ensure_ascii=False), encoding="utf-8")
		tmp.replace(path)
	except OSError:
		pass  # нет доступа к состоянию — бридж работает в рамках одной жизни


def fresh_sources(root: str, since_ts: float):
	"""Свежие .bsl (mtime-кандидаты) и Rights.xml (все) в канонических src."""
	fresh_bsl, rights = [], []
	cutoff = since_ts - MTIME_SLACK_S
	for prefix in SOURCE_PREFIXES:
		base = Path(root) / prefix.rstrip("/")
		if not base.is_dir():
			continue
		for p in base.rglob("*"):
			if not p.is_file():
				continue
			name = p.name
			try:
				mtime = p.stat().st_mtime
			except OSError:
				continue
			rel = str(p.relative_to(root))
			if name.lower() == "rights.xml" and RIGHTS_RE.search(rel):
				rights.append(rel)
			elif p.suffix.lower() == ".bsl" and mtime >= cutoff:
				fresh_bsl.append(rel)
	return fresh_bsl, rights


def handle_apply_plan(event: dict, root: str, args: dict) -> int:
	sid = session_id(event)
	texts = plan_bsl_texts(args)
	if not texts:
		audit(RULE, f"unica.apply план {args.get('at') or ''}",
		      "ops вне тел BSL-модулей — носитель не проверяется", "advisory")
		return 0
	plans = _load_plans(sid)
	plans.append({
		"saved_at": int(time.time()),
		"expires_ts": time.time() + BRIDGE_TTL_S,
		"texts": texts,
	})
	_save_plans(sid, plans)
	# Правила зависят от носителя, а план адресован логическим узлом —
	# проверка только по факту записи (фаза исполнения).
	audit(RULE, f"unica.apply план {args.get('at') or ''}",
	      f"план сохранён ({len(texts)} BSL-ops)", "advisory")
	return 0


def handle_apply_execute(event: dict, root: str) -> int:
	sid = session_id(event)
	plans = _load_plans(sid)
	if not plans:
		return 0  # потеря состояния (рестарт сессии/TTL) — тихий пропуск
	oldest = min(p.get("saved_at", 0) for p in plans)
	fresh_bsl, _rights = fresh_sources(root, oldest)
	entries, matched = [], []
	for plan in plans:
		found = False
		for text in plan.get("texts") or []:
			if not isinstance(text, str) or not text:
				continue
			for rel in fresh_bsl:
				try:
					body = (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
				except OSError:
					continue
				span = _span_of_new_text(body, text)
				if not span:
					continue
				found = True
				for number, rule, in_span in check_bsl(rel, body, span):
					entries.append((rel, number, rule, in_span))
		if found:
			matched.append(plan)
	if not entries and not matched:
		audit(RULE, "unica.apply исполнение",
		      "тексты планов не найдены среди свежих файлов — планы оставлены", "advisory")
		return 0
	_save_plans(sid, [p for p in plans if p not in matched])
	return gate_verdict(entries, root, "unica.apply", "unica.apply")


# ── маршрутизация ────────────────────────────────────────────────────────────

def handle_unica(event: dict, tool_name: str, cwd: str, root: str) -> int:
	if unica_verb(tool_name, root) != "apply":
		return 0
	args = unica_args(event.get("tool_input"))
	if "executionToken" in args:
		return handle_apply_execute(event, root)
	if isinstance(args.get("ops"), list):
		return handle_apply_plan(event, root, args)
	return 0


def main() -> int:
	event = read_event()
	if not event:
		return 0
	tool_name = str(event.get("tool_name") or "")
	cwd = str(event.get("cwd") or "")
	root = project_root(cwd)
	if tool_name.startswith("mcp__"):
		return handle_unica(event, tool_name, cwd, root)
	if tool_name not in ("Write", "Edit", "ApplyPatch"):
		return 0
	targets = bsl_targets(event, cwd) + [
		norm[0] for norm in (normalize_source_path(raw, cwd)
		                     for raw in raw_paths_of(event, tool_name))
		if norm and RIGHTS_RE.search(norm[0])
	]
	if not targets:
		return 0
	return handle_direct(event, tool_name, cwd, root)


if __name__ == "__main__":
	sys.exit(main())
