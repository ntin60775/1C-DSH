/**
 * Паритет гейтов T3: `doc`, `mcp`, `bsl-style`, `bsl-module`.
 *
 * Корпуса — сценарии: шаги `pre`/`post` по порядку, потому что гейты ведут
 * состояние (флаг сверки, планы правок). Оба адаптера проигрывают один
 * сценарий: эталон — процессами хука со своим файловым состоянием, TS —
 * чистыми функциями с памятью сессии. Сверяются решение, класс причины и
 * названные в причине файлы.
 *
 * Стиль-гейт сверяется на ОДНОМ движке: заглушка `fake-style-engine.py`
 * подставляется обеим реализациям через `1C_STYLE_ENGINE`, поэтому
 * проверяется поведение гейта, а не чекера.
 *
 * spec: Т3 — «паритет-тесты четырёх гейтов зелёные на корпусе эталона».
 * @module tests/parity/gates.spec
 */

import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { createStyleChecker, ENGINE_ENV } from '../../src/style-checker.js'
import { classifyOracle, classifyText, type OracleClass } from './classify.js'
import { seedStandLock } from './oracle.js'
import { cleanup, loadScenarios, prepare, stepOracle, stepTs, substitute, type Scenario } from './scenario.js'

const STUB = new URL('../helpers/fake-style-engine.py', import.meta.url).pathname
const corpora: readonly { name: string; url: URL }[] = [
  { name: 'doc', url: new URL('../vectors/doc.json', import.meta.url) },
  { name: 'mcp', url: new URL('../vectors/mcp.json', import.meta.url) },
  { name: 'bsl-style', url: new URL('../vectors/bsl-style.json', import.meta.url) },
  { name: 'bsl-module', url: new URL('../vectors/bsl-module.json', import.meta.url) },
]

let previousEngine: string | undefined

beforeAll(() => {
  previousEngine = process.env[ENGINE_ENV]
  process.env[ENGINE_ENV] = STUB
})

afterAll(() => {
  if (previousEngine === undefined) delete process.env[ENGINE_ENV]
  else process.env[ENGINE_ENV] = previousEngine
})

for (const corpus of corpora) {
  for (const scenario of loadScenarios(corpus.url)) {
    describe(`${corpus.name}/${scenario.id}`, () => {
      it(scenario.describe, async () => {
        const checker = scenario.gate === 'bsl-style' ? createStyleChecker() : undefined
        const runtime = prepare(scenario as Scenario, checker)
        try {
          if (scenario.seedStandLock !== undefined) {
            seedStandLock({
              tree: runtime.paths.projectRoot,
              connection: substitute(scenario.seedStandLock.connection, runtime.fixture.root),
              pid: process.pid,
              legacyStateDir: runtime.fixture.legacyStateDir,
            })
          }
          for (const [index, step] of scenario.steps.entries()) {
            const ts = await stepTs(scenario.gate, step, runtime)
            const oracle = stepOracle(scenario.gate, step, runtime)
            const where = `${scenario.id} шаг ${index + 1} (${step.phase})`
            expect(oracle.decision, `${where}: эталон — ${oracle.text.slice(0, 300)}`).toBe(step.expect.decision)
            expect(ts.decision, `${where}: TS`).toBe(step.expect.decision)
            if (step.expect.decision === 'deny' || step.expect.decision === 'block') {
              const tsClass = step.expect.decision === 'deny' ? ts.code : classifyText(2, ts.text)
              expect(tsClass, where).toBe(step.expect.code as OracleClass)
              expect(
                classifyOracle({ exitCode: 2, stderr: oracle.text }),
                `${where}: класс эталона`,
              ).toBe(step.expect.code)
            }
            for (const fragment of step.expect.reasonIncludes ?? []) {
              expect(ts.text, `${where}: TS-причина без «${fragment}»`).toContain(fragment)
              expect(oracle.text, `${where}: причина эталона без «${fragment}»`).toContain(fragment)
            }
            for (const fragment of step.expect.noticeIncludes ?? []) {
              expect(ts.text, `${where}: заметка TS без «${fragment}»`).toContain(fragment)
              expect(oracle.text, `${where}: заметка эталона без «${fragment}»`).toContain(fragment)
            }
          }
        } finally {
          cleanup(runtime)
        }
      })
    })
  }
}

describe('корпус T3', () => {
  it('идентификаторы сценариев уникальны в корпусе и между корпусами', () => {
    const ids = corpora.flatMap((corpus) => loadScenarios(corpus.url).map((scenario) => scenario.id))
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('каждый шаг знает, что ждать', () => {
    for (const corpus of corpora) {
      for (const scenario of loadScenarios(corpus.url)) {
        expect(scenario.steps.length, scenario.id).toBeGreaterThan(0)
        for (const step of scenario.steps) {
          expect(['pre', 'post'], scenario.id).toContain(step.phase)
          expect(['allow', 'deny', 'accept', 'block'], scenario.id).toContain(step.expect.decision)
        }
      }
    }
  })
})
