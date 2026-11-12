---
node_type: index
title: Спеки юнитов контура
service: _platform
status: active
updated: 2026-11-12
---

# Спеки юнитов

Спека — durable-план одного юнита: цель, готово, рамки, ограничения,
контекст. Состояние исполнения — [../ops/continue.md](../ops/continue.md),
тикеты — в `tickets/<slug>/`. Статус `approved` в frontmatter — маркер
план-гейта метода OntoShip.

## Открытые

- [dsh-contour.md](dsh-contour.md) — переезд контура с ZCode на DSH:
  нативный плагин, гейты, пресет, раскладка, doctor, пилот, парк.
  Т1–Т9 приняты; Т10 — парк и реестр.
- [external-epf-loop.md](external-epf-loop.md) — цикл внешних epf
  (ишью #62): Т1, Т3 приняты; дальше Т2, Т4, Т5, Т6.
- [e2e-client-lifecycle.md](e2e-client-lifecycle.md) — жизненный цикл
  тест-клиентов e2e (ишью #56): Т1–Т5 приняты; дальше Т6–Т10.

## Завершённые

- [build-out-canon.md](build-out-canon.md) — канон раскладки `build/out`:
  закрыт 08.11.
- [1c-db-multistand-stage1.md](1c-db-multistand-stage1.md) — мультистенд
  1c-db, этап 1 (ишью #37): закрыт 06.11, релиз v0.4.93.
- [execute-fragment-limits.md](execute-fragment-limits.md) — границы
  execute-фрагмента 1c-db (ишью #58): закрыт 07.11.
- [ibcmd-delivery-supply.md](ibcmd-delivery-supply.md) — поставка скилла
  1c-ibcmd-delivery: закрыт 05.11, релиз v0.4.92.