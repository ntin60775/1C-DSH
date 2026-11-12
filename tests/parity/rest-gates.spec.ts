/**
 * Корпус гейтов T4: `anon`, `retry`, `worktree-hint`, `session-clean`,
 * `session-context`, `kd-session-start`.
 *
 * Каждый сценарий проигрывается на TS-гейте против ожиданий вектора
 * (`tests/vectors/`): решение, класс причины, фрагменты причины и заметок,
 * побочные эффекты (файлы состояния, маркер станции kd). Ожидания
 * заморожены с python-эталона до его снятия в Т8.
 *
 * spec: Т4 — «паритет-тесты шести гейтов зелёные; векторы — в
 * `tests/vectors/`»; Т8 — регрессия живёт в векторах без python.
 * @module tests/parity/rest-gates.spec
 */

import { existsSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import {
  cleanup,
  markerState,
  prepare,
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
        const runtime = prepare(scenario)
        try {
          for (const [index, step] of scenario.steps.entries()) {
            const where = `${scenario.id} шаг ${index + 1} (${step.phase})`
            const ts = await stepTs(scenario.gate, step, runtime)

            expect(ts.decision, `${where}: ${ts.text.slice(0, 300)}`).toBe(step.expect.decision)
            if (step.expect.decision === 'deny' || step.expect.decision === 'block') {
              expect(ts.code, `${where}: класс причины`).toBe(step.expect.code)
            }
            for (const fragment of step.expect.reasonIncludes ?? []) {
              expect(ts.text, `${where}: причина без «${fragment}»`).toContain(fragment)
            }
            for (const fragment of step.expect.noticeIncludes ?? []) {
              expect(ts.text, `${where}: заметка без «${fragment}»`).toContain(fragment)
            }
            if (step.expect.noticeAbsent === true) {
              expect(ts.text, `${where}: гейт должен молчать`).toBe('')
            }
          }

          const after = scenario.expectAfter
          if (after !== undefined) {
            const base = scenario.gate === 'session-clean' ? runtime.fixture.stateDir : runtime.fixture.root
            for (const relative of after.absent ?? []) {
              expect(existsSync(join(base, relative)), `${relative} должен быть удалён`).toBe(false)
            }
            for (const relative of after.present ?? []) {
              expect(existsSync(join(base, relative)), `${relative} должен остаться`).toBe(true)
            }
            if (after.marker !== undefined) {
              expect(await markerState(join(runtime.fixture.root, 'marker'), after.marker), 'маркер станции').toBe(after.marker)
            }
          }
        } finally {
          cleanup(runtime)
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
