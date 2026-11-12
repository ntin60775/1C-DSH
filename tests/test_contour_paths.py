#!/usr/bin/env python3
"""contour_paths.py — общий слой раскладок, состояния и разбора инстансов.

spec: docs/plans/dsh-contour.md, «Готово» 9 (Т10) — раскладки только DSH,
корень состояния задаёт DSH_1C_STATE_DIR; разбор db-instances.yaml переехал
из снятого wire_config.py без изменения семантики (мультистенд #37, Т6).
Ожидаемые значения разбора — из шаблона templates/db-instances.example.yaml
и семантики скаляров (кавычки, inline-комментарий), не из кода модуля.

Запуск: python3 tests/test_contour_paths.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import contour_paths as cp  # noqa: E402
from _lib import check, summary  # noqa: E402

# Пример из шаблона templates/db-instances.example.yaml (независимый источник).
TEMPLATE_INSTANCE = """instances:
  base2:
    channel: base2
    connection: File=build/ib/base2
    user: ""
    password: ""
    display: 98
    publish_port: 6011
"""


def main() -> int:
	# ── таблица раскладок: только DSH (Т10) ──
	check("LAYOUT_DIRS: канон и промежуточная, legacy снят",
	      cp.LAYOUT_DIRS == (".dsh/1c", ".dsh"), str(cp.LAYOUT_DIRS))

	# ── корень состояния ──
	saved = os.environ.get("DSH_1C_STATE_DIR")
	try:
		os.environ["DSH_1C_STATE_DIR"] = "/tmp/contour-state"
		root, source = cp.state_root()
		check("state_root: DSH_1C_STATE_DIR задаёт корень",
		      str(root) == "/tmp/contour-state" and source == "DSH_1C_STATE_DIR",
		      f"{root} ({source})")
		os.environ.pop("DSH_1C_STATE_DIR", None)
		root, source = cp.state_root()
		check("state_root: без переменной — ~/.dsh/state/1c",
		      root == Path.home() / ".dsh" / "state" / "1c" and source == "умолчание",
		      f"{root} ({source})")
	finally:
		if saved is None:
			os.environ.pop("DSH_1C_STATE_DIR", None)
		else:
			os.environ["DSH_1C_STATE_DIR"] = saved

	with tempfile.TemporaryDirectory(prefix="contour-paths-") as tmp:
		root = Path(tmp)
		# ── find_input: канон перекрывает промежуточную ──
		(root / ".dsh").mkdir(parents=True)
		(root / ".dsh" / "contour.json").write_text('{"layer": "flat"}', encoding="utf-8")
		hit = cp.find_input(root, "contour.json")
		check("find_input: промежуточная .dsh читается",
		      hit is not None and hit[1] == ".dsh/contour.json", str(hit))
		(root / ".dsh" / "1c").mkdir(parents=True)
		(root / ".dsh" / "1c" / "contour.json").write_text('{"layer": "canon"}', encoding="utf-8")
		hit = cp.find_input(root, "contour.json")
		check("find_input: канон .dsh/1c перекрывает .dsh",
		      hit is not None and hit[1] == ".dsh/1c/contour.json", str(hit))
		check("find_input: отсутствующий вход — None",
		      cp.find_input(root, "db-instances.yaml") is None, "не None")

		# ── parse_instances_at: семантика шаблона и скаляров ──
		inst = root / "db-instances.yaml"
		inst.write_text(TEMPLATE_INSTANCE, encoding="utf-8")
		records = cp.parse_instances_at(inst)
		check("разбор: одна запись base2 из шаблона",
		      len(records) == 1 and records[0]["name"] == "base2", str(records))
		r = records[0]
		check("разбор: поля шаблона прочитаны",
		      r["channel"] == "base2" and r["connection"] == "File=build/ib/base2"
		      and r["display"] == "98" and r["publish_port"] == "6011"
		      and r["user"] == "" and r["password"] == "", str(r))
		check("разбор: _seen несёт присутствующие ключи",
		      {"channel", "connection", "user", "password", "display", "publish_port"}
		      <= r["_seen"], str(r["_seen"]))

		inst.write_text("instances:\n  b2:\n    connection: File=x\n    user: u\n"
		                '    password: ""\n', encoding="utf-8")
		r = cp.parse_instances_at(inst)[0]
		check("разбор: канал не задан — имя инстанса", r["channel"] == "b2", str(r))
		check("разбор: password отсутствует в _seen (пустой валиден, отсутствие — нет)",
		      "publish_port" not in r["_seen"] and "password" in r["_seen"], str(r["_seen"]))

		inst.write_text("instances:\n  b2:\n    password: 'p#1'   # комментарий\n"
		                "    connection: File=x\n    user: u\n", encoding="utf-8")
		r = cp.parse_instances_at(inst)[0]
		check("скаляр: кавычки сняты, inline-комментарий вне кавычек срезан",
		      r["password"] == "p#1", repr(r["password"]))

		for bad, fragment in (
			("instances:\n\tb2:\n", "табуляция"),
			("instances:\n  B2:\n    connection: File=x\n", "имя инстанса"),
			("instances:\n  b2:\n    channel: два\n", "канал"),
			("instances:\n  b2:\n    мусор\n", "не разобрал строку"),
		):
			inst.write_text(bad, encoding="utf-8")
			try:
				cp.parse_instances_at(inst)
				check(f"fail-loud: {fragment}", False, "ValueError не поднят")
			except ValueError as error:
				check(f"fail-loud: {fragment}", fragment in str(error), str(error))

		# ── channel_url ──
		check("channel_url: ? к url без query",
		      cp.channel_url("http://h/mcp", "ch") == "http://h/mcp?channel=ch", "")
		check("channel_url: & к url с query",
		      cp.channel_url("http://h/mcp?x=1", "ch") == "http://h/mcp?x=1&channel=ch", "")

		# ── actual_db_url: drift локального слоя ──
		contour = {"1c_db": {"url": "http://127.0.0.1:6003/mcp"}}
		check("actual_db_url: умолчание без contour",
		      cp.actual_db_url({}, {}) == cp.DEFAULT_DB_URL, "")
		check("actual_db_url: url из contour",
		      cp.actual_db_url(contour, {}) == "http://127.0.0.1:6003/mcp", "")
		local = {"1c_db": {"url": "http://127.0.0.1:6004/mcp",
		                   "desired_url": "http://127.0.0.1:6003/mcp"}}
		check("actual_db_url: drift применён, пока desired совпадает",
		      cp.actual_db_url(contour, local) == "http://127.0.0.1:6004/mcp", "")
		local["desired_url"] = "http://other/mcp"
		check("actual_db_url: смена желаемого снимает drift",
		      cp.actual_db_url(contour, local) == "http://127.0.0.1:6003/mcp", "")

		# ── update_registry: атомарная запись под flock ──
		state = root / "state"
		ok, error = cp.update_registry(
			lambda data: data["projects"].update({"/p": {"path": "/p", "layout": "dsh"}}) or True,
			state_dir=state)
		check("реестр: запись создана", ok and error is None, str(error))
		data = json.loads((state / cp.REGISTRY).read_text(encoding="utf-8"))
		check("реестр: версия и запись на месте",
		      data["version"] == cp.REGISTRY_VERSION and data["projects"]["/p"]["layout"] == "dsh",
		      str(data))
		ok, error = cp.update_registry(lambda data: False, state_dir=state)
		check("реестр: mutate False — файл не переписан", ok, str(error))
		(state / cp.REGISTRY).write_text("{not json", encoding="utf-8")
		ok, error = cp.update_registry(lambda data: True, state_dir=state)
		check("реестр: битый файл не чинится молча", not ok and "не читается" in (error or ""),
		      str((ok, error)))
		check("реестр: lock-файл не подменяется", (state / (cp.REGISTRY + ".lock")).is_file(), "")

	return summary()


if __name__ == "__main__":
	sys.exit(main())
