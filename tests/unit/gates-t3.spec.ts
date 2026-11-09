/**
 * Гейты T3: раскладка `.dsh`, поверхность из `contour.json`, чистые разборы.
 *
 * Паритет с python-эталоном — отдельный корпус (`tests/parity/gates.spec.ts`).
 * Здесь то, чего у эталона нет или что он проверял только на legacy-раскладке:
 * приоритет `.dsh`, override поверхности, порт чекера стиля.
 *
 * invariant: новая раскладка `.dsh` читается первой, legacy `.zcode` — вторым.
 * @module tests/unit/gates-t3.spec
 */

import { rmSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import type { GateCall } from '../../src/contract.js'
import { checkBsl, checkRights, evaluatePost as modulePost, escalationSkips } from '../../src/gates/bsl-module.js'
import { contourOf, evaluatePre as mcpPre, profileConnection } from '../../src/gates/mcp.js'
import { docsResultIsMeaningful } from '../../src/gates/doc.js'
import { resolveProject } from '../../src/paths.js'
import { createGateState } from '../../src/state.js'
import { createStyleChecker } from '../../src/style-checker.js'
import { createFixture } from '../helpers/fixture.js'

/** Состояние гейта на фикстуре. */
function stateOf(fixture: { stateDir: string }) {
  const store = new Map<string, unknown>()
  return createGateState({
    sessionId: 's1',
    stateDir: fixture.stateDir,
    memory: {
      get: <T>(key: string): T | undefined => store.get(key) as T | undefined,
      set: (key: string, value: unknown): void => {
        store.set(key, value)
      },
    },
  })
}

describe('mcp: раскладка и поверхность', () => {
  it('признак контура читается из .dsh/contour', () => {
    const fixture = createFixture({ write: { 'tree/.dsh/contour': 'test\n' } })
    expect(contourOf(resolveProject(fixture.tree))).toBe('test')
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('.dsh/contour перекрывает legacy .zcode/contour', () => {
    const fixture = createFixture({
      write: { 'tree/.zcode/contour': 'test\n', 'tree/.dsh/contour': 'prod\n' },
    })
    expect(contourOf(resolveProject(fixture.tree))).toBe('prod')
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('allowlist в .dsh разрешает инструмент', () => {
    const fixture = createFixture({ write: { 'tree/.dsh/mcp-write-allow.txt': 'execute_code\n' } })
    const call: GateCall = { tool: 'mcp__1c-db__execute_code', args: { query: 'x' }, cwd: fixture.tree }
    expect(mcpPre(call, resolveProject(fixture.tree), stateOf(fixture))).toMatchObject({ kind: 'allow' })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('override опасных инструментов из contour.json дополняет список', () => {
    const fixture = createFixture({
      contour: { mcp_gate: { dangerous: { '1c-testpilot': ['tc_danger'] } } },
    })
    const call: GateCall = { tool: 'mcp__1c-testpilot__tc_danger', args: {}, cwd: fixture.tree }
    expect(mcpPre(call, resolveProject(fixture.tree), stateOf(fixture))).toMatchObject({
      kind: 'deny',
      code: 'mcp-unsafe',
    })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('серверный профиль в строку подключения не разрешается', () => {
    const fixture = createFixture({
      write: {
        'tree/.zcode/testpilot/profiles.yaml': 'profiles:\n  main:\n    base: db\n    server: true\n  file:\n    base: $TMP/tree/db\n',
      },
    })
    const project = resolveProject(fixture.tree)
    expect(profileConnection(project, 'main')).toBeNull()
    expect(profileConnection(project, 'file')).toBe(`File=${fixture.tree}/db`)
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('module: разборы', () => {
  it('директивы ловятся и вне изменённых строк, комментарии — нет', () => {
    const rel = 'src/cf/CommonModules/Общий/Ext/Module.bsl'
    const text = '// &НаКлиенте\n&НаСервере\nПроцедура А()\nКонецПроцедуры\n'
    expect(checkBsl(rel, text, null)).toMatchObject([
      { line: 2, rule: 'directive-in-common-module', hot: true },
    ])
  })

  it('Перем внутри метода не считается модульной переменной', () => {
    const rel = 'src/cf/CommonModules/Общий/Ext/Module.bsl'
    const text = 'Процедура А()\n\tПерем Локальная;\nКонецПроцедуры\n'
    expect(checkBsl(rel, text, null)).toEqual([])
  })

  it('носитель без правил (не модуль и не роль) не проверяется', () => {
    expect(checkBsl('src/cf/CommonModules/Общий/Ext/Help.xml', '&НаКлиенте\n', null)).toEqual([])
  })

  it('права на запретный класс ловятся только при value=true', () => {
    const rights = (value: string, name: string) =>
      `<Rights><object><name>${name}</name><right><name>Read</name><value>${value}</value></right></object></Rights>`
    expect(checkRights(rights('true', 'Enum.Статус'))).toEqual(['Enum.Статус'])
    expect(checkRights(rights('false', 'Enum.Статус'))).toEqual([])
    expect(checkRights(rights('true', 'Document.Заказ'))).toEqual([])
  })

  it('эскалация читается из .dsh первой', () => {
    const fixture = createFixture({
      write: {
        'tree/.zcode/bsl-module-gate-escalations.txt': 'directive-in-common-module:старое\n',
        'tree/.dsh/bsl-module-gate-escalations.txt': 'directive-in-common-module:новое\n',
      },
    })
    const skips = escalationSkips(resolveProject(fixture.tree))
    expect(skips).toEqual([['directive-in-common-module', 'новое']])
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('эскалация снимает блок и оставляет заметку', () => {
    const fixture = createFixture({
      write: {
        'tree/src/cf/CommonModules/Общий/Ext/Module.bsl': '&НаКлиенте\nПроцедура А()\nКонецПроцедуры\n',
        'tree/.dsh/bsl-module-gate-escalations.txt': 'CommonModules/Общий\n',
      },
    })
    const call: GateCall = {
      tool: 'write',
      args: { file_path: 'src/cf/CommonModules/Общий/Ext/Module.bsl', content: '' },
      cwd: fixture.tree,
    }
    const decision = modulePost(
      { call, isError: false, denied: false, resultText: '' },
      resolveProject(fixture.tree),
      stateOf(fixture),
    )
    expect(decision.kind).toBe('accept')
    if (decision.kind === 'accept') expect(decision.notice).toContain('вне твоей правки')
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('сверка: осмысленность ответа', () => {
  it('пустой ответ и отказ провайдера сверкой не считаются', () => {
    expect(docsResultIsMeaningful('')).toBe(false)
    expect(docsResultIsMeaningful('{"hits":[]}')).toBe(false)
    expect(docsResultIsMeaningful('{"code":"unsupported_source"}')).toBe(false)
    expect(docsResultIsMeaningful('{"status":"working"}')).toBe(false)
  })

  it('содержательный ответ считается сверкой', () => {
    expect(docsResultIsMeaningful('{"sections":[{"status":"ok","hits":[{"title":"x"}]}]}')).toBe(true)
    expect(docsResultIsMeaningful('{"items":[{"name":"x"}]}')).toBe(true)
  })
})

describe('порт чекера стиля', () => {
  it('движок из 1C_STYLE_ENGINE вызывается с подкомандой check', async () => {
    const fixture = createFixture()
    const stub = new URL('../helpers/fake-style-engine.py', import.meta.url).pathname
    const checker = createStyleChecker({ env: { ...process.env, '1C_STYLE_ENGINE': stub } })
    const rel = 'src/cf/CommonModules/M.bsl'
    const file = `${fixture.tree}/${rel}`
    const { writeFileSync } = await import('node:fs')
    writeFileSync(file, 'Плохо();\n', 'utf8')
    const report = await checker.run([rel], fixture.tree)
    expect(report).toContain('[BSL-STYLE]')
    const clean = await checker.run(['src/cf/CommonModules/Clean.bsl'], fixture.tree)
    expect(clean).toBe('')
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('фрагмент проверяется отдельным файлом и подписан «фрагмент»', async () => {
    const fixture = createFixture()
    const stub = new URL('../helpers/fake-style-engine.py', import.meta.url).pathname
    const checker = createStyleChecker({ env: { ...process.env, '1C_STYLE_ENGINE': stub } })
    const report = await checker.snippet(['Плохо();'], fixture.tree)
    expect(report).toContain('фрагмент:')
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('без движка порт молчит', async () => {
    const fixture = createFixture()
    const checker = createStyleChecker({ env: { PATH: '/nonexistent' } })
    expect(await checker.run(['src/cf/M.bsl'], fixture.tree)).toBeNull()
    rmSync(fixture.root, { recursive: true, force: true })
  })
})
