/**
 * Инвариант Т10: в поставляемом слое контура нет наследия ZCode.
 *
 * Критерий тикета — «grep `.zcode` по контуру (плагин, скрипты, скиллы,
 * команды, шаблоны, тесты) пусто; `ZCODE_1C_STATE_DIR` не упоминается».
 * Гард охраняет критерий от регрессии: сканирует поставляемый слой и
 * процессные доки (кроме архивных снимков, планов/оперативных записей и
 * тикетов, которым факты с путями положены по назначению). Процессный
 * слой `.agents/` не поставляется и не проверяется (правило AGENTS.md):
 * его тексты описывают харнесс ZCode как историю метода.
 *
 * Единственное именованное исключение приёмного grep — этот файл: гард
 * называет запрещённые маркеры, чтобы их искать. Приёмный grep запускается
 * с `--exclude=no-legacy.spec.ts`; все прочие носители обязаны быть чисты.
 *
 * invariant: tickets/dsh-contour/T10-park-registry.md#критерии-приёмки —
 * legacy-раскладки, переменная состояния и путь станции ZCode сняты.
 * @module tests/unit/no-legacy.spec
 */

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/** Корень сканирования: репозиторий; подмена для прогона на базовом коммите. */
const ROOT = process.env['DSH_1C_GUARD_ROOT'] ?? fileURLToPath(new URL('../..', import.meta.url))

/** Запрещённые маркеры наследия ZCode (критерий Т10 + путь станции). */
const MARKERS: readonly string[] = ['.zcode', 'ZCODE_1C_STATE_DIR', 'share/1c-zcode']

/** Каталоги, которые не сканируются: производные, вендор и процессный слой. */
const SKIP_DIRS = new Set(['node_modules', '__pycache__', '.git', 'lib', 'vendor',
  '.agents', '.gitmark', '.scratch'])

/** Этот файл называет маркеры — единственное именованное исключение. */
const SELF = 'tests/unit/no-legacy.spec.ts'

/** Расширения поставляемого слоя и доков. */
const EXT = /\.(md|py|json|ts|sh|yaml|yml|template|txt|mjs)$/

/** Архивные снимки и процессные записи: факты с путями — их назначение. */
const DOC_EXCLUDE = new Set(['migration-map.md', 'OPEN-GAPS.md'])

function files(dir: string, rel: string): string[] {
  const result: string[] = []
  let entries: string[]
  try {
    entries = readdirSync(dir)
  } catch {
    return result
  }
  for (const name of entries) {
    if (SKIP_DIRS.has(name)) continue
    const path = join(dir, name)
    const relative = rel === '' ? name : `${rel}/${name}`
    if (statSync(path).isDirectory()) {
      // Процессные записи (планы, оперативные, тикеты) фиксируют факты
      // с путями — это их назначение, они вне критерия Т10.
      if (relative === 'docs/plans' || relative === 'docs/ops' || relative === 'tickets') continue
      result.push(...files(path, relative))
    } else if (EXT.test(name) && !DOC_EXCLUDE.has(name) && relative !== SELF) {
      result.push(relative)
    }
  }
  return result
}

describe('инвариант Т10: поставляемый слой без наследия ZCode', () => {
  it('ни одного маркера legacy в контуре и доках (кроме архивов и этого гарда)', () => {
    const hits: string[] = []
    const scanned = files(ROOT, '')
    // Гард обязан видеть поставляемый слой, иначе проверка доказывала бы пустоту.
    expect(scanned.length).toBeGreaterThan(100)
    expect(scanned).toContain('src/paths.ts')
    expect(scanned).toContain('scripts/doctor.py')
    for (const relative of scanned) {
      const text = readFileSync(join(ROOT, relative), 'utf8')
      for (const marker of MARKERS) {
        if (text.includes(marker)) hits.push(`${relative}: ${marker}`)
      }
    }
    expect(hits).toEqual([])
  })
})
