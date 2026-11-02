"""Базовый e2e-набор приёмки контура 1c-zcode.

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

from conftest import http_json, query


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
    assert "[ORG-" in data or "[INN-" in data, \
        f"представления Контрагентов пришли без токенов (анонимизация выключена?): {data[:200]}"


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
