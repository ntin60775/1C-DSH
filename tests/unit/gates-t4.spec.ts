/**
 * Гейты T4: анонимизация, ретраи, ворктри-навигация, старт сессии.
 *
 * Паритет-корпус (векторы) — отдельный набор (`tests/parity/rest-gates.spec.ts`).
 * Здесь то, что проверяется только юнитом: границы шаблонов, сброс счётчиков,
 * разбор флагов, уборка состояния, git-контекст, пробуждение станции kd.
 *
 * spec: Т4 — «шесть гейтов на TS»; критерии — deny raw-режима, блок третьего
 * повтора, подсказка ворктри, уборка замка, контекст старта, обе ветки kd.
 * spec: Т10 — станция КД: url только из contour.json, скрипт — канон пакета
 * (`$DSH_1C_ROOT`), каталог станции `1c-dsh`, шов `KD_START_SCRIPT`.
 * @module tests/unit/gates-t4.spec
 */

import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, rmSync, utimesSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { homedir } from 'node:os'
import { join } from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import type { GateCall, GateState } from '../../src/contract.js'
import { evaluatePre as anonPre, scanText } from '../../src/gates/anon.js'
import { ensureKdStation, kdUrl, startScript, stationAlive, stationDir } from '../../src/gates/kd-session-start.js'
import { evaluatePre as retryPre, observeResult as retryResult } from '../../src/gates/retry.js'
import { cleanSessionState } from '../../src/gates/session-clean.js'
import { sessionContext } from '../../src/gates/session-context.js'
import { evaluatePost as worktreePost, extractPath } from '../../src/gates/worktree-hint.js'
import { resolveProject } from '../../src/paths.js'
import { createGateState } from '../../src/state.js'
import { createFixture } from '../helpers/fixture.js'

/** Корень репозитория: канон пакета для стартового скрипта станции. */
const REPO_ROOT = new URL('../..', import.meta.url).pathname

/** Корни временных деревьев, которые надо снести после теста. */
const roots: string[] = []

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true })
})

/** Состояние гейта на памяти и временном каталоге. */
function stateOf(stateDir: string): GateState {
  const store = new Map<string, unknown>()
  return createGateState({
    sessionId: 's1',
    stateDir,
    memory: {
      get: <T>(key: string): T | undefined => store.get(key) as T | undefined,
      set: (key: string, value: unknown): void => {
        store.set(key, value)
      },
    },
  })
}

/** Создать фикстуру и взять её под уборку. */
function fixtureOf(options?: Parameters<typeof createFixture>[0]) {
  const fixture = createFixture(options)
  roots.push(fixture.root)
  return fixture
}

/** Вызов инструмента в форме DSH. */
function call(tool: string, args: unknown, cwd: string): GateCall {
  return { tool, args, cwd }
}

/** Исход вызова для постового гейта. */
function executed(callValue: GateCall, isError = false) {
  return { call: callValue, isError, denied: false, resultText: '' }
}

/** git-команда в тесте. */
function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
}

/** Инициализировать git-репозиторий с одним коммитом. */
function initRepo(dir: string): void {
  git(dir, 'init', '-b', 'main')
  git(dir, 'config', 'user.email', 'test@example.com')
  git(dir, 'config', 'user.name', 'test')
  writeFileSync(join(dir, 'README.md'), 'test\n', 'utf8')
  git(dir, 'add', 'README.md')
  git(dir, 'commit', '-m', 'init')
}

