/**
 * Корпус `unica_source_gate`: TS-гейт против векторов.
 *
 * Один корпус канонических кейсов (`tests/vectors/unica-source.json`):
 * решение, класс причины и (когда задан) текст причины заморожены в
 * векторе. До снятия python-эталона в Т8 каждый кейс сверялся и с
 * эталоном через ZCode-адаптер; поля `oracle`/`oracleNote` в векторах
 * хранят ту форму вызова как происхождение кейса.
 *
 * spec: Т2 — «паритет-тест `unica_source_gate` зелёный на корпусе кейсов
 * эталона: решение и класс причины совпадают»; Т8 — регрессия живёт в
 * векторах без python.
 * @module tests/parity/unica-source.spec
 */

import { readFileSync, rmSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'

import { afterAll, describe, expect, it } from 'vitest'

import type { GateCall } from '../../src/contract.js'
import { evaluatePre, patchPaths } from '../../src/gates/unica-source.js'
import { resolveProject } from '../../src/paths.js'
import { createGateState } from '../../src/state.js'
import { createFixture, REBUILD_RULES } from '../helpers/fixture.js'
import { seedBaseLock } from '../helpers/locks.js'
import type { ReasonClass } from './classify.js'

/** Ожидание кейса. */
interface Expectation {
  readonly decision: 'allow' | 'deny'
  readonly code?: Exclude<ReasonClass, 'allow' | 'unknown'>
  readonly reasonIncludes?: readonly string[]
  readonly reasonEquals?: string
}

/** Один канонический кейс корпуса. */
interface Vector {
  readonly id: string
  readonly describe: string
  /** Пути, извлечённые из текста патча: для кейсов `apply_patch`. */
  readonly paths?: readonly string[]
  /** Пояснение к форме эталонного вызова (происхождение кейса). */
  readonly oracleNote?: string
  readonly cwd?: string
  readonly fixture?: {
    readonly write?: Readonly<Record<string, string>>
    readonly mkdir?: readonly string[]
    readonly contour?: unknown
    readonly layout?: 'zcode' | 'dsh'
  }
  readonly seedLock?: {
    readonly holderTree: string
    readonly fromTree: string
    readonly operation: string
    readonly sessionId: string
  }
  readonly call: { readonly tool: string; readonly args: unknown }
  readonly oracle?: { readonly tool_name: string; readonly tool_input: unknown }
  readonly expect: Expectation
}

const vectors = JSON.parse(
  readFileSync(new URL('../vectors/unica-source.json', import.meta.url), 'utf8'),
) as readonly Vector[]

const created: string[] = []
afterAll(() => {
  for (const root of created) rmSync(root, { recursive: true, force: true })
})

/** Подставить `$TMP` во все строки значения. */
function substitute<T>(value: T, root: string): T {
  if (typeof value === 'string') return value.replaceAll('$TMP', root) as T
  if (Array.isArray(value)) return value.map((item) => substitute(item, root)) as T
  if (typeof value === 'object' && value !== null) {
    const result: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(value)) result[key] = substitute(item, root)
    return result as T
  }
  return value
}

/** Каталог кейса: относительный путь считается от корня фикстуры. */
function caseCwd(value: string, root: string): string {
  const substituted = value.replaceAll('$TMP', root)
  return isAbsolute(substituted) ? substituted : join(root, substituted)
}

for (const vector of vectors) {
  describe(vector.id, () => {
    it(vector.describe, () => {
      const options = vector.fixture ?? {}
      const fixture = createFixture({
        ...options.write === undefined ? {} : { write: options.write },
        ...options.mkdir === undefined ? {} : { mkdir: options.mkdir },
        ...options.contour === undefined ? {} : { contour: options.contour === '$REBUILD_RULES' ? REBUILD_RULES : options.contour },
        ...options.layout === undefined ? {} : { layout: options.layout },
      })
      created.push(fixture.root)
      const sessionId = 's1'

      if (vector.seedLock !== undefined) {
        seedBaseLock({
          holderTree: caseCwd(vector.seedLock.holderTree, fixture.root),
          connectionSourceTree: caseCwd(vector.seedLock.fromTree, fixture.root),
          operation: vector.seedLock.operation,
          sessionId: vector.seedLock.sessionId,
          stateDir: fixture.stateDir,
        })
      }

      if (vector.paths !== undefined) {
        const patch = (vector.call.args as { input?: unknown }).input
        expect(typeof patch).toBe('string')
        expect(patchPaths(patch as string)).toEqual(vector.paths)
      }

      const cwd = caseCwd(vector.cwd ?? 'tree', fixture.root)
      const call: GateCall = {
        tool: vector.call.tool,
        args: substitute(vector.call.args, fixture.root),
        cwd,
      }
      const decision = evaluatePre(
        call,
        resolveProject(cwd),
        createGateState({ sessionId, stateDir: fixture.stateDir }),
      )

      expect(decision.kind).toBe(vector.expect.decision)
      if (decision.kind !== 'deny') return

      expect(decision.code).toBe(vector.expect.code)
      for (const fragment of vector.expect.reasonIncludes ?? []) {
        expect(decision.reason, `причина без «${fragment}»`).toContain(fragment)
      }
      if (vector.expect.reasonEquals !== undefined) {
        expect(decision.reason).toBe(vector.expect.reasonEquals)
      }
    })
  })
}

describe('корпус паритета', () => {
  it('идентификаторы кейсов уникальны', () => {
    const ids = vectors.map((vector) => vector.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('денай-кейсы задают класс причины', () => {
    const missing = vectors.filter((vector) => vector.expect.decision === 'deny' && vector.expect.code === undefined)
    expect(missing.map((vector) => vector.id)).toEqual([])
  })

  it('кейсы правила пересборки сверяют текст причины, а не только класс', () => {
    // Классификатор выводит rebuild-rule как запасной вариант для
    // неизвестного блока: без сверки текста такой кейс прошёл бы даром.
    const wordless = vectors.filter(
      (vector) => vector.expect.code === 'rebuild-rule' && vector.expect.reasonEquals === undefined,
    )
    expect(wordless.map((vector) => vector.id)).toEqual([])
  })

  it('кейсы apply_patch описывают пути патча', () => {
    const patches = vectors.filter((vector) => vector.call.tool === 'apply_patch')
    expect(patches.length).toBeGreaterThan(0)
    for (const vector of patches) {
      expect(vector.paths, vector.id).toBeDefined()
    }
  })
})
