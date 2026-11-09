#!/usr/bin/env python3
"""Тесты bsl_module_gate.py: контракт носителя 1С-кода (директивы, Перем,
права ролей) — блокирующий PostToolUse.

Симуляция правки — как у стайл-гейта: на диске уже ПРИМЕНЁННЫЙ текст
(PostToolUse приходит после записи), spans гейт строит из new_string.

Запуск: python3 tests/test_bsl_module_gate.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import audit_entries, check, run_hook, summary  # noqa: E402

HOOK = "bsl_module_gate.py"


def main() -> int:
	with tempfile.TemporaryDirectory() as tmp:
		tmp_path = Path(tmp)
		state = str(tmp_path / "state")
		tree = tmp_path / "tree"
		cwd = str(tree)
		(tree / ".zcode").mkdir(parents=True)
		common = tree / "src" / "cfe" / "СС" / "CommonModules" / "М" / "Ext"
		common.mkdir(parents=True)
		module_rel = "src/cfe/СС/CommonModules/М/Ext/Module.bsl"
		module_file = tree / module_rel
		mgr_rel = "src/cfe/СС/Documents/Д/Ext/ManagerModule.bsl"
		(tree / mgr_rel).parent.mkdir(parents=True)
		mgr_file = tree / mgr_rel
		form_rel = "src/cfe/СС/Catalogs/К/Forms/Форма/Ext/Form/Module.bsl"
		(tree / form_rel).parent.mkdir(parents=True)
		form_file = tree / form_rel
		roles_rel = "src/cfe/СС/Roles/Р/Ext/Rights.xml"
		(tree / roles_rel).parent.mkdir(parents=True)
		roles_file = tree / roles_rel

		def post(tool, tool_input, sid="s1"):
			return run_hook(HOOK, {
				"hook_event_name": "PostToolUse", "session_id": sid,
				"tool_name": tool, "tool_input": tool_input, "cwd": cwd,
			}, state, cwd)

		def ctx(response):
			try:
				return json.loads(response.stdout).get("additionalContext") or ""
			except ValueError:
				return ""

		def write(path, text):
			path.parent.mkdir(parents=True, exist_ok=True)
			path.write_text(text, encoding="utf-8")

		# ══ директива в общем модуле: блок ══════════════════════════════════
		write(module_file, "&НаСервере\nФункция Ф()\n\tВозврат 1;\nКонецФункции\n")
		r = post("Edit", {"file_path": module_rel,
		                  "old_string": "Функция Ф()", "new_string": "&НаСервере\nФункция Ф()"})
		check("директива в общем модуле: блок (exit 2)", r.returncode == 2, f"exit {r.returncode}")
		check("директива: правило в additionalContext",
		      "directive-in-common-module" in ctx(r), ctx(r)[:160])
		check("директива: норма про флаги в сообщении",
		      "флаги" in ctx(r), ctx(r)[:200])
		check("директива: аудит blocked", any(
			e.get("decision") == "blocked"
			for e in audit_entries(state, "bsl-module-gate")), "")

		# ══ вендорская директива вне правки — фон ═══════════════════════════
		write(module_file,
		      "// шапка\n\n\t&насерверебезконтекста \nФункция Ф()\n\tВозврат 2;\nКонецФункции\n")
		r = post("Edit", {"file_path": module_rel,
		                  "old_string": "Возврат 2;", "new_string": "Возврат 2;"})
		check("директива вне изменённых строк — фон, не блок", r.returncode == 0,
		      f"exit {r.returncode}")
		check("фон: холодная находка в stderr", "не блокируют" in r.stderr, r.stderr[:200])

		# ══ модуль менеджера: Write целым файлом ════════════════════════════
		write(mgr_file, "&НаСервере\nФункция Г()\n\tВозврат 1;\nКонецФункции\n")
		r = post("Write", {"file_path": mgr_rel, "content": "—"})
		check("Write целым файлом в менеджер: блок", r.returncode == 2, f"exit {r.returncode}")
		check("менеджер: правило directive-in-manager-module",
		      "directive-in-manager-module" in ctx(r), ctx(r)[:160])

		# ══ модульные Перем: верхний уровень блок, в методе — нет ═══════════
		write(module_file, "Функция Ф()\n\tПерем Лок;\n\tВозврат 3;\nКонецФункции\n")
		r = post("Edit", {"file_path": module_rel,
		                  "old_string": "Возврат 3;", "new_string": "Возврат 3;"})
		check("Перем внутри метода не нарушение", r.returncode == 0, f"exit {r.returncode}")

		write(module_file, "Перем Кэш;\n\nФункция Ф()\n\tВозврат 4;\nКонецФункции\n")
		r = post("Edit", {"file_path": module_rel,
		                  "old_string": "Возврат 4;", "new_string": "Возврат 4;"})
		check("Перем верхнего уровня вне правки — фон", r.returncode == 0, f"exit {r.returncode}")

		write(module_file, "Перем Кэш;\nФункция Ф()\n\tВозврат 5;\nКонецФункции\n")
		r = post("Edit", {"file_path": module_rel,
		                  "old_string": "Функция Ф()", "new_string": "Перем Кэш;\nФункция Ф()"})
		check("Перем верхнего уровня в правке: блок", r.returncode == 2, f"exit {r.returncode}")
		check("Перем: правило module-level-variable",
		      "module-level-variable" in ctx(r), ctx(r)[:160])

		# ══ носитель не тот: формы — гейт молчит ════════════════════════════
		write(form_file, "&НаСервере\nФункция В()\n\tВозврат 1;\nКонецФункции\n")
		r = post("Edit", {"file_path": form_rel,
		                  "old_string": "Возврат 1;", "new_string": "Возврат 6;"})
		check("директивы в модуле формы — не носитель гейта", r.returncode == 0,
		      f"exit {r.returncode}")

		# ══ роли: перечисление блок, документ нет, шаблон-ловушка чист ══════
		rights_ok = ("<Rights xmlns=\"http://v8.1c.ru/8.2/roles\">\n"
		             "\t<object>\n\t\t<name>Document.Сделка</name>\n"
		             "\t\t<right><name>Read</name><value>true</value></right>\n"
		             "\t</object>\n"
		             "\t<restrictionTemplate><expression>ЗНАЧЕНИЕ(Перечисление.Виды.ДляВсех)"
		             "</expression></restrictionTemplate>\n</Rights>\n")
		write(roles_file, rights_ok)
		r = post("Edit", {"file_path": roles_rel,
		                  "old_string": "</Rights>", "new_string": "</Rights>"})
		check("роль без запретных прав + «Перечисление» в шаблоне — чисто",
		      r.returncode == 0, f"exit {r.returncode}")

		write(roles_file, rights_ok.replace(
			"</Rights>",
			"\t<object>\n\t\t<name>Enum.ВидыОплат</name>\n"
			"\t\t<right><name>Read</name><value>true</value></right>\n\t</object>\n</Rights>"))
		r = post("Edit", {"file_path": roles_rel,
		                  "old_string": "</Rights>", "new_string": "</Rights>"})
		check("право на перечисление: блок", r.returncode == 2, f"exit {r.returncode}")
		check("роль: правило role-forbidden-rights",
		      "role-forbidden-rights" in ctx(r), ctx(r)[:160])

		# ══ эскалация точечная ══════════════════════════════════════════════
		esc = tree / ".zcode" / "bsl-module-gate-escalations.txt"
		esc.write_text("# комментарий\nmodule-level-variable:CommonModules/М/\n", encoding="utf-8")
		write(module_file, "Перем Кэш;\nФункция Ф()\n\tВозврат 7;\nКонецФункции\n")
		r = post("Edit", {"file_path": module_rel,
		                  "old_string": "Возврат 7;", "new_string": "Возврат 7;"})
		check("эскалация снимает блок", r.returncode == 0, f"exit {r.returncode}")
		write(mgr_file, "&НаСервере\nПерем К2;\nФункция Г()\n\tВозврат 8;\nКонецФункции\n")
		r = post("Edit", {"file_path": mgr_rel,
		                  "old_string": "Функция Г()", "new_string": "Перем К2;\nФункция Г()"})
		check("эскалация точечная: другой файл блокируется", r.returncode == 2,
		      f"exit {r.returncode}")

		# ══ бридж unica.apply: план → исполнение ════════════════════════════
		def post_unica(tool_input, tool="mcp__unica__unica_apply", sid="s2"):
			return run_hook(HOOK, {
				"hook_event_name": "PostToolUse", "session_id": sid,
				"tool_name": tool, "tool_input": tool_input, "cwd": cwd,
			}, state, cwd)

		def plan_ops(text):
			return [{"op": "code.insert",
			         "args": {"at": "cfe:CommonModule.М.Body", "text": text}}]

		bridge_file = Path(state) / "state" / "1c" / "module-bridge" / "s2.json"
		write(module_file, "// шапка\n\n&НаКлиенте\nФункция Ц()\nФункция Ф()\n\tВозврат 1;\nКонецФункции\n")
		r = post_unica({"at": "cfe:CommonModule.М", "ops": plan_ops("&НаКлиенте\nФункция Ц()")})
		check("apply план: не блок, план в состоянии", r.returncode == 0 and bridge_file.is_file(),
		      f"exit {r.returncode}")

		r = post_unica({"executionToken": "tok-1"},
		               tool="mcp__plugin_unica_unica__unica_apply")
		check("apply исполнение: директива из плана блокирует", r.returncode == 2,
		      f"exit {r.returncode}")
		check("apply исполнение: план закрыт", not bridge_file.exists(), str(bridge_file))

		write(module_file, "// шапка\n\nФункция Ф()\n\tВозврат 9;\nКонецФункции\n")
		r = post_unica({"at": "cfe:CommonModule.М", "ops": plan_ops("\tВозврат 9;")})
		r = post_unica({"executionToken": "tok-2"},
		               tool="mcp__plugin_unica_unica__unica_apply")
		check("apply исполнение: чистая вставка проходит", r.returncode == 0,
		      f"exit {r.returncode}")

		# ══ не наше событие — тихий пропуск ═════════════════════════════════
		r = post("Bash", {"command": "echo hi"})
		check("не целевой инструмент — пропуск", r.returncode == 0, f"exit {r.returncode}")

	return summary()


if __name__ == "__main__":
	sys.exit(main())
