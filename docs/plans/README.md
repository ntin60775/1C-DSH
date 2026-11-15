---
node_type: index
title: Спеки юнитов контура
service: _platform
status: active
updated: 2026-11-16
---

# Спеки юнитов

Спека — durable-план одного юнита: цель, готово, рамки, ограничения,
контекст. Состояние исполнения — [../ops/continue.md](../ops/continue.md),
тикеты — в `tickets/<slug>/`. Статус `approved` в frontmatter — маркер
план-гейта метода OntoShip.

## Открытые

- [e2e-client-lifecycle.md](e2e-client-lifecycle.md) — жизненный цикл
  тест-клиентов e2e (ишью #56): Т1–Т9 и Т11 приняты; Т10 — живая приёмка,
  закрывает юнит.
- [team-delivery-preset-ids.md](team-delivery-preset-ids.md) — шов
  доставки контура для стековых пресетов 1С (юнит `team-profiles`
  ntin, тикет 05): T1 принят 14.11; юнит ведётся вне этого репозитория.

## Завершённые

- [dsh-contour.md](dsh-contour.md) — переезд контура с ZCode на DSH:
  нативный плагин, гейты, пресет, раскладка, doctor, пилот, парк.
  Т1–Т10 приняты, юнит закрыт 13.11.
- [external-epf-loop.md](external-epf-loop.md) — цикл внешних epf
  (ишью #62): Т1–Т6 приняты, юнит закрыт 13.11.
- [build-out-canon.md](build-out-canon.md) — канон раскладки `build/out`:
  закрыт 08.11.
- [1c-db-multistand-stage1.md](1c-db-multistand-stage1.md) — мультистенд
  1c-db, этап 1 (ишью #37): закрыт 06.11, релиз v0.4.93.
- [execute-fragment-limits.md](execute-fragment-limits.md) — границы
  execute-фрагмента 1c-db (ишью #58): закрыт 07.11.
- [ibcmd-delivery-supply.md](ibcmd-delivery-supply.md) — поставка скилла
  1c-ibcmd-delivery: закрыт 05.11, релиз v0.4.92.