describe('anon: граница шаблона', () => {
  it('raw-режим в bash блокируется и пишется в журнал', () => {
    const fixture = fixtureOf()
    const state = stateOf(fixture.stateDir)
    const decision = anonPre(call('bash', { command: 'CONTOUR_ANON=off python3 probe.py' }, fixture.tree), state)
    expect(decision).toMatchObject({ kind: 'deny', code: 'anon-raw' })
    const journal = readFileSync(join(fixture.stateDir, 'logs', 'rule-audit.jsonl'), 'utf8')
    expect(journal).toContain('"rule":"anon-gate"')
  })

  it('имя переменной регистрозависимо, значение — нет', () => {
    const fixture = fixtureOf()
    const state = stateOf(fixture.stateDir)
    expect(anonPre(call('bash', { command: 'contour_anon=off' }, fixture.tree), state).kind).toBe('allow')
    expect(anonPre(call('bash', { command: 'CONTOUR_ANON=OFF' }, fixture.tree), state)).toMatchObject({
      kind: 'deny',
      code: 'anon-raw',
    })
  })

  it('хвост слова и точка значение не матчат', () => {
    const fixture = fixtureOf()
    const state = stateOf(fixture.stateDir)
    expect(anonPre(call('bash', { command: 'CONTOUR_ANON=offshore' }, fixture.tree), state).kind).toBe('allow')
    expect(
      anonPre(call('bash', { command: 'DEANONYMIZATION_API_ENABLED=1.5' }, fixture.tree), state).kind,
    ).toBe('allow')
  })

  it('комментарии, markdown и удаляемые строки патча пропускаются', () => {
    const fixture = fixtureOf()
    const state = stateOf(fixture.stateDir)
    expect(anonPre(call('bash', { command: '# CONTOUR_ANON=off\necho ok' }, fixture.tree), state).kind).toBe('allow')
    expect(
      anonPre(call('write', { file_path: 'notes.md', content: 'CONTOUR_ANON: off' }, fixture.tree), state).kind,
    ).toBe('allow')
    expect(
      anonPre(
        call('apply_patch', { patch: '*** Begin Patch\n*** Update File: x.py\n@@\n-CONTOUR_ANON=off\n+print(1)\n*** End Patch' }, fixture.tree),
        state,
      ).kind,
    ).toBe('allow')
  })

  it('добавленные строки патча и новая строка правки сканируются', () => {
    const fixture = fixtureOf()
    const state = stateOf(fixture.stateDir)
    const add = '*** Begin Patch\n*** Add File: x.py\n+DEANONYMIZATION_API_ENABLED = True\n*** End Patch'
    expect(anonPre(call('apply_patch', { patch: add }, fixture.tree), state)).toMatchObject({
      kind: 'deny',
      code: 'anon-raw',
    })
    expect(
      anonPre(call('edit', { file_path: 'x.py', old_string: 'a', new_string: 'CONTOUR_ANON = "no"' }, fixture.tree), state),
    ).toMatchObject({ kind: 'deny', code: 'anon-raw' })
  })

  it('JSON-форма с кавычками ловится', () => {
    const fixture = fixtureOf()
    const state = stateOf(fixture.stateDir)
    const command = `python3 -c '{"CONTOUR_ANON": "false"}'`
    expect(anonPre(call('bash', { command }, fixture.tree), state)).toMatchObject({ kind: 'deny', code: 'anon-raw' })
  })

  it('scanText подписывает bash и файл', () => {
    expect(scanText(call('bash', { command: 'ls' }, '/tmp'))).toEqual({ text: 'ls', label: 'bash' })
    expect(scanText(call('write', { file_path: 'a.py', content: 'x\n' }, '/tmp'))).toEqual({
      text: 'x',
      label: 'a.py',
    })
  })
})

