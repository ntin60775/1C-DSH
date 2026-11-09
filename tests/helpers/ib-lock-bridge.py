#!/usr/bin/env python3
"""Мост к эталонным замкам ИБ: разложить замок так, как его кладёт python.

Нужен паритет-стенду: TS-гейт обязан увидеть замок, поставленный эталоном, и
наоборот. Замок в фикстуру кладёт сам эталон — формат не переописывается в
тесте, иначе проверка стала бы тавтологией.

    acquire <tree> <operation> <session> <connection-source-tree>
    stand-acquire <tree> <connection> <pid>
    resolve <tree>

Каталог гейтов — в `CONTOUR_GATES`, корень состояния — в `ZCODE_1C_STATE_DIR`.
"""
import json
import os
import sys

sys.path.insert(0, os.environ["CONTOUR_GATES"])

from ib_lock import (  # noqa: E402
	acquire_base_lock,
	acquire_stand_lock,
	resolve_infobase_connection,
)


def main() -> int:
	command = sys.argv[1]
	if command == "acquire":
		tree, operation, session = sys.argv[2], sys.argv[3], sys.argv[4]
		connection = resolve_infobase_connection(sys.argv[5])
		if not connection:
			print(json.dumps({"error": "строка подключения не найдена", "tree": sys.argv[5]}))
			return 1
		status, holder = acquire_base_lock(tree, connection, operation, session)
		print(json.dumps({"status": status, "connection": connection, "holder": holder}, ensure_ascii=False))
		return 0
	if command == "stand-acquire":
		tree, connection, pid = sys.argv[2], sys.argv[3], int(sys.argv[4])
		status, holder = acquire_stand_lock(tree, connection, pid)
		print(json.dumps({"status": status, "holder": holder}, ensure_ascii=False))
		return 0
	if command == "resolve":
		print(json.dumps({"connection": resolve_infobase_connection(sys.argv[2])}, ensure_ascii=False))
		return 0
	print(json.dumps({"error": f"неизвестная команда: {command}"}))
	return 1


if __name__ == "__main__":
	sys.exit(main())
