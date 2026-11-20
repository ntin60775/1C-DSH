/**
 * Инвариант Т4 юнита org-regulations-delivery: контур не читает паки
 * регламентов. Лексический инвариант — в `src/` нет литерала `.agents`:
 * это ловит и прямую строку `.agents/packs`, и сборку пути
 * `join('.agents', 'packs')`. Семантический скан «чтений» пропустил бы
 * join; лексический краснеет и от комментария — осознанно, граница
 * названа в спеке (Ограничения, T21 проекций).
 *
 * Оркестратор contour-deploy читает паки легитимно — граница про
 * 1C-DSH, не про оркестратор. Скан только `src/`: скиллы и скрипты
 * контура вендорят свои паки (стиль, БСП) в `.dsh/1c`, не в `.agents`.
 *
 * invariant: tickets/org-regulations-delivery/T4-invariant.md#критерии-приёмки —
 * src/ не содержит литерала .agents; мутация (резолв пути) краснит.
 * docs/plans/org-regulations-delivery.md — Ограничения.
 * @module tests/unit/no-agents-packs.spec
 */

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/** Корень сканирования: репозиторий; подмена для прогона на базовом коммите. */
const ROOT = process.env['DSH_1C_GUARD_ROOT'] ?? fileURLToPath(new URL('../..', import.meta.url))

/** Запрещённый литерал: любой вход в паки стандартов OntoShip. */
const MARKER = '.agents'

function files(dir: string, rel: string): string[] {
  const result: string[] = []
  let entries: string[]
  try {
    entries = readdirSync(dir)
  } catch {
    return result
  }
  for (const name of entries) {
    if (name === 'node_modules' || name === '.git') continue
    const path = join(dir, name)
    const relative = rel === '' ? name : `${rel}/${name}`
    if (statSync(path).isDirectory()) {
      result.push(...files(path, relative))
    } else {
      result.push(relative)
    }
  }
  return result
}

describe('инвариант Т4: src/ контура без литерала .agents', () => {
  it('ни одного вхождения .agents в src/', () => {
    const scanned = files(join(ROOT, 'src'), 'src')
    // Гард обязан видеть слой, иначе проверка доказывала бы пустоту.
    expect(scanned.length).toBeGreaterThan(5)
    expect(scanned).toContain('src/paths.ts')
    expect(scanned).toContain('src/index.ts')
    expect(scanned).toContain('src/contract.ts')
    const hits: string[] = []
    for (const relative of scanned) {
      const text = readFileSync(join(ROOT, relative), 'utf8')
      if (text.includes(MARKER)) hits.push(relative)
    }
    expect(hits).toEqual([])
  })
})