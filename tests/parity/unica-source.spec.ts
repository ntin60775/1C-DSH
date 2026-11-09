/**
 * Паритет `unica_source_gate`: TS-гейт против python-эталона.
 *
 * Один корпус канонических кейсов (`tests/vectors/unica-source.json`) и два
 * адаптера: ZCode-payload для эталона и DSH-вызов для TS-гейта. Сверяются
 * решение, класс причины и (когда задан) текст причины. Имена инструментов
 * различаются (`Write`/`Edit`/`ApplyPatch` против `write`/`edit`/`apply_patch`),
 * поэтому сверка идёт на канонических кейсах, а не на сырых payload.
 * @module tests/parity/unica-source.spec
 */

import { readFileSync, rmSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'

import { afterAll, describe, expect, it } from 'vitest'

import type { GateCall } from '../../src/contract.js'
import { evaluatePre } from '../../src/gates/unica-source.js'
import { resolveProject } from '../../src/paths.js'
import { createGateState } from '../../src/state.js'
import { createFixture, REBUILD_RULES } from '../helpers/fixture.js'
import { classifyOracle, type OracleClass } from './classify.js'
import { runOracle, seedPythonLock } from './oracle.js'

/** ZCode-форма вызова: имя инструмента и его аргументы. */
interface OracleCall {
  readonly tool_name: string
  readonly tool_input: unknown
}

/** Ожидание кейса. */
interface Expectation {
  readonly decision: 'allow' | 'deny'
  readonly code?: Exclude<OracleClass, 'allow' | 'unknown'>
  readonly reasonIncludes?: readonly string[]
  readonly reasonEquals?: string
}

/** Один канонический кейс корпуса. */
interface Vector {
  readonly id: string
  readonly describe: string
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
  readonly oracle?: OracleCall
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

/**
 * ZCode-адаптер по умолчанию: имя инструмента и его аргументы.
 *
 * `apply_patch` DSH несёт патч текстом, а ZCode-инструмент получал готовые
 * пути, поэтому такой кейс задаёт форму эталона явно.
 */
function oracleCallFor(vector: Vector): OracleCall {
  if (vector.oracle !== undefined) return vector.oracle
  const names: Record<string, string> = { write: 'Write', edit: 'Edit', bash: 'Bash' }
  const mapped = names[vector.call.tool]
  if (mapped === undefined) return { tool_name: vector.call.tool, tool_input: vector.call.args }
  return { tool_name: mapped, tool_input: vector.call.args }
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
        seedPythonLock({
          holderTree: caseCwd(vector.seedLock.holderTree, fixture.root),
          connectionSourceTree: caseCwd(vector.seedLock.fromTree, fixture.root),
          operation: vector.seedLock.operation,
          sessionId: vector.seedLock.sessionId,
          legacyStateDir: fixture.legacyStateDir,
        })
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

      const oracle = oracleCallFor(vector)
      const oracleResult = runOracle(
        {
          hook_event_name: 'PreToolUse',
          session_id: sessionId,
          tool_name: oracle.tool_name,
          tool_input: substitute(oracle.tool_input, fixture.root),
          cwd,
        },
        { stateDir: fixture.legacyStateDir, cwd },
      )
      const oracleClass = classifyOracle(oracleResult)

      expect(oracleClass, `эталон: ${oracleResult.stderr.slice(0, 400)}`).toBe(vector.expect.decision === 'allow' ? 'allow' : vector.expect.code)
      expect(decision.kind).toBe(vector.expect.decision)
      if (decision.kind !== 'deny') return

      expect(decision.code).toBe(oracleClass)
      for (const fragment of vector.expect.reasonIncludes ?? []) {
        expect(decision.reason, `TS-причина без «${fragment}»`).toContain(fragment)
        expect(oracleResult.stderr, `причина эталона без «${fragment}»`).toContain(fragment)
      }
      if (vector.expect.reasonEquals !== undefined) {
        expect(decision.reason).toBe(vector.expect.reasonEquals)
        expect(oracleResult.stderr.trim()).toBe(vector.expect.reasonEquals)
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
})
