---
node_type: index
title: Спеки юнитов контура
service: _platform
status: active
updated: 2026-11-25
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

## Отложенные

- [dap-debugging.md](dap-debugging.md) — станция `1c-dap`: отладка BSL из
  сессии агента (точки, стек, локальные переменные) поверх `unica dap` и
  `debug-server` раннера. План-гейт 20.11 пройден, юнит отложен 25.11:
  вендорный код закрывается временным расширением с логом, DAP добавляет
  только диагноз без гипотезы внутри нерасширяемых методов — не оправдывает
  станцию с состоянием. Условие возврата — в спеке.

## Завершённые

- [bsp-v017-contour.md](bsp-v017-contour.md) — граница БСП v0.17 в контуре:
  линтер слотов судит по декодированному значению, тексты `1c-contour` и
  `1c-bsl-review-standards` называют платформенное исключение, док-гниль про
  снятый пресет `1c` убрана. Выполнен 22.11, скоуп —
  только 1C-DSH.

- [org-regulations-delivery.md](org-regulations-delivery.md) — поставка
  орг-регламентов: team-паки контор в проекты парка, семантическая
  привязка, тихое обновление, фриланс-сценарий; скилл `pack-deploy` в
  contour-deploy, канон реестра org/regulations_pack/pack-archive.
  Т1–Т5 приняты, юнит закрыт 20.11; проза норм — отдельный проход.
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
