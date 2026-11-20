---
node_type: index
title: Спеки юнитов контура
service: _platform
status: active
updated: 2026-11-19
---

# Спеки юнитов

Спека — durable-план одного юнита: цель, готово, рамки, ограничения,
контекст. Состояние исполнения — [../ops/continue.md](../ops/continue.md),
тикеты — в `tickets/<slug>/`. Статус frontmatter — по словарю онтологии
(`active`, `draft`, `deprecated`, `archived`); утверждение план-гейта и
приёмка юнита фиксируются в теле спеки и в списках ниже.

## Открытые

- [team-delivery-preset-ids.md](team-delivery-preset-ids.md) — шов
  доставки контура для стековых пресетов 1С (юнит `team-profiles`
  ntin, тикет 05): T1 принят 14.11; живая GUI-проба — ntin
  `tickets/team-profiles/07` (нужен перезапуск хоста).

## Завершённые

- [e2e-client-lifecycle.md](e2e-client-lifecycle.md) — жизненный цикл
  тест-клиентов e2e (ишью #56): Т1–Т12 приняты, юнит закрыт 16.11.
- [dsh-contour.md](dsh-contour.md) — переезд контура с ZCode на DSH:
  нативный плагин, гейты, пресет, раскладка, doctor, пилот, парк.
  Т1–Т10 приняты, юнит закрыт 13.11; хвосты Т11–Т12 приняты 16.11.
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
