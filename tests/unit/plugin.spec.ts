/**
 * Подключение гейтов к событиям DSH: четыре события, отказ до вызова,
 * освобождение замка после, наблюдаемый исход в обоих видах.
 *
 * Хост подменён минимальным контекстом: тест проверяет разводку плагина, а
 * не Cordis. Освобождение замка проверяется на замке, который поставил
 * python-эталон, — это же и проверка совместимости формата.
 *
 * spec: Т2 — «`tools/result` видит и успешный, и ошибочный исход — тест
 * на оба»; invariant: замок ИБ читается эталоном и TS-гейтом в обе стороны.
 * @module tests/unit/plugin.spec
 */

import { existsSync, rmSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { evaluatePre } from '../../src/gates/unica-source.js'
import { apply, DENIED_CODE, inject, name } from '../../src/index.js'
import { lockPath, resolveInfobaseConnection } from '../../src/lock.js'
import { resolveProject } from '../../src/paths.js'
import { sessionStateOf } from '../../src/session.js'
import { createGateState } from '../../src/state.js'
import { BSL, createFixture, V8PROJECT } from '../helpers/fixture.js'
import { runOracle, seedPythonLock } from '../parity/oracle.js'

/** Минимальный контекст плагина: запоминает обработчики событий. */
interface FakeContext {
  readonly handlers: Map<string, (...args: any[]) => any>
  readonly warnings: string[]
  readonly ctx: unknown
}

/** Подменить хост: каталог сессии, логгер и подписка на события. */
function createFakeContext(cwd: string): FakeContext {
  const handlers = new Map<string, (...args: any[]) => any>()
  const warnings: string[] = []
  const ctx = {
    logger: {
      warn: (message: string) => warnings.push(message),
      info: () => undefined,
      debug: () => undefined,
    },
    on: (event: string, handler: (...args: any[]) => any) => {
      handlers.set(event, handler)
      return () => handlers.delete(event)
    },
    workingDirectory: {
      get: () => cwd,
      ensure: async () => cwd,
    },
  }
  return { handlers, warnings, ctx }
}

/** Выполнить с подменённым корнем состояния контура. */
async function withStateDir<T>(dir: string, body: () => Promise<T> | T): Promise<T> {
  const previous = process.env['DSH_1C_STATE_DIR']
  process.env['DSH_1C_STATE_DIR'] = dir
  try {
    return await body()
  } finally {
    if (previous === undefined) delete process.env['DSH_1C_STATE_DIR']
    else process.env['DSH_1C_STATE_DIR'] = previous
  }
}

/** Агент сессии: тот же объект, что видят плагин и тест. */
function agentOf(sessionId = 's1'): { session: { id: string } } {
  return { session: { id: sessionId } }
}

/** Вызов инструмента в форме события DSH. */
function exec(tool: string, args: unknown, agent: { session: { id: string } } = agentOf()): unknown {
  return { name: tool, arguments: args, agent }
}

describe('разводка плагина', () => {
  it('объявляет имя и зависимости', () => {
    expect(name).toBe('dsh-1c-contour')
    expect(inject).toEqual(['tools', 'workingDirectory'])
  })

  it('подписывается на четыре события контура', () => {
    const fixture = createFixture()
    const fake = createFakeContext(fixture.tree)
    apply(fake.ctx as never)
    expect([...fake.handlers.keys()].sort()).toEqual([
      'agent/created',
      'tools/post-execute',
      'tools/pre-execute',
      'tools/result',
    ])
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('tools/pre-execute', () => {
  it('отказывает правке исходника с причиной и кодом отказа', async () => {
    const fixture = createFixture()
    const decision = await withStateDir(fixture.stateDir, async () => {
      const fake = createFakeContext(fixture.tree)
      apply(fake.ctx as never)
      const handler = fake.handlers.get('tools/pre-execute') as (exec: unknown, next: () => unknown) => Promise<any>
      return handler(exec('write', { file_path: BSL, content: '' }), () => ({ kind: 'allow' }))
    })
    expect(decision.kind).toBe('deny')
    expect(decision.reason).toContain('unica.apply')
    expect(decision.info).toEqual({ name: 'ContourDeniedError', code: DENIED_CODE })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('пропускает вызов вне исходников и отдаёт слово следующему слушателю', async () => {
    const fixture = createFixture()
    const decision = await withStateDir(fixture.stateDir, async () => {
      const fake = createFakeContext(fixture.tree)
      apply(fake.ctx as never)
      const handler = fake.handlers.get('tools/pre-execute') as (exec: unknown, next: () => unknown) => Promise<any>
      return handler(exec('write', { file_path: 'docs/readme.md', content: '' }), () => ({ kind: 'allow' }))
    })
    expect(decision).toEqual({ kind: 'allow' })
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('tools/post-execute', () => {
  it('отпускает замок, поставленный python-эталоном', async () => {
    const fixture = createFixture({ contour: {} })
    const connection = seedPythonLock({
      holderTree: fixture.tree,
      connectionSourceTree: fixture.tree,
      operation: 'push',
      sessionId: 's1',
      legacyStateDir: fixture.legacyStateDir,
    })
    const lockFile = lockPath(fixture.stateDir, connection, fixture.tree)
    expect(existsSync(lockFile)).toBe(true)

    const decision = await withStateDir(fixture.stateDir, async () => {
      const fake = createFakeContext(fixture.tree)
      apply(fake.ctx as never)
      const handler = fake.handlers.get('tools/post-execute') as (...args: any[]) => Promise<any>
      return handler(
        exec('mcp__unica__run', { args: { op: 'push', cwd: fixture.tree } }),
        { isError: false, content: [] },
        () => ({ kind: 'accept' }),
      )
    })
    expect(decision).toEqual({ kind: 'accept' })
    expect(existsSync(lockFile)).toBe(false)

    // Для эталона замка тоже нет: следующая его операция проходит.
    const oracle = runOracle(
      {
        hook_event_name: 'PreToolUse',
        session_id: 's1',
        tool_name: 'mcp__unica__run',
        tool_input: { args: { op: 'push', cwd: fixture.tree } },
        cwd: fixture.tree,
      },
      { stateDir: fixture.legacyStateDir, cwd: fixture.tree },
    )
    expect(oracle.exitCode, oracle.stderr).toBe(0)
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('tools/result', () => {
  it('видит и успешный, и ошибочный исход и считает оба', async () => {
    const fixture = createFixture()
    const agent = agentOf()
    await withStateDir(fixture.stateDir, () => {
      const fake = createFakeContext(fixture.tree)
      apply(fake.ctx as never)
      const handler = fake.handlers.get('tools/result') as (...args: any[]) => unknown
      handler(exec('bash', { command: 'ls' }, agent), { isError: false, content: [], value: null })
      handler(exec('bash', { command: 'false' }, agent), {
        isError: true,
        content: [{ type: 'text', text: 'Error: 1' }],
        error: { message: '1' },
      })
    })
    expect(sessionStateOf(agent as never).outcomes.get('bash')).toEqual({ ok: 1, error: 1 })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('не считает отказ политики сбоем инструмента', async () => {
    const fixture = createFixture()
    const agent = agentOf()
    await withStateDir(fixture.stateDir, () => {
      const fake = createFakeContext(fixture.tree)
      apply(fake.ctx as never)
      const handler = fake.handlers.get('tools/result') as (...args: any[]) => unknown
      handler(exec('write', { file_path: BSL, content: '' }, agent), {
        isError: true,
        content: [],
        error: { message: 'отказ', info: { name: 'ContourDeniedError', code: DENIED_CODE } },
      })
    })
    expect(sessionStateOf(agent as never).outcomes.get('write')).toEqual({ ok: 0, error: 0 })
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('совместимость замков', () => {
  it('замок TS-гейта виден python-эталону как занятость базы', () => {
    const fixture = createFixture({
      contour: {},
      write: { 'other-tree/v8project.yaml': V8PROJECT },
    })
    const state = createGateState({ sessionId: 's1', stateDir: fixture.stateDir })
    const decision = evaluatePre(
      { tool: 'mcp__unica__run', args: { args: { op: 'push', cwd: fixture.tree } }, cwd: fixture.tree },
      resolveProject(fixture.tree),
      state,
    )
    expect(decision).toMatchObject({ kind: 'allow' })

    // Эталон идёт из другого дерева с той же строкой подключения: база занята.
    const otherTree = `${fixture.root}/other-tree`
    const oracle = runOracle(
      {
        hook_event_name: 'PreToolUse',
        session_id: 's-other',
        tool_name: 'mcp__unica__run',
        tool_input: { args: { op: 'push', sourceSet: 'main', cwd: otherTree } },
        cwd: otherTree,
      },
      { stateDir: fixture.legacyStateDir, cwd: otherTree },
    )
    expect(oracle.exitCode, oracle.stderr).toBe(2)
    expect(oracle.stderr).toContain('База занята')
    const connection = resolveInfobaseConnection(fixture.tree) as string
    expect(existsSync(lockPath(fixture.stateDir, connection, fixture.tree))).toBe(true)
    rmSync(fixture.root, { recursive: true, force: true })
  })
})
