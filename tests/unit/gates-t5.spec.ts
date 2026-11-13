/**
 * Сужение гейта исходников под канон внешних epf/erf (юнит #62, Т5).
 *
 * spec: docs/plans/external-epf-loop.md, «Готово» п. 5 — `src/epf` и
 * `src/erf` удалены из `SOURCE_PREFIXES` (остаток `src/cf`, `src/cfe`,
 * `tests/epf`); bash-ветка читает тот же список. Ожидаемые значения —
 * из спеки и тикета Т5 (tickets/external-epf-loop/T5-gate-invariant.md),
 * не из кода гейта.
 * @module tests/unit/gates-t5.spec
 */

import { rmSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import type { GateCall } from '../../src/contract.js'
import { evaluatePre } from '../../src/gates/unica-source.js'
import { resolveProject, SOURCE_PREFIXES } from '../../src/paths.js'
import { createGateState } from '../../src/state.js'
import { createFixture } from '../helpers/fixture.js'

/** Собрать вызов инструмента. */
function call(tool: string, args: unknown, cwd: string): GateCall {
  return { tool, args, cwd }
}

/** Прогнать гейт на фикстуре. */
function run(
  fixture: { readonly tree: string; readonly stateDir: string },
  toolCall: GateCall,
): ReturnType<typeof evaluatePre> {
  const project = resolveProject(toolCall.cwd)
  const state = createGateState({ sessionId: 's1', stateDir: fixture.stateDir })
  return evaluatePre(toolCall, project, state)
}

describe('состав SOURCE_PREFIXES после сужения', () => {
  it('остаток: src/cf, src/cfe, tests/epf; внешние epf/erf выпали', () => {
    expect([...SOURCE_PREFIXES]).toEqual(['src/cf/', 'src/cfe/', 'tests/epf/'])
  })
})

describe('прямая правка внешних epf/erf проходит', () => {
  const cases: readonly {
    readonly name: string
    readonly tool: string
    readonly args: Record<string, unknown>
  }[] = [
    { name: 'write src/epf', tool: 'write', args: { file_path: 'src/epf/Обработка/Ext/ObjectModule.bsl', content: '' } },
    { name: 'edit src/epf', tool: 'edit', args: { file_path: 'src/epf/Обработка/Ext/ObjectModule.bsl', old_string: 'a', new_string: 'b' } },
    { name: 'write src/erf', tool: 'write', args: { file_path: 'src/erf/Отчёт/Отчёт.xml', content: '' } },
    { name: 'edit src/erf', tool: 'edit', args: { file_path: 'src/erf/Отчёт/Ext/ObjectModule.bsl', old_string: 'a', new_string: 'b' } },
    { name: 'apply_patch src/epf', tool: 'apply_patch', args: { input: '*** Begin Patch\n*** Add File: src/epf/Новая/Ext/ObjectModule.bsl\n+// модуль\n*** End Patch\n' } },
    { name: 'bash sed src/epf', tool: 'bash', args: { command: "sed -i 's/a/b/' src/epf/Обработка/epf.epf" } },
    { name: 'bash redirect src/erf', tool: 'bash', args: { command: 'echo x > src/erf/Отчёт/erf.erf' } },
  ]

  for (const c of cases) {
    it(`${c.name} — allow`, () => {
      const fixture = createFixture()
      const decision = run(fixture, call(c.tool, c.args, fixture.tree))
      expect(decision).toMatchObject({ kind: 'allow', code: 'none' })
      rmSync(fixture.root, { recursive: true, force: true })
    })
  }
})

describe('защита cf/cfe/tests-epf не ослаблена', () => {
  const cases: readonly {
    readonly name: string
    readonly tool: string
    readonly args: Record<string, unknown>
    readonly code: string
  }[] = [
    { name: 'write src/cf', tool: 'write', args: { file_path: 'src/cf/CommonModules/Модуль.bsl', content: '' }, code: 'source-edit' },
    { name: 'write src/cfe', tool: 'write', args: { file_path: 'src/cfe/Расширение/Module.bsl', content: '' }, code: 'source-edit' },
    { name: 'write tests/epf', tool: 'write', args: { file_path: 'tests/epf/Тест.epf', content: '' }, code: 'source-edit' },
    { name: 'bash sed tests/epf', tool: 'bash', args: { command: "sed -i 's/a/b/' tests/epf/Тест.epf" }, code: 'source-bash' },
    { name: 'bash redirect src/cf', tool: 'bash', args: { command: 'echo x >> src/cf/CommonModules/Модуль.bsl' }, code: 'source-bash' },
  ]

  for (const c of cases) {
    it(`${c.name} — deny ${c.code}`, () => {
      const fixture = createFixture()
      const decision = run(fixture, call(c.tool, c.args, fixture.tree))
      expect(decision).toMatchObject({ kind: 'deny', code: c.code })
      rmSync(fixture.root, { recursive: true, force: true })
    })
  }
})