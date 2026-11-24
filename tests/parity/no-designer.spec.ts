/**
 * Корпус гейта `no-designer`: запрет Конфигуратора агенту.
 *
 * Сценарии проигрываются TS-гейтом против ожиданий вектора
 * (`tests/vectors/no-designer.json`): решение, класс причины, фрагменты
 * причины. Прямые запуски и make блокируются, чтение и ENTERPRISE — нет.
 * @module tests/parity/no-designer.spec
 */

import { describe, expect, it } from 'vitest'

import { classifyText, type ReasonClass } from './classify.js'
import { cleanup, loadScenarios, prepare, stepTs, type Scenario } from './scenario.js'

const corpus = new URL('../vectors/no-designer.json', import.meta.url)

for (const scenario of loadScenarios(corpus)) {
  describe(`no-designer/${scenario.id}`, () => {
    it(scenario.describe, async () => {
      const runtime = prepare(scenario as Scenario)
      try {
        for (const [index, step] of scenario.steps.entries()) {
          const ts = await stepTs(scenario.gate, step, runtime)
          const where = `${scenario.id} шаг ${index + 1} (${step.phase})`
          expect(ts.decision, `${where}: ${ts.text.slice(0, 300)}`).toBe(step.expect.decision)
          if (step.expect.decision === 'deny') {
            expect(ts.code, where).toBe(step.expect.code as ReasonClass)
            expect(classifyText(2, ts.text), `${where}: текст без маркера класса`).toBe(step.expect.code as ReasonClass)
          }
          for (const fragment of step.expect.reasonIncludes ?? []) {
            expect(ts.text, `${where}: причина без «${fragment}»`).toContain(fragment)
          }
        }
      } finally {
        cleanup(runtime)
      }
    })
  })
}
