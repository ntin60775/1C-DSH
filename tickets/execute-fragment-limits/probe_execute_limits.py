#!/usr/bin/env python3
"""Диагностическая матрица проб execute_code — юнит «Границы execute-фрагмента 1c-db» (Т1).

Клиент stdlib к живому 1c-db (Streamable HTTP, минимальный handshake как
onec_db.OnecDB). Каждая проба — отдельный tools/call execute_code; факт —
число либо текст ошибки по каждой пробе. Фрагменты вычислительные, записей
данных в базы нет; анонимайзер канала не отключается.

Скрипт строки соединения не строит и не печатает; печатается только url
канала из contour-конфига (без userinfo) и факты проб.

Запуск:
	python3 probe_execute_limits.py --project <корень проекта> --label <имя среды>

URL 1c-db читается из .zcode/1c/contour.json (ключ 1c_db.url) с перекрытием
фактическим из .zcode/1c/contour.local.json (дрейф порта). Прямой URL можно
задать --url.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Независимый источник ожиданий — Синтакс-помощник 8.3.27.1859 (статья
# Выполнить, root_Exec): операторных ограничений нет; клон тулкита
# 1.10.0 ca14a98, Module.bsl:3969 — «Выполнить(Код);».
# kind пробы: owner — владельческая, disc — дискриминатор, ctrl — контроль,
# base — базовая.


def _cycle_while(indent: str) -> str:
	lines = ["Е2ЕМ4Сч = 0;"]
	for src in [
	    "Пока Е2ЕМ4Сч < 3 Цикл",
	    f"{indent}Е2ЕМ4Сч = Е2ЕМ4Сч + 1;",
	    "КонецЦикла;",
	]:
	    lines.append(src)
	lines.append("Результат = Е2ЕМ4Сч;")
	return "\n".join(lines)


def _nested_if(counter: str, condition: str) -> str:
	"""Если 3 уровня, счётчик в двух ступенях (тогда +1, иначе +10/100/1000)."""
	parts = [f"{counter} = 0;"]
	weights = [1000, 100, 10]  # внешние уровни — при молчании листа виден вес
	for level in range(3):
	    parts.append("    " * level + f"Если {condition} Тогда")
	parts.append("            " + f"{counter} = {counter} + 1;")
	for level in (2, 1, 0):
	    parts.append("    " * level + "Иначе")
	    parts.append("    " * level + f"    {counter} = {counter} + {weights[level]};")
	    parts.append("    " * level + "КонецЕсли;")
	parts.append(f"Результат = {counter};")
	return "\n".join(parts)


PROBES = [
	{
	    "id": "own-while-tab",
	    "kind": "owner",
	    "desc": "владельческая: цикл Пока, отступ табом",
	    "bsl": _cycle_while("\t"),
	    "expect_green": 3,
	},
	{
	    "id": "own-while-spaces",
	    "kind": "owner",
	    "desc": "владельческая: цикл Пока, отступ пробелами",
	    "bsl": _cycle_while("    "),
	    "expect_green": 3,
	},
	{
	    "id": "own-strduplicate",
	    "kind": "owner",
	    "desc": "владельческая: СтрДублировать — контроль диагностики (функции нет в языке)",
	    "bsl": 'Результат = СтрДублировать("а", 5);',
	    "expect_red": "не определена",
	},
	{
	    "id": "ctrl-strlen",
	    "kind": "ctrl",
	    "desc": "контроль: СтрДлина",
	    "bsl": 'Результат = СтрДлина("ааа");',
	    "expect_green": 3,
	},
	{
	    "id": "disc-if-oneline",
	    "kind": "disc",
	    "desc": "дискриминатор: однострочный Если",
	    "bsl": "Если Истина Тогда Результат = 2; КонецЕсли;",
	    "expect_green": 2,
	},
	{
	    "id": "disc-nested-literal",
	    "kind": "disc",
	    "desc": "дискриминатор, ступень А: Если 3 уровня, условия тривиальные (Истина), счётчик в двух ступенях",
	    "bsl": _nested_if("Е2ЕМ1Сч", "Истина"),
	    "expect_green": 1,
	},
	{
	    "id": "disc-nested-calls",
	    "kind": "disc",
	    "desc": "дискриминатор, ступень Б: Если 3 уровня, условия — вызовы глобальных функций (СтрДлина)",
	    "bsl": _nested_if("Е2ЕМ2Сч", 'СтрДлина("а") = 1'),
	    "expect_green": 1,
	},
	{
	    "id": "disc-for-loop",
	    "kind": "disc",
	    "desc": "дискриминатор: Для-цикл со счётчиком",
	    "bsl": (
	        "Е2ЕМ3Сч = 0;\n"
	        "Для Е2ЕМ3И = 1 По 3 Цикл\n"
	        "    Е2ЕМ3Сч = Е2ЕМ3Сч + 1;\n"
	        "КонецЦикла;\n"
	        "Результат = Е2ЕМ3Сч;"
	    ),
	    "expect_green": 3,
	},
	{
	    "id": "ctrl-strtemplate",
	    "kind": "ctrl",
	    "desc": "контроль: СтрШаблон — доступность глобальных функций (8.3.6; маркеры %1..%N)",
	    "bsl": 'Результат = СтрШаблон("%1|%2", СтрДлина("ааа"), СтрДлина("ааа"));',
	    "expect_green": "3|3",
	},
	{
	    "id": "disc-syntax-error",
	    "kind": "disc",
	    "desc": "дискриминатор: заведомая синтаксическая ошибка — строка epf в ошибке должна указывать на Выполнить(Код) клона (3969)",
	    "bsl": "Результат = (();",
	    "expect_red": "Форма(3969)",
	},
	{
	    "id": "base-one",
	    "kind": "base",
	    "desc": "базовая: Результат = 1",
	    "bsl": "Результат = 1;",
	    "expect_green": 1,
	},
]

# Проба с «естественными» именами владельца — заводится только при молчании
# обеих ступеней (тикет Т1): различает молчание против коллизии имён.
# На серверной ветке контекст формы недоступен — ВыполнитьКод объявлена
# &НаСервереБезКонтекста (клон 1.10.0, Module.bsl:3963); клиентская ветка
# (execution_context=client) вне матрицы.
OWNER_NAMES_BSL = _nested_if("Е2ЕОчки", 'СтрДлина("а") = 1')


def resolve_url(project: Path) -> tuple:
	"""(url, желаемый, фактический) из contour-конфига проекта."""
	config = project / ".zcode" / "1c" / "contour.json"
	if not config.is_file():
	    sys.exit(f"нет contour-конфига проекта: {config}")
	try:
	    desired = (json.loads(config.read_text("utf-8")) or {}).get("1c_db", {}).get("url")
	except json.JSONDecodeError as e:
	    sys.exit(f"битый JSON в {config}: {e}")
	if not desired:
	    sys.exit(f"нет ключа 1c_db.url в {config}")
	local = project / ".zcode" / "1c" / "contour.local.json"
	if local.is_file():
	    try:
	        local_data = json.loads(local.read_text("utf-8")) or {}
	    except json.JSONDecodeError as e:
	        sys.exit(f"битый JSON в {local}: {e}")
	    actual = local_data.get("1c_db", {}).get("url")
	    if actual:
	        return actual, desired, actual
	return desired, desired, desired


class ProbeClient:
	"""Минимальный MCP-клиент (Streamable HTTP); handshake как onec_db.OnecDB."""

	def __init__(self, url: str, timeout: int = 120):
	    self.url = url
	    self.timeout = timeout
	    self._sid = None
	    self._id = 0
	    self._initialize()

	def _post(self, request_id: int, payload: dict, notify: bool = False):
	    headers = {"Content-Type": "application/json",
	               "Accept": "application/json, text/event-stream"}
	    if self._sid:
	        headers["Mcp-Session-Id"] = self._sid
	    req = urllib.request.Request(
	        self.url, data=json.dumps(payload).encode("utf-8"),
	        headers=headers, method="POST")
	    try:
	        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
	            sid = resp.headers.get("Mcp-Session-Id")
	            if sid:
	                self._sid = sid
	            raw = resp.read().decode("utf-8")
	            ctype = resp.headers.get("Content-Type", "")
	    except urllib.error.HTTPError as e:
	        raise RuntimeError(f"1c-db HTTP {e.code}: {e.read()[:300]}") from e
	    except urllib.error.URLError as e:
	        raise RuntimeError(f"1c-db недоступен ({self.url}): {e.reason}") from e
	    if notify:
	        return None
	    if "text/event-stream" in ctype:
	        answer = None
	        for line in raw.splitlines():
	            if not line.startswith("data:"):
	                continue
	            candidate = line[5:].strip()
	            if not candidate:
	                continue
	            message = json.loads(candidate)
	            # уведомления без id пропускаем — матрице нужен ответ на свой запрос
	            if message.get("id") == request_id and (
	                    "result" in message or "error" in message):
	                answer = message
	                break
	        if answer is None:
	            raise RuntimeError("1c-db вернул SSE-поток без ответа на запрос")
	        return answer
	    return json.loads(raw)

	def _initialize(self):
	    request_id = self._next_id()
	    resp = self._post(request_id,
	                      {"jsonrpc": "2.0", "id": request_id,
	                       "method": "initialize",
	                       "params": {"protocolVersion": "2025-03-26",
	                                  "capabilities": {},
	                                  "clientInfo": {"name": "probe-58", "version": "0.1"}}})
	    if "error" in resp:
	        raise RuntimeError(f"1c-db initialize: {resp['error']}")
	    self._post(self._next_id(),
	               {"jsonrpc": "2.0", "method": "notifications/initialized"}, notify=True)

	def _next_id(self) -> int:
	    self._id += 1
	    return self._id

	def raw_execute(self, code: str):
	    """tools/call execute_code без разворачивания в исключения: матрице нужен факт.

	    Возвращает ("green", значение) | ("red", текст ошибки) | ("silence", причина).
	    """
	    request_id = self._next_id()
	    resp = self._post(request_id,
	                      {"jsonrpc": "2.0", "id": request_id,
	                       "method": "tools/call",
	                       "params": {"name": "execute_code",
	                                  "arguments": {"code": code,
	                                                "execution_context": "server"}}})
	    if "error" in resp:
	        return "red", json.dumps(resp["error"], ensure_ascii=False)[:400]
	    result = resp.get("result", {})
	    if result.get("isError"):
	        text = "; ".join(c.get("text", "") for c in result.get("content", [])
	                         if isinstance(c, dict))
	        return "red", text[:400]
	    data = result.get("structuredContent")
	    if data is None:
	        for c in result.get("content", []):
	            if isinstance(c, dict) and c.get("type") == "text":
	                data = c["text"]
	                break
	    if isinstance(data, dict) and isinstance(data.get("result"), dict):
	        data = data["result"]
	    if isinstance(data, dict) and data.get("success") is False:
	        return "red", str(data.get("error", "?"))[:400]
	    payload = data.get("data") if isinstance(data, dict) else data
	    if payload is None:
	        return "silence", "Результат не установлен"
	    if isinstance(payload, str):
	        stripped = payload.strip()
	        if stripped[:1] in ('"', "{", "[", "-", "0", "1", "2", "3", "4",
	                            "5", "6", "7", "8", "9", "t", "f", "n"):
	            try:
	                payload = json.loads(payload)
	            except (json.JSONDecodeError, ValueError):
	                pass
	    return "green", payload


def format_value(value) -> str:
	if isinstance(value, str):
	    return json.dumps(value, ensure_ascii=False)
	if isinstance(value, bool):
	    return "Истина" if value else "Ложь"
	return repr(value)


def values_equal(fact, expected) -> bool:
	"""Сравнение с учётом типа: bool — не число (True не засчитывается за 1)."""
	if isinstance(fact, bool) != isinstance(expected, bool):
	    return False
	return fact == expected


def run_probe(client: ProbeClient, probe: dict):
	"""Вернуть (статус, факт, отклонение)."""
	status, fact = client.raw_execute(probe["bsl"])
	if "expect_green" in probe:
	    expected = probe["expect_green"]
	    if status == "green" and values_equal(fact, expected):
	        return status, fact, None
	    return status, fact, f"ожидалось {expected!r}, получено {status} {format_value(fact)}"
	expected_red = probe["expect_red"]
	if status == "red" and expected_red in str(fact):
	    return status, fact, None
	return status, fact, f"ожидалась красная ошибка с {expected_red!r}, получено {status} {format_value(fact)}"


def main():
	ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	ap.add_argument("--project", required=True, help="корень проекта 1С")
	ap.add_argument("--label", required=True, help="имя среды для столбца матрицы")
	ap.add_argument("--url", help="прямой URL 1c-db (перекрывает contour-конфиг)")
	ap.add_argument("--timeout", type=int, default=120)
	args = ap.parse_args()

	if args.url:
	    url = args.url
	else:
	    url, desired, actual = resolve_url(Path(args.project))
	    if actual != desired:
	        print(f"# дрейф порта: желаемый {desired}, фактический {actual}")

	client = ProbeClient(url, timeout=args.timeout)
	print(f"# среда: {args.label}")
	print(f"# канал: {url}")
	print()

	deviations = 0
	facts = {}
	nested = {p["id"]: None for p in PROBES
	          if p["id"] in ("disc-nested-literal", "disc-nested-calls")}
	for probe in PROBES:
	    status, fact, deviation = run_probe(client, probe)
	    facts[probe["id"]] = fact
	    if probe["id"] in nested:
	        nested[probe["id"]] = status
	    mark = "ОК"
	    if deviation:
	        deviations += 1
	        mark = "ОТКЛОНЕНИЕ"
	    print(f"[{probe['id']}] {mark} ({probe['kind']}) {probe['desc']}")
	    print(f"    факт: {format_value(fact)}")
	    if deviation:
	        print(f"    {deviation}")
	    print()

	if all(s == "silence" for s in nested.values()):
	    probe = {
	        "id": "disc-owner-names",
	        "kind": "disc",
	        "desc": "дискриминатор: ступень Б с «естественными» именами владельца (Е2ЕОчки)",
	        "bsl": OWNER_NAMES_BSL,
	        "expect_green": 1,
	    }
	    status, fact, deviation = run_probe(client, probe)
	    facts[probe["id"]] = fact
	    mark = "ОК" if not deviation else "ОТКЛОНЕНИЕ"
	    deviations += 1 if deviation else 0
	    print(f"[{probe['id']}] {mark} ({probe['kind']}) {probe['desc']}")
	    print(f"    факт: {format_value(fact)}")
	    if deviation:
	        print(f"    {deviation}")
	    print()
	else:
	    print("[disc-owner-names] НЕ ПОТРЕБОВАЛАСЬ — обе ступени вернули ожидание")
	    print()

	epf_lines = re.findall(r"Форма\((\d+)\)", json.dumps(
	    facts, ensure_ascii=False, default=str))
	print(f"# строки epf в ошибках: {sorted(set(epf_lines))} (клон: Выполнить(Код) — 3969)")
	print(f"# итог: проб {len(PROBES) + (1 if all(s == 'silence' for s in nested.values()) else 0)}, "
	      f"отклонений {deviations}")


if __name__ == "__main__":
	main()
