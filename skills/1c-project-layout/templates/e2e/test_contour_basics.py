"""Базовый e2e-набор приёмки контура 1С на DSH.

Проверяет живой канал 1c-db по канону скилла 1c-db-data: живость прокси и
канала, прогрев словаря с токенами анонимизации, число без токена, структура
метаданных, валидатор навигационных ссылок. Только чтение — данные в базу
не пишутся, свип после прогона находит ноль остатков.

Граница набора: позитивный roundtrip ссылки (get_link_of_object →
get_object_by_link по реальному объекту) остаётся в ручном приёмочном наборе —
UUID недоступен языку запросов, добыча его тянет execute_code. Здесь
проверяется детерминированная часть контракта ссылок: e1cib-формат и отказ
на некорректный ввод.
"""
from urllib.parse import quote

import json
import os
import pathlib
import re
import subprocess
import urllib.error
import urllib.request

import pytest


# ── Канал 1c-db: адрес из .dsh/1c/contour.json, самоподъём ──
# Тесты читают живой канал через REST-фолбэк прокси и не пишут данные в базу.
# Фикстура поднимает канал, если стенд молчит или пришёл без анонимизации:
# целевое состояние — токенизированный канал, всегда и независимо от
# окружения вызвавшего. Импортов из conftest нет — набор встаёт и в проект
# со своим conftest.


def _find_root() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        if (cand / "v8project.yaml").is_file() or (cand / ".dsh" / "1c").is_dir():
            return cand
    raise RuntimeError("корень проекта контура не найден (нет v8project.yaml и .dsh/1c)")


ROOT = _find_root()
DSH_1C_ROOT = os.environ.get("DSH_1C_ROOT", "")


def _contour_script(name: str) -> pathlib.Path:
    """Скрипт контура из пакета: $DSH_1C_ROOT/scripts/<name>."""
    return pathlib.Path(DSH_1C_ROOT) / "scripts" / name


def _origin() -> str:
    """База url канала без пути (http://host:port) — из contour.json."""
    cfg = ROOT / ".dsh" / "1c" / "contour.json"
    contour = json.loads(cfg.read_text(encoding="utf-8")) if cfg.is_file() else {}
    url = (contour.get("1c_db") or {}).get("url") or ""
    if not url:
        raise RuntimeError(
            "в .dsh/1c/contour.json нет 1c_db.url — контур не разведён "
            "(навык 1c-project-bootstrap)")
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
    script = _contour_script("start_1c_db.sh")
    if not script.is_file():
        pytest.fail("стенд 1c-db молчит, а $DSH_1C_ROOT/scripts/start_1c_db.sh нет — "
                    "задай DSH_1C_ROOT (корень пакета контура)")
    env = dict(os.environ, CONTOUR_ANON="on")
    subprocess.run(["bash", str(script), str(ROOT), "--stop"],
                   env=env, timeout=120, capture_output=True)
    done = subprocess.run(["bash", str(script), str(ROOT), "--headless"],
                          env=env, timeout=560, capture_output=True, text=True)
    if done.returncode != 0:
        pytest.fail(f"подъём стенда не удался (exit {done.returncode}): "
                    f"{(done.stdout + done.stderr).strip()[-400:]}")


@pytest.fixture(scope="module")
def db_origin() -> str:
    """Канал с включённой анонимизацией — на время проб и после них.

    Молчащий стенд или канал без токенов поднимается заново штатным
    headless-подъёмом (CONTOUR_ANON="on" — независимо от окружения
    вызвавшего); в остальных случаях ничего не меняется. Подстройки под
    окружение нет: после модуля канал остаётся поднятым и токенизированным.
    """
    origin = _origin()
    api = f"{origin}/api/execute_query"
    if not _alive(origin):
        _raise_channel()
    elif not _anon_on(api):
        _raise_channel()
    assert _anon_on(api), \
        "канал поднят, но пробы персон не токенизируются — анонимизация не включилась"
    yield origin


@pytest.fixture(scope="module")
def db_api(db_origin: str) -> str:
    return f"{db_origin}/api/execute_query"


def test_health(db_origin: str):
    status, body = http_json(f"{db_origin}/health", timeout=10)
    assert status == 200, f"HTTP {status}: {body}"
    assert body.get("status") == "healthy", body


def test_status_endpoint(db_origin: str):
    status, body = http_json(f"{db_origin}/1c/status", timeout=10)
    assert status == 200, f"HTTP {status}: {body}"
    assert body.get("version") == 1, body
    assert "channel" in body, body


def test_warmup_tokens(db_api: str):
    data = query(db_api, "ВЫБРАТЬ ПЕРВЫЕ 5 Наименование ИЗ Справочник.Контрагенты")
    assert data.strip(), "база без Контрагентов — прогрев словаря не проверить"
    # Категория токена зависит от профиля базы (ORG/INN/STR/…); инвариант —
    # «токен, не сырое»: сырое наименование значит, что словарь не готов.
    assert re.search(r"\[[A-Z]+-\d+\]", data), \
        f"представления Контрагентов пришли сырыми (анонимизация выключена?): {data[:200]}"


def test_person_literal_tokenized(db_api: str):
    data = query(db_api, 'ВЫБРАТЬ "Иванов Пётр Сергеевич" КАК ПроверкаAnon')
    assert "[PER-" in data, f"литерал-персона пришла сырой: {data[:200]}"


def test_number_not_tokenized(db_api: str):
    data = query(db_api, "ВЫБРАТЬ 6*7 КАК Проверка")
    last = data.strip().splitlines()[-1].strip() if data.strip() else ""
    assert last == "42", f"число исказилось или токенизировалось: {data[:200]}"


def test_metadata_structure(db_origin: str):
    status, body = http_json(
        f"{db_origin}/api/get_metadata?filter={quote('Справочник.Контрагенты')}",
        timeout=60)
    assert status == 200, f"HTTP {status}: {body}"
    assert body.get("success"), f"метаданные не прочитаны: {body.get('error')}"


def test_link_validator_rejects_garbage(db_origin: str):
    status, _ = http_json(f"{db_origin}/api/get_object_by_link",
                          {"link": "http://не-ссылка"}, timeout=30)
    assert status == 422, "валидатор пропустил не-e1cib ссылку"
    status, _ = http_json(f"{db_origin}/api/get_link_of_object",
                          {"object_description": {"_objectRef": True}}, timeout=30)
    assert status == 422, "валидатор пропустил описание без UUID и типа"
