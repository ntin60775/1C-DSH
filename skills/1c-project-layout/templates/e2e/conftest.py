"""Фикстуры e2e-приёмки контура: адрес канала — из .zcode/1c/contour.json.

Тесты читают живой канал 1c-db через REST-фолбэк прокси (stdlib urllib) и не
пишут данные в базу. Канал для прогона поднимает фикстура, если стенд молчит
или работает без анонимизации: токенные пробы канона требуют включённой
анонимизации (CONTOUR_ANON=on), независимо от окружения вызвавшего (run_e2e.sh
экспортирует CONTOUR_ANON=off — фикстура перекрывает его явно).
"""
import json
import os
import pathlib
import subprocess
import urllib.error
import urllib.request

import pytest


def _find_root() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        if (cand / "v8project.yaml").is_file() or (cand / ".zcode" / "1c").is_dir():
            return cand
    raise RuntimeError("корень проекта контура не найден (нет v8project.yaml и .zcode/1c)")


ROOT = _find_root()


def _origin() -> str:
    """База url канала без пути (http://host:port) — из contour.json."""
    cfg = ROOT / ".zcode" / "1c" / "contour.json"
    contour = json.loads(cfg.read_text(encoding="utf-8")) if cfg.is_file() else {}
    url = (contour.get("1c_db") or {}).get("url") or ""
    if not url:
        raise RuntimeError("в .zcode/1c/contour.json нет 1c_db.url — прогони wire_config.py")
    return url.rsplit("/", 1)[0]


def http_json(url: str, payload=None, timeout: int = 180):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")
        try:
            return err.code, json.loads(body)
        except json.JSONDecodeError:
            return err.code, {"raw": body}


def query(api_url: str, sql: str) -> str:
    status, body = http_json(api_url, {"query": sql})
    assert status == 200, f"HTTP {status}: {body}"
    assert body.get("success"), f"запрос отклонён прокси: {body.get('error')}"
    return body.get("data") or ""


def _alive(origin: str) -> bool:
    try:
        status, _ = http_json(f"{origin}/health", timeout=5)
        return status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _anon_on(api_url: str) -> bool:
    probe = query(api_url, 'ВЫБРАТЬ "Иванов Пётр Сергеевич" КАК ПроверкаAnon')
    return "[PER-" in probe


def _raise_channel() -> None:
    script = ROOT / ".zcode" / "1c" / "scripts" / "start_1c_db.sh"
    if not script.is_file():
        pytest.fail("стенд 1c-db молчит, а .zcode/1c/scripts/start_1c_db.sh нет — "
                    "контур не разведён (навык 1c-project-bootstrap)")
    env = dict(os.environ, CONTOUR_ANON="on")
    subprocess.run(["bash", str(script), str(ROOT), "--stop"],
                   env=env, timeout=120, capture_output=True)
    done = subprocess.run(["bash", str(script), str(ROOT), "--headless"],
                          env=env, timeout=560, capture_output=True, text=True)
    if done.returncode != 0:
        pytest.fail(f"подъём стенда не удался (exit {done.returncode}): "
                    f"{(done.stdout + done.stderr).strip()[-400:]}")


@pytest.fixture(scope="session")
def db_origin() -> str:
    origin = _origin()
    if not _alive(origin) or not _anon_on(f"{origin}/api/execute_query"):
        _raise_channel()
        assert _anon_on(f"{origin}/api/execute_query"), \
            "канал поднят, но пробы персон не токенизируются — анонимизация не включилась"
    return origin


@pytest.fixture(scope="session")
def db_api(db_origin: str) -> str:
    return f"{db_origin}/api/execute_query"
