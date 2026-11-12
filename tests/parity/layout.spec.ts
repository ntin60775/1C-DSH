/**
 * Т6: один и тот же корпус гейтов на двух раскладках.
 *
 * Критерий тикета — «проект на `.dsh/1c` и проект на `.zcode/1c` проходит
 * одинаковый набор гейтов». Корпус `tests/vectors` написан на legacy
 * `.zcode`; здесь каждый сценарий, у которого есть проектные входы,
 * проигрывается ещё раз с этими входами в каноне `.dsh/1c` и сверяется с
 * теми же ожиданиями сценария, что и legacy-прогон.
 *
 * Сценарии без проектных входов сюда не попадают: у них канон и legacy
 * дают один и тот же прогон, и проверка доказывала бы пустоту.
 *
 * spec: Т6 — «Проект на `.dsh/1c` и проект на `.zcode/1c` проходят
 * одинаковый набор гейтов».
 * @module tests/parity/layout.spec
 */

import { existsSync } from 'node:fs'
import { join } from 'node:path'

import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { createStyleChecker, ENGINE_ENV } from '../../src/style-checker.js'
import { seedStandLock } from '../helpers/locks.js'
import { classifyText, type ReasonClass } from './classify.js'
import { cleanup, loadScenarios, prepare, substitute, stepTs, type Scenario } from './scenario.js'

const STUB = new URL('../helpers/fake-style-engine.mjs', import.meta.url).pathname

/** Корпуса сценариев гейтов T3. */
const CORPORA: readonly { name: string; url: URL }[] = [
  { name: 'doc', url: new URL('../vectors/doc.json', import.meta.url) },
  { name: 'mcp', url: new URL('../vectors/mcp.json', import.meta.url) },
  { name: 'bsl-style', url: new URL('../vectors/bsl-style.json', import.meta.url) },
  { name: 'bsl-module', url: new URL('../vectors/bsl-module.json', import.meta.url) },
]

/** Несёт ли сценарий проектные входы, которые переезжают в канон. */
function hasLayoutInputs(scenario: Scenario): boolean {
  if (scenario.fixture?.contour !== undefined) return true
  return Object.keys(scenario.fixture?.write ?? {}).some((path) => path.startsWith('tree/.zcode/'))
}

let previousEngine: string | undefined

beforeAll(() => {
  previousEngine = process.env[ENGINE_ENV]
  process.env[ENGINE_ENV] = STUB
})

afterAll(() => {
  if (previousEngine === undefined) delete process.env[ENGINE_ENV]
  else process.env[ENGINE_ENV] = previousEngine
})

for (const corpus of CORPORA) {
  const scenarios = loadScenarios(corpus.url)
  const withInputs = scenarios.filter(hasLayoutInputs)

  describe(`канон .dsh/1c · корпус ${corpus.name}`, () => {
    // Корпус без проектных входов канон-прогона не имеет: у doc и bsl-style
    // решение зависит от текста вызова, а не от раскладки. Такой корпус
    // пропускается явно, а не зеленеет пустым прогоном.
    if (withInputs.length === 0) it.skip(`нет сценариев с проектными входами (${scenarios.length} шт.)`, () => {})

    for (const scenario of withInputs) {
      it(scenario.describe, async () => {
        const checker = scenario.gate === 'bsl-style' ? createStyleChecker() : undefined
        const runtime = prepare(scenario, checker, 'canon')
        try {
          // Входы обязаны лежать именно в каноне: иначе прогон доказывал бы
          // legacy-раскладку под именем канонической.
          const tree = runtime.paths.projectRoot
          expect(existsSync(join(tree, '.dsh', '1c')), 'входы не в .dsh/1c').toBe(true)
          expect(existsSync(join(tree, '.zcode')), 'остался legacy-каталог').toBe(false)
          expect(runtime.project, 'канон не распознан как проект контура').not.toBeNull()
          if (scenario.seedStandLock !== undefined) {
            seedStandLock({
              tree,
              connection: substitute(scenario.seedStandLock.connection, runtime.fixture.root),
              pid: process.pid,
              stateDir: runtime.fixture.stateDir,
            })
          }
          for (const [index, step] of scenario.steps.entries()) {
            const ts = await stepTs(scenario.gate, step, runtime)
            const where = `${scenario.id} шаг ${index + 1} (${step.phase}) на .dsh/1c`
            expect(ts.decision, `${where}: ${ts.text.slice(0, 300)}`).toBe(step.expect.decision)
            if (step.expect.decision === 'deny' || step.expect.decision === 'block') {
              const tsClass: ReasonClass = step.expect.decision === 'deny'
                ? ts.code as ReasonClass
                : classifyText(2, ts.text)
              expect(tsClass, where).toBe(step.expect.code as ReasonClass)
              // Класс по тексту причины — независимая сверка словаря маркеров.
              expect(classifyText(2, ts.text), `${where}: класс по тексту`).toBe(step.expect.code as ReasonClass)
              for (const fragment of step.expect.reasonIncludes ?? []) {
                expect(ts.text, where).toContain(fragment)
              }
            } else {
              for (const fragment of step.expect.noticeIncludes ?? []) {
                expect(ts.text, where).toContain(fragment)
              }
            }
          }
        } finally {
          cleanup(runtime)
        }
      })
    }
  })
}