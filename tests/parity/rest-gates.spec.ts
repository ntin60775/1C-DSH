/**
 * Паритет гейтов T4: `anon`, `retry`, `worktree-hint`, `session-clean`,
 * `session-context`, `kd-session-start`.
 *
 * Каждый сценарий проигрывается на двух независимых фикстурах: TS-гейт и
 * python-эталон. Сверяются решение, класс причины, фрагменты причины и
 * заметки, побочные эффекты (файлы состояния, маркер станции kd).
 *
 * spec: Т4 — «паритет-тесты шести гейтов зелёные; векторы — в
 * `tests/vectors/`».
 * @module tests/parity/rest-gates.spec
 */

import { existsSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import {
  cleanup,
  markerState,
  prepare,
  stepOracle,
  stepTs,
  type RestGate,
  type RestScenario,
} from './rest.js'
import { loadScenarios } from './scenario.js'

const corpora: readonly { readonly name: RestGate; readonly url: URL }[] = [
  { name: 'anon', url: new URL('../vectors/anon.json', import.meta.url) },
  { name: 'retry', url: new URL('../vectors/retry.json', import.meta.url) },
  { name: 'worktree-hint', url: new URL('../vectors/worktree-hint.json', import.meta.url) },
  { name: 'session-clean', url: new URL('../vectors/session-clean.json', import.meta.url) },
  { name: 'session-context', url: new URL('../vectors/session-context.json', import.meta.url) },
  { name: 'kd-session-start', url: new URL('../vectors/kd-session-start.json', import.meta.url) },
]

for (const corpus of corpora) {
  for (const scenario of loadScenarios(corpus.url) as RestScenario[]) {
    describe(`${corpus.name}/${scenario.id}`, () => {
      it(scenario.describe, async () => {
        const tsRuntime = prepare(scenario)
        const oracleRuntime = prepare(scenario)
        try {
          for (const [index, step] of scenario.steps.entries()) {
            const where = `${scenario.id} шаг ${index + 1} (${step.phase})`
            const ts = await stepTs(scenario.gate, step, tsRuntime)
            const oracle = stepOracle(scenario.gate, step, oracleRuntime)

            expect(oracle.decision, `${where}: эталон — ${oracle.result.stderr.slice(0, 300)}`).toBe(step.expect.decision)
            expect(ts.decision, `${where}: TS`).toBe(step.expect.decision)
            if (step.expect.decision === 'deny' || step.expect.decision === 'block') {
              expect(ts.code, `${where}: класс TS`).toBe(step.expect.code)
              expect(oracle.code, `${where}: класс эталона`).toBe(step.expect.code)
            }
            for (const fragment of step.expect.reasonIncludes ?? []) {
              expect(ts.text, `${where}: TS-причина без «${fragment}»`).toContain(fragment)
              expect(oracle.result.stderr, `${where}: причина эталона без «${fragment}»`).toContain(fragment)
            }
            for (const fragment of step.expect.noticeIncludes ?? []) {
              expect(ts.text, `${where}: заметка TS без «${fragment}»`).toContain(fragment)
              expect(oracle.notice, `${where}: заметка эталона без «${fragment}»`).toContain(fragment)
            }
            if (step.expect.noticeAbsent === true) {
              expect(ts.text, `${where}: TS должен молчать`).toBe('')
              expect(oracle.notice, `${where}: эталон должен молчать`).toBe('')
            }
          }

          const after = scenario.expectAfter
          if (after !== undefined) {
            const base = (runtime: typeof tsRuntime): string =>
              scenario.gate === 'session-clean' ? runtime.fixture.stateDir : runtime.fixture.root
            for (const runtime of [tsRuntime, oracleRuntime]) {
              const where = runtime === tsRuntime ? 'TS' : 'эталон'
              for (const relative of after.absent ?? []) {
                expect(existsSync(join(base(runtime), relative)), `${where}: ${relative} должен быть удалён`).toBe(false)
              }
              for (const relative of after.present ?? []) {
                expect(existsSync(join(base(runtime), relative)), `${where}: ${relative} должен остаться`).toBe(true)
              }
            }
            if (after.marker !== undefined) {
              expect(await markerState(join(tsRuntime.fixture.root, 'marker'), after.marker), 'маркер TS').toBe(after.marker)
              expect(await markerState(join(oracleRuntime.fixture.root, 'marker'), after.marker), 'маркер эталона').toBe(after.marker)
            }
          }
        } finally {
          cleanup(tsRuntime)
          cleanup(oracleRuntime)
        }
      })
    })
  }
}

describe('корпус T4', () => {
  it('идентификаторы сценариев уникальны между корпусами', () => {
    const ids = corpora.flatMap((corpus) => (loadScenarios(corpus.url) as RestScenario[]).map((scenario) => scenario.id))
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('каждый шаг знает, что ждать', () => {
    for (const corpus of corpora) {
      for (const scenario of loadScenarios(corpus.url) as RestScenario[]) {
        expect(scenario.steps.length, scenario.id).toBeGreaterThan(0)
        for (const step of scenario.steps) {
          expect(['pre', 'result', 'post', 'session'], scenario.id).toContain(step.phase)
          expect(['allow', 'deny', 'accept', 'block'], scenario.id).toContain(step.expect.decision)
        }
      }
    }
  })
})