describe('retry: дисциплина повторов', () => {
  it('третий идентичный вызов после двух ошибок блокируется', () => {
    const fixture = fixtureOf()
    const state = stateOf(fixture.stateDir)
    const bash = call('bash', { command: 'false' }, fixture.tree)
    expect(retryPre(bash, resolveProject(fixture.tree), state).kind).toBe('allow')
    retryResult(state, bash, resolveProject(fixture.tree), true, false)
    retryResult(state, bash, resolveProject(fixture.tree), true, false)
    const decision = retryPre(bash, resolveProject(fixture.tree), state)
    expect(decision).toMatchObject({ kind: 'deny', code: 'retry-discipline' })
    if (decision.kind === 'deny') expect(decision.reason).toContain('уже падала 2 раза подряд')
    expect(readFileSync(join(fixture.stateDir, 'logs', 'rule-audit.jsonl'), 'utf8')).toContain('"rule":"retry-discipline"')
  })

  it('успех сбрасывает счётчик, другая команда не блокируется', () => {
    const fixture = fixtureOf()
    const state = stateOf(fixture.stateDir)
    const bash = call('bash', { command: 'false' }, fixture.tree)
    retryResult(state, bash, resolveProject(fixture.tree), true, false)
    retryResult(state, bash, resolveProject(fixture.tree), false, false)
    retryResult(state, bash, resolveProject(fixture.tree), true, false)
    expect(retryPre(bash, resolveProject(fixture.tree), state).kind).toBe('allow')
    expect(retryPre(call('bash', { command: 'true' }, fixture.tree), resolveProject(fixture.tree), state).kind).toBe('allow')
  })

  it('отказ политики и не-bash не считаются', () => {
    const fixture = fixtureOf()
    const state = stateOf(fixture.stateDir)
    const bash = call('bash', { command: 'false' }, fixture.tree)
    retryResult(state, bash, resolveProject(fixture.tree), true, true)
    retryResult(state, bash, resolveProject(fixture.tree), true, true)
    retryResult(state, call('write', { file_path: 'x', content: '' }, fixture.tree), resolveProject(fixture.tree), true, false)
    expect(retryPre(bash, resolveProject(fixture.tree), state).kind).toBe('allow')
  })
})

describe('worktree_hint: навигация', () => {
  it('путь извлекается мимо флагов и --', () => {
    expect(extractPath('git worktree add -b feature ../wt')).toBe('../wt')
    expect(extractPath('git worktree add --checkout ../wt')).toBe('')
    expect(extractPath('git worktree add --detach ../wt')).toBe('../wt')
    expect(extractPath('git worktree add -- ../wt')).toBe('')
    expect(extractPath('git worktree list')).toBe('')
    expect(extractPath('echo git worktree add')).toBe('')
  })

  it('существующий каталог даёт заметку, отсутствующий и ошибка — нет', () => {
    const fixture = fixtureOf()
    mkdirSync(join(fixture.root, 'wt'), { recursive: true })
    const state = stateOf(fixture.stateDir)
    const ok = worktreePost(executed(call('bash', { command: 'git worktree add wt -b feature' }, fixture.root)), resolveProject(fixture.tree), state)
    expect(ok.kind).toBe('accept')
    if (ok.kind === 'accept') {
      expect(ok.notice).toContain(`ворктри создан: ${fixture.root}/wt`)
      expect(ok.notice).toContain('сессию DSH')
    }
    const missing = worktreePost(executed(call('bash', { command: 'git worktree add missing -b feature' }, fixture.root)), resolveProject(fixture.tree), state)
    expect(missing.kind === 'accept' ? missing.notice : undefined).toBeUndefined()
    const failed = worktreePost(executed(call('bash', { command: 'git worktree add wt -b feature' }, fixture.root), true), resolveProject(fixture.tree), state)
    expect(failed.kind === 'accept' ? failed.notice : undefined).toBeUndefined()
  })

  it('относительный путь считается от workdir вызова', () => {
    const fixture = fixtureOf({ mkdir: ['sub/wt'] })
    const state = stateOf(fixture.stateDir)
    const decision = worktreePost(
      executed(call('bash', { command: 'git worktree add wt -b feature', workdir: 'sub' }, fixture.root)),
      resolveProject(fixture.tree),
      state,
    )
    expect(decision.kind).toBe('accept')
    if (decision.kind === 'accept') expect(decision.notice).toContain(`${fixture.root}/sub/wt`)
  })
})

