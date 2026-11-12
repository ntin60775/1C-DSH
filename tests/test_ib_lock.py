#!/usr/bin/env python3
"""Тесты scripts/ib_lock.py — слой замков python/bash-скриптов контура.

Цели (правило качества — .agents/skills/testing/references/quality-rule.md):
  spec: tickets/dsh-contour/T8-pilot.md — перенос ib_lock из reference/ в
        scripts/ с DSH-контрактом состояния; формат замка совпадает с
        src/lock.ts (без учёток в файле, stand — pid-владелец);
  invariant: stand-замок атомарен (жёсткая ссылка), живой чужой владелец
        даёт busy, мёртвый освобождает; ключ stand не зависит от Usr/Pwd;
        замок ИБ пишется без учёток, режим 0600, TTL выметается;
  target: базовая половина ib_lock писала Usr/Pwd в файл состояния
        неатомарно — находка панели Т8 (P2), формат приведён к контракту.

Сеть не нужна; процессы — короткие sleep-заглушки владельцев.
Запуск: python3 tests/test_ib_lock.py
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import ib_lock  # noqa: E402


def main() -> int:
	with tempfile.TemporaryDirectory(prefix="ib-lock-") as tmp:
		os.environ["DSH_1C_STATE_DIR"] = tmp
		conn = "Srvr=host;Ref=erp;Usr=u;Pwd=secret;"

		# ── корень состояния: DSH-переменная (spec: решение 7, Т10) ──
		check("корень: DSH_1C_STATE_DIR задаёт корень состояния",
		      str(ib_lock.state_root()) == tmp, str(ib_lock.state_root()))

		# ── литералы ключей: заморожены python-писателем, TS-сторона
		# сверяет тот же литерал (tests/unit/lock.spec.ts) — дрейф любой
		# стороны краснит её тест (находка панели Т8, P2).
		check("ключ иб: литерал 57db9fe7…",
		      ib_lock.lock_key("Srvr=host;Ref=erp;", "/tree/a")
		      == "57db9fe786460ef38252e441c37967ddf24df939",
		      ib_lock.lock_key("Srvr=host;Ref=erp;", "/tree/a"))
		check("ключ stand: литерал 48b716fb…",
		      ib_lock.stand_lock_key("Srvr=host;Ref=erp;Usr=u;Pwd=p;", "/tree/a")
		      == "48b716fbf05cbc86e9c66e66017f0bfe9f9c8c2c",
		      ib_lock.stand_lock_key("Srvr=host;Ref=erp;Usr=u;Pwd=p;", "/tree/a"))

		# ── stand: ключ не зависит от учёток ──
		k1 = ib_lock.stand_lock_key(conn, "/tree/a")
		k2 = ib_lock.stand_lock_key("Srvr=host;Ref=erp;Usr=other;Pwd=x;", "/tree/a")
		check("stand: смена пароля не создаёт второй ключ", k1 == k2, f"{k1} != {k2}")

		# ── stand: захват, формат записи, busy, мёртвый владелец ──
		owner = subprocess.Popen(["sleep", "30"])
		status, holder = ib_lock.acquire_stand_lock("/tree/a", conn, owner.pid)
		check("stand: первый захват ok", status == "ok", status)
		path = ib_lock._stand_lock_path(conn, "/tree/a")
		record = json.loads(path.read_text(encoding="utf-8"))
		check("stand: формат python-писателя (kind/pid, без session_id/expires_ts)",
		      record.get("kind") == "stand" and record.get("pid") == owner.pid
		      and "session_id" not in record and "expires_ts" not in record,
		      str(record))
		check("stand: учёток в файле нет",
		      "secret" not in path.read_text(encoding="utf-8")
		      and "Usr=" not in record.get("connection", ""),
		      record.get("connection", ""))
		status, holder = ib_lock.acquire_stand_lock("/tree/b", conn, os.getpid())
		check("stand: живой чужой владелец — busy с держателем",
		      status == "busy" and holder.get("pid") == owner.pid,
		      f"{status} {holder}")
		status, _ = ib_lock.acquire_stand_lock("/tree/a", conn, owner.pid)
		check("stand: свой pid продлевает без конфликта", status == "ok", status)
		owner.kill()
		owner.wait()
		status, _ = ib_lock.acquire_stand_lock("/tree/b", conn, os.getpid())
		check("stand: мёртвый владелец освобождает замок", status == "ok", status)
		ib_lock.release_stand_lock("/tree/b", conn, os.getpid())
		check("stand: выпуск владельцем убирает файл",
		      ib_lock.stand_lock_status("/tree/b", conn) is None, "замок остался")

		# ── stand: статус не читает мёртвого владельца ──
		dead = subprocess.Popen(["sleep", "0"])
		dead.wait()
		ib_lock.acquire_stand_lock("/tree/c", conn, dead.pid)
		check("stand: статус с мёртвым pid — None",
		      ib_lock.stand_lock_status("/tree/c", conn) is None, "прочитан мёртвый")

		# ── замок ИБ: формат контракта, TTL, продление ──
		status, _ = ib_lock.acquire_base_lock("/tree/a", conn, "build", "s1")
		check("иб: захват ok", status == "ok", status)
		bpath = ib_lock._lock_path(conn, "/tree/a")
		brecord = json.loads(bpath.read_text(encoding="utf-8"))
		check("иб: учёток в файле нет (контракт src/lock.ts)",
		      "secret" not in bpath.read_text(encoding="utf-8")
		      and "Usr=" not in brecord.get("connection", ""),
		      brecord.get("connection", ""))
		mode = stat.S_IMODE(bpath.stat().st_mode)
		check("иб: режим файла 0600", mode == 0o600, oct(mode))
		started = brecord["started_at"]
		time.sleep(0.01)
		status, _ = ib_lock.acquire_base_lock("/tree/a", conn, "load", "s1")
		brecord2 = json.loads(bpath.read_text(encoding="utf-8"))
		check("иб: своё дерево продлевает, started_at сохранён",
		      status == "ok" and brecord2["started_at"] == started,
		      f"{status} {brecord2['started_at']} != {started}")
		status, holder = ib_lock.acquire_base_lock("/tree/b", conn, "build", "s2")
		check("иб: чужое дерево — busy", status == "busy"
		      and holder.get("tree") == "/tree/a", f"{status} {holder}")
		ib_lock.release_base_lock("/tree/a", conn, "s1")
		check("иб: выпуск владельцем убирает файл", not bpath.exists(), "файл остался")

		# ── TTL: просроченный выметается ──
		stale = ib_lock._lock_path("Srvr=host;Ref=stale;", "/tree/z")
		stale.parent.mkdir(parents=True, exist_ok=True)
		stale.write_text(json.dumps({"expires_ts": 1}), encoding="utf-8")
		removed = ib_lock.purge_stale_locks()
		check("иб: просроченный замок выметен", removed >= 1 and not stale.exists(),
		      f"removed={removed}")

		return summary()


if __name__ == "__main__":
	sys.exit(main())
