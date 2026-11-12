/**
 * Покрытие корпуса: каждый гейт контура имеет векторный корпус.
 *
 * spec: Т8 — «векторы `tests/vectors/` покрывают 11 гейтов»; после снятия
 * python-эталона векторы — единственный носитель регрессии, и молчаливо
 * потерянный корпус означал бы молчаливо потерянную регрессию.
 * @module tests/parity/corpus.spec
 */

import { readdirSync, readFileSync } from 'node:fs'

import { MARKERS } from './classify.js'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/** Одиннадцать гейтов контура — состав спеки (решение 11). */
const GATES: readonly string[] = [
  'unica-source',
  'doc',
  'mcp',
  'bsl-style',
  'bsl-module',
  'anon',
  'retry',
  'worktree-hint',
  'session-clean',
  'session-context',
  'kd-session-start',
]

const VECTORS_DIR = new URL('../vectors/', import.meta.url)
const GATES_DIR = new URL('../../src/gates/', import.meta.url)

describe('покрытие корпуса векторами', () => {
  it('каждый из 11 гейтов имеет непустой корпус', () => {
    for (const gate of GATES) {
      const url = new URL(`${gate}.json`, VECTORS_DIR)
      const corpus = JSON.parse(readFileSync(url, 'utf8')) as unknown[]
      expect(Array.isArray(corpus), gate).toBe(true)
      expect(corpus.length, `${gate}: пустой корпус`).toBeGreaterThan(0)
    }
  })

  it('набор корпусов совпадает с набором гейтов плагина', () => {
    const corpora = readdirSync(VECTORS_DIR)
      .filter((name) => name.endsWith('.json'))
      .map((name) => name.replace(/\.json$/, ''))
      .sort()
    // edits.ts — общий помощник правок, не гейт.
    const modules = readdirSync(GATES_DIR)
      .filter((name) => name.endsWith('.ts') && name !== 'edits.ts')
      .map((name) => name.replace(/\.ts$/, ''))
      .sort()
    expect(corpora).toEqual(modules)
    expect(corpora).toEqual([...GATES].sort())
  })

  it('каждый блокирующий класс причины имеет маркер в классификаторе', () => {
    // rebuild-rule — запасной класс блока без маркера (кейсы сверяют текст);
    // none/escalation-granted — классы пропуска, не блока.
    const blockClasses = [
      'source-edit', 'source-bash', 'worktree-uninit', 'ib-lock',
      'docs-unverified', 'mcp-unsafe', 'stand-lock', 'bsl-style',
      'bsl-module', 'anon-raw', 'retry-discipline',
    ]
    const marked = new Set(MARKERS.map((entry) => entry.code))
    for (const code of blockClasses) {
      expect(marked.has(code as never), `класс ${code} без маркера`).toBe(true)
    }
    for (const entry of MARKERS) {
      expect(entry.marker.length, `пустой маркер ${entry.code}`).toBeGreaterThan(0)
    }
  })

  it('идентификаторы кейсов уникальны во всём корпусе', () => {
    const ids: string[] = []
    for (const gate of GATES) {
      const corpus = JSON.parse(
        readFileSync(new URL(`${gate}.json`, VECTORS_DIR), 'utf8'),
      ) as { id: string }[]
      ids.push(...corpus.map((vector) => vector.id))
    }
    expect(new Set(ids).size).toBe(ids.length)
  })
})