describe('границы проекта', () => {
  it('вне 1С-проекта retry, ворктри-подсказка и контекст молчат, anon — нет', () => {
    const fixture = fixtureOf()
    const plain = join(fixture.root, 'plain')
    mkdirSync(plain, { recursive: true })
    const project = resolveProject(plain)
    expect(project).toBeNull()
    const state = stateOf(fixture.stateDir)
    const bash = call('bash', { command: 'false' }, plain)
    retryResult(state, bash, project, true, false)
    retryResult(state, bash, project, true, false)
    expect(retryPre(bash, project, state).kind).toBe('allow')
    expect(
      worktreePost(executed(call('bash', { command: 'git worktree add wt -b feature' }, plain)), project, state),
    ).toEqual({ kind: 'accept' })
    expect(sessionContext(project, plain)).toBeNull()
    // Граница ПДн не зависит от каталога сессии: fail-closed всегда.
    expect(anonPre(call('bash', { command: 'CONTOUR_ANON=off' }, plain), state)).toMatchObject({
      kind: 'deny',
      code: 'anon-raw',
    })
  })
})

describe('session_clean: уборка старта', () => {
  it('просроченный замок снят, живой остался, старый файл состояния снят', () => {
    const fixture = fixtureOf()
    const locks = join(fixture.stateDir, 'ib-locks')
    mkdirSync(locks, { recursive: true })
    writeFileSync(join(locks, 'stale.json'), JSON.stringify({ expires_ts: 1 }), 'utf8')
    writeFileSync(join(locks, 'fresh.json'), JSON.stringify({ expires_ts: 9_999_999_999 }), 'utf8')
    const oldDir = join(fixture.stateDir, 'doc-gate')
    mkdirSync(oldDir, { recursive: true })
    const oldPath = join(oldDir, 'old.json')
    writeFileSync(oldPath, '{}', 'utf8')
    const oldSeconds = Date.now() / 1000 - 8 * 86400
    utimesSync(oldPath, oldSeconds, oldSeconds)
    const report = cleanSessionState(fixture.stateDir, Date.now() / 1000)
    expect(report.locks).toBe(1)
    expect(report.files).toBe(1)
    expect(existsSync(join(locks, 'stale.json'))).toBe(false)
    expect(existsSync(join(locks, 'fresh.json'))).toBe(true)
    expect(existsSync(oldPath)).toBe(false)
  })

  it('пустой каталог состояния не роняет уборку', () => {
    const fixture = fixtureOf()
    expect(cleanSessionState(join(fixture.root, 'none'), Date.now() / 1000)).toEqual({ locks: 0, files: 0 })
  })
})

describe('session_context: где сессия', () => {
  it('основное дерево без ворктри молчит, с ворктри — сообщает', () => {
    const fixture = fixtureOf()
    initRepo(fixture.tree)
    expect(sessionContext(resolveProject(fixture.tree), fixture.tree)).toBeNull()
    git(fixture.tree, 'worktree', 'add', join(fixture.root, 'wt'), '-b', 'feature')
    const message = sessionContext(resolveProject(fixture.tree), fixture.tree)
    expect(message).toContain('cwd — основное дерево')
    expect(message).toContain('feature')
    expect(message).toContain(join(fixture.root, 'wt'))
  })

  it('из ворктри называет основным настоящее основное дерево', () => {
    const fixture = fixtureOf()
    initRepo(fixture.tree)
    git(fixture.tree, 'worktree', 'add', join(fixture.root, 'wt'), '-b', 'feature')
    const message = sessionContext(resolveProject(fixture.tree), join(fixture.root, 'wt'))
    expect(message).toContain('сессия в ворктри')
    expect(message).toContain(`основное дерево: ${fixture.tree}`)
    expect(message).toContain('(feature)')
  })

  it('вне git-репозитория молчит', () => {
    const fixture = fixtureOf()
    expect(sessionContext(resolveProject(fixture.tree), fixture.tree)).toBeNull()
  })
})

