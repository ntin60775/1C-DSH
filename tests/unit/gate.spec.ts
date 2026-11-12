/**
 * Гейт `unica_source_gate`: раскладка `.dsh`, журнал, патч, поверхность Unica.
 *
 * Паритет-корпус (векторы) проверяется отдельно (`tests/parity`). Здесь —
 * то, чего у эталона нет: новая раскладка `.dsh`, извлечение путей из
 * `apply_patch`, коды причин и записи журнала.
 *
 * target: решение 7 спеки — новая раскладка `.dsh` перекрывает legacy;
 * target: `apply_patch` приносит патч текстом, пути извлекаются из него.
 * @module tests/unit/gate.spec
 */

import { existsSync, mkdirSync, rmSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import type { ContourProject, GateCall, GateState } from '../../src/contract.js'
import { evaluatePost, evaluatePre, patchPaths } from '../../src/gates/unica-source.js'
import { resolveProject } from '../../src/paths.js'
import { lockPath, resolveInfobaseConnection } from '../../src/lock.js'
import { createGateState } from '../../src/state.js'
import { BSL, createFixture, readAudit } from '../helpers/fixture.js'

/** Собрать вызов инструмента. */
function call(tool: string, args: unknown, cwd: string): GateCall {
  return { tool, args, cwd }
}

/** Прогнать гейт на фикстуре. */
function run(
  fixture: { readonly tree: string; readonly stateDir: string },
  toolCall: GateCall,
  sessionId = 's1',
): { project: ContourProject | null; state: GateState; decision: ReturnType<typeof evaluatePre> } {
  const project = resolveProject(toolCall.cwd)
  const state = createGateState({ sessionId, stateDir: fixture.stateDir })
  return { project, state, decision: evaluatePre(toolCall, project, state) }
}

describe('вне проекта 1С', () => {
  it('гейт молчит и на пути исходника', () => {
    const fixture = createFixture()
    const outside = join(fixture.root, 'no-project')
    mkdirSync(outside, { recursive: true })
    expect(resolveProject(outside)).toBeNull()
    const write = run(fixture, call('write', { file_path: 'src/cf/X.bsl', content: '' }, outside))
    expect(write.decision).toMatchObject({ kind: 'allow', code: 'none' })
    const bash = run(fixture, call('bash', { command: 'echo x >> src/cf/X.bsl' }, outside))
    expect(bash.decision).toMatchObject({ kind: 'allow', code: 'none' })
    // Решение не журналируется: контура вне проекта нет.
    expect(readAudit(fixture.stateDir)).toEqual([])
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('раскладка .dsh', () => {
  it('allowlist в .dsh разрешает прямую правку', () => {
    const fixture = createFixture({
      write: { 'tree/.dsh/unica-gate-escalations.txt': `# разовая прямая правка\n${BSL}\n` },
    })
    const { decision } = run(fixture, call('write', { file_path: BSL, content: '' }, fixture.tree))
    expect(decision).toMatchObject({ kind: 'allow', code: 'escalation-granted' })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('.dsh перекрывает legacy .zcode того же дерева', () => {
    const fixture = createFixture({
      write: {
        'tree/.zcode/unica-gate-escalations.txt': `# старый файл\n${BSL}\n`,
        'tree/.dsh/unica-gate-escalations.txt': '# новая раскладка, путей нет\n',
      },
    })
    // Legacy-файл разрешил бы правку; новая раскладка пуста — значит, решает она.
    const { decision } = run(fixture, call('write', { file_path: BSL, content: '' }, fixture.tree))
    expect(decision).toMatchObject({ kind: 'deny', code: 'source-edit' })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('правила пересборки читаются из .dsh/1c/contour.json', () => {
    const fixture = createFixture({
      layout: 'dsh',
      contour: {
        unica: {
          rebuild_rules: [
            { tool: 'run', op: 'push', source_sets: 'main', forbid: { full: true }, message: 'полная пересборка запрещена' },
          ],
        },
      },
    })
    const { decision } = run(
      fixture,
      call('mcp__unica__run', { args: { op: 'push', full: true, cwd: fixture.tree } }, fixture.tree),
    )
    expect(decision).toMatchObject({ kind: 'deny', code: 'rebuild-rule', reason: 'полная пересборка запрещена' })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('правило пересборки сравнивает значения по содержимому', () => {
    const rule = {
      tool: 'run',
      op: 'push',
      require: { params: { a: 1 } },
      message: 'структура не та',
    }
    const fixture = createFixture({ layout: 'dsh', contour: { unica: { rebuild_rules: [rule] } } })
    const equal = run(
      fixture,
      call('mcp__unica__run', { args: { op: 'push', params: { a: 1 }, cwd: fixture.tree } }, fixture.tree),
    )
    expect(equal.decision).toMatchObject({ kind: 'allow' })
    const other = run(
      fixture,
      call('mcp__unica__run', { args: { op: 'push', params: { a: 2 }, cwd: fixture.tree } }, fixture.tree),
    )
    expect(other.decision).toMatchObject({ kind: 'deny', code: 'rebuild-rule', reason: 'структура не та' })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('имя MCP-сервера юники переопределяется contour.json', () => {
    const fixture = createFixture({
      contour: {
        unica: {
          server: 'мойуника',
          rebuild_rules: [
            { tool: 'run', op: 'push', source_sets: 'main', forbid: { full: true }, message: 'полная пересборка запрещена' },
          ],
        },
      },
    })
    const ours = run(
      fixture,
      call('mcp__мойуника__run', { args: { op: 'push', full: true, cwd: fixture.tree } }, fixture.tree),
    )
    expect(ours.decision).toMatchObject({ kind: 'deny', code: 'rebuild-rule' })
    const foreign = run(
      fixture,
      call('mcp__1c-db__run', { args: { op: 'push', full: true, cwd: fixture.tree } }, fixture.tree),
    )
    expect(foreign.decision).toMatchObject({ kind: 'allow', code: 'none' })
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('журнал решений', () => {
  it('блок исходника пишется в журнал с причиной', () => {
    const fixture = createFixture()
    run(fixture, call('write', { file_path: BSL, content: '' }, fixture.tree))
    const audit = readAudit(fixture.stateDir)
    expect(audit).toHaveLength(1)
    expect(audit[0]).toMatchObject({ rule: 'unica-source-gate', decision: 'blocked' })
    expect(String(audit[0]?.['reason'])).toContain('unica.apply')
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('разрешённая эскалация пишется как confirmed', () => {
    const fixture = createFixture({
      write: { 'tree/.zcode/unica-gate-escalations.txt': `# разовая прямая правка\n${BSL}\n` },
    })
    run(fixture, call('write', { file_path: BSL, content: '' }, fixture.tree))
    expect(readAudit(fixture.stateDir)[0]).toMatchObject({ decision: 'confirmed' })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('правка allowlist-файла попадает в журнал', () => {
    const fixture = createFixture({ write: { 'tree/.zcode/unica-gate-escalations.txt': '# пусто\n' } })
    const { decision } = run(
      fixture,
      call('edit', { file_path: '.zcode/unica-gate-escalations.txt', old_string: 'a', new_string: 'b' }, fixture.tree),
    )
    expect(decision).toMatchObject({ kind: 'allow', code: 'none' })
    const audit = readAudit(fixture.stateDir)
    expect(String(audit[0]?.['reason'])).toContain('изменён allowlist')
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('apply_patch', () => {
  it('блокирует по второму файлу патча и называет его', () => {
    const fixture = createFixture()
    const patch = [
      '*** Begin Patch',
      '*** Update File: src/docs/readme.md',
      '@@',
      '-старое',
      '+новое',
      `*** Update File: ${BSL}`,
      '@@',
      '-Процедура А()',
      '+Процедура Б()',
      '*** End Patch',
      '',
    ].join('\n')
    const { decision } = run(fixture, call('apply_patch', { input: patch }, fixture.tree))
    expect(decision).toMatchObject({ kind: 'deny', code: 'source-edit' })
    if (decision.kind === 'deny') expect(decision.reason).toContain(`Файл: ${BSL}`)
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('удаление исходника через патч тоже блокируется', () => {
    const fixture = createFixture()
    const patch = `*** Begin Patch\n*** Delete File: ${BSL}\n*** End Patch\n`
    const { decision } = run(fixture, call('apply_patch', { input: patch }, fixture.tree))
    expect(decision).toMatchObject({ kind: 'deny', code: 'source-edit' })
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('patchPaths', () => {
  it('извлекает пути всех видов правки патча', () => {
    const patch = [
      '*** Begin Patch',
      '*** Add File: src/docs/a.md',
      '+текст',
      '*** Update File: src/docs/b.md',
      '*** Move to: src/cf/Б.bsl',
      '@@',
      '*** Delete File: src/docs/c.md',
      '*** End Patch',
    ].join('\n')
    expect(patchPaths(patch)).toEqual(['src/docs/a.md', 'src/docs/b.md', 'src/cf/Б.bsl', 'src/docs/c.md'])
  })

  it('без заголовков правки патч целей не даёт', () => {
    expect(patchPaths('*** Begin Patch\n*** End Patch\n')).toEqual([])
    expect(patchPaths('обычный текст')).toEqual([])
  })

  it('ведущая и хвостовая пробельность заголовка не прячет путь', () => {
    // Парсер DSH обрезает заголовок по Unicode White_Space с обоих концов,
    // поэтому такой патч правит файл и обязан попасть в гейт.
    const header = '*** Update File: src/cf/CommonModules/X.bsl'
    for (const prefix of [' ', '\t', '\u00a0', ' \t ']) {
      const patch = `*** Begin Patch\n${prefix}${header}${prefix}\n@@\n-a\n+b\n*** End Patch\n`
      expect(patchPaths(patch), JSON.stringify(prefix)).toEqual(['src/cf/CommonModules/X.bsl'])
    }
    expect(patchPaths('*** Begin Patch\r\n*** Add File: src/cf/Y.bsl\r\n+x\r\n*** End Patch\r\n'))
      .toEqual(['src/cf/Y.bsl'])
  })
})

describe('постовое решение', () => {
  it('синхронная операция юники отпускает замок своего дерева', () => {
    const fixture = createFixture({ contour: {} })
    const state = createGateState({ sessionId: 's1', stateDir: fixture.stateDir })
    const unica = call(
      'mcp__unica__run',
      { args: { op: 'push', cwd: fixture.tree } },
      fixture.tree,
    )
    const connection = resolveInfobaseConnection(fixture.tree) as string
    const lockFile = lockPath(fixture.stateDir, connection, fixture.tree)
    // Гейт берёт замок на операцию, меняющую базу.
    expect(evaluatePre(unica, resolveProject(fixture.tree), state)).toMatchObject({ kind: 'allow' })
    expect(existsSync(lockFile)).toBe(true)
    const decision = evaluatePost(
      { call: unica, isError: false, denied: false },
      resolveProject(fixture.tree),
      state,
    )
    expect(decision).toMatchObject({ kind: 'accept' })
    expect(existsSync(lockFile)).toBe(false)
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('упавшая или отбитая операция замок не отпускает', () => {
    // Эталон отпускает замок только на успешном PostToolUse: упавшая
    // операция держит базу до TTL.
    for (const outcome of [{ isError: true, denied: false }, { isError: true, denied: true }]) {
      const fixture = createFixture({ contour: {} })
      const state = createGateState({ sessionId: 's1', stateDir: fixture.stateDir })
      const unica = call('mcp__unica__run', { args: { op: 'push', cwd: fixture.tree } }, fixture.tree)
      const connection = resolveInfobaseConnection(fixture.tree) as string
      const lockFile = lockPath(fixture.stateDir, connection, fixture.tree)
      expect(evaluatePre(unica, resolveProject(fixture.tree), state)).toMatchObject({ kind: 'allow' })
      expect(evaluatePost({ call: unica, ...outcome }, resolveProject(fixture.tree), state))
        .toMatchObject({ kind: 'accept' })
      expect(existsSync(lockFile), JSON.stringify(outcome)).toBe(true)
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('durable-задача держит замок до TTL', () => {
    const fixture = createFixture({ contour: {} })
    const state = createGateState({ sessionId: 's1', stateDir: fixture.stateDir })
    const task = call(
      'mcp__unica__task_get',
      { args: { op: 'push', cwd: fixture.tree } },
      fixture.tree,
    )
    const connection = resolveInfobaseConnection(fixture.tree) as string
    const lockFile = lockPath(fixture.stateDir, connection, fixture.tree)
    expect(evaluatePre(task, resolveProject(fixture.tree), state)).toMatchObject({ kind: 'allow' })
    expect(evaluatePost({ call: task, isError: false, denied: false }, resolveProject(fixture.tree), state))
      .toMatchObject({ kind: 'accept' })
    expect(existsSync(lockFile)).toBe(true)
    rmSync(fixture.root, { recursive: true, force: true })
  })
})
