---
node_type: index
title: База знаний контура 1С на DSH
service: _platform
status: active
updated: 2026-11-12
---

# База знаний контура 1С на DSH

Главный индекс KB. Документ — источник истины; `.gitmark/` и HTML-карта —
производные, не коммитятся. Поиск до чтения — скилл `kb-search`, правка
документов — скилл `kb-curate`. Модель типов и инвариантов —
[ontology.md](ontology.md).

## Установка и устройство

- [dsh-install.md](dsh-install.md) — установка контура на DSH с нуля:
  bundle, пресет `1c`, Юника, оснащение проекта и реестр парка, doctor.
- [dsh-gates.md](dsh-gates.md) — одиннадцать гейтов: события, решения,
  входы, состояние.
- [UNICA.md](UNICA.md) — Unica: версии, глобальная установка, скиллы и
  MCP-запись проекта.
- [KD-RULES.md](KD-RULES.md) — канал правил обмена КД 2/3 (MCP `1c-kd`):
  станция, разводка, вендор.

## Каноны и дизайны

- [TEXT-STYLE.md](TEXT-STYLE.md) — канон текстов контура: скиллы, правила,
  сообщения гейтов.
- [PROJECT-LAYOUT-SKILL.md](PROJECT-LAYOUT-SKILL.md) — дизайн навыка
  `1c-project-layout`: топология каталогов 1С-проекта.
- [BSL-REVIEW-JOIN-SPEC.md](BSL-REVIEW-JOIN-SPEC.md) — спека скрипта-джойна
  ревьюера стандартов `bsl_review_join.py`.
- [UNICA-GATE-BASH-WRITE-SPEC.md](UNICA-GATE-BASH-WRITE-SPEC.md) — спека
  обнаружения bash-записи в исходники гейтом `unica-source-gate`.

## Разделы

- [ops/](ops/README.md) — операционные процедуры: состояние работы и
  продолжение сессий.
- [plans/](plans/README.md) — спеки юнитов: открытые и завершённые.

## История (ZCode-слой снят)

- [migration-map.md](migration-map.md) — карта переноса 1c-omp → 1c-zcode:
  история порта, механики ZCode в репозитории больше нет.
- [OPEN-GAPS.md](OPEN-GAPS.md) — отклонения контура линии v0.4.x: история
  приёмки ZCode, актуальные границы — [UNICA.md](UNICA.md).