describe('kd_session_start: пробуждение станции', () => {
  it('URL берётся из contour.json; без него — пусто', () => {
    const fixture = fixtureOf({ contour: { '1c_kd': { url: 'http://contour/mcp' } } })
    expect(kdUrl(resolveProject(fixture.tree))).toBe('http://contour/mcp')
    const none = fixtureOf()
    expect(kdUrl(resolveProject(none.tree))).toBe('')
    expect(kdUrl(null)).toBe('')
  })

  it('скрипт старта — канон пакета контура, переопределяется KD_START_SCRIPT', () => {
    const packageScript = startScript({})
    expect(packageScript).toBe(join(REPO_ROOT, 'scripts', 'start_kd_rules.sh'))
    const custom = fixtureOf()
    const customScript = join(custom.root, 'kd-start.sh')
    writeFileSync(customScript, '#!/bin/sh\n', 'utf8')
    expect(startScript({ KD_START_SCRIPT: customScript })).toBe(customScript)
    expect(startScript({ KD_START_SCRIPT: join(custom.root, 'absent.sh') })).toBeNull()
  })

  it('каталог станции по умолчанию — 1c-dsh, переопределяется KD_RULES_STATION', () => {
    expect(stationDir({})).toBe(join(homedir(), '.local', 'share', '1c-dsh', 'kd-rules-mcp'))
    expect(stationDir({ KD_RULES_STATION: '/tmp/station' })).toBe('/tmp/station')
  })

  it('спящая станция поднимается, живая — нет', async () => {
    const fixture = fixtureOf({ contour: { '1c_kd': { url: 'http://127.0.0.1:9/mcp' } } })
    const station = join(fixture.root, 'station')
    mkdirSync(station, { recursive: true })
    writeFileSync(join(station, 'pyproject.toml'), '[project]\nname = "kd"\n', 'utf8')
    const marker = join(fixture.root, 'wake.txt')
    const script = join(fixture.root, 'kd-start.sh')
    writeFileSync(script, `#!/bin/sh\necho "$@" > "${marker}"\n`, 'utf8')
    const env = { ...process.env, KD_RULES_STATION: station }

    expect(await ensureKdStation(resolveProject(fixture.tree), { ...env, KD_START_SCRIPT: script })).toBe(true)
    for (let attempt = 0; attempt < 40 && !existsSync(marker); attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 50))
    }
    expect(readFileSync(marker, 'utf8')).toContain('--url http://127.0.0.1:9/mcp')

    const server = createServer((_request, response) => {
      response.statusCode = 406
      response.end()
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()))
    const address = server.address()
    const port = typeof address === 'object' && address !== null ? address.port : 0
    const aliveUrl = `http://127.0.0.1:${port}/mcp`
    rmSync(marker, { force: true })
    const aliveFixture = fixtureOf({ contour: { '1c_kd': { url: aliveUrl } } })
    expect(await stationAlive(aliveUrl)).toBe(true)
    const aliveScript = join(aliveFixture.root, 'kd-start.sh')
    writeFileSync(aliveScript, `#!/bin/sh\necho "$@" > "${marker}"\n`, 'utf8')
    expect(await ensureKdStation(resolveProject(aliveFixture.tree), { ...env, KD_START_SCRIPT: aliveScript })).toBe(false)
    expect(existsSync(marker)).toBe(false)
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  it('без станции и без проекта молчит', async () => {
    const fixture = fixtureOf()
    expect(await ensureKdStation(null, { ...process.env, KD_RULES_STATION: join(fixture.root, 'none') })).toBe(false)
  })

  it('недоступный скрипт — молчим (шов KD_START_SCRIPT)', async () => {
    const fixture = fixtureOf({ contour: { '1c_kd': { url: 'http://127.0.0.1:9/mcp' } } })
    const station = join(fixture.root, 'station')
    mkdirSync(station, { recursive: true })
    writeFileSync(join(station, 'pyproject.toml'), '[project]\nname = "kd"\n', 'utf8')
    expect(await ensureKdStation(resolveProject(fixture.tree), {
      ...process.env,
      KD_RULES_STATION: station,
      KD_START_SCRIPT: join(fixture.root, 'absent-kd-start.sh'),
    })).toBe(false)
  })
})

