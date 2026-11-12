/**
 * Корпус гейтов T4: `anon`, `retry`, `worktree-hint`, `session-clean`,
 * `session-context`, `kd-session-start`.
 *
 * Каждый сценарий проигрывается на TS-гейте (чистые функции + память
 * сессии) против ожиданий вектора: решение, класс причины, названные
 * фрагменты заметок и побочные эффекты (файлы состояния, маркер станции).
 * Ожидания заморожены с python-эталона до его снятия в Т8.
 * @module tests/parity/rest
 */

import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, utimesSync, writeFileSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

import type { ContourProject, ExecutedCall, GateCall, GateState } from '../../src/contract.js'
import { evaluatePre as anonPre } from '../../src/gates/anon.js'
import { ensureKdStation } from '../../src/gates/kd-session-start.js'
import { evaluatePre as retryPre, observeResult as retryResult } from '../../src/gates/retry.js'
import { cleanSessionState } from '../../src/gates/session-clean.js'
import { sessionContext } from '../../src/gates/session-context.js'
import { evaluatePost as worktreePost } from '../../src/gates/worktree-hint.js'
import { resolveProject } from '../../src/paths.js'
import { createGateState } from '../../src/state.js'
import { createFixture, type Fixture } from '../helpers/fixture.js'
import type { ReasonClass } from './classify.js'
import { substitute } from './scenario.js'

/** Гейт корпуса T4. */
export type RestGate =
  | 'anon'
  | 'retry'
  | 'worktree-hint'
  | 'session-clean'
  | 'session-context'
  | 'kd-session-start'

/** Шаг сценария T4. */
export interface RestStep {
  readonly phase: 'pre' | 'result' | 'post' | 'session'
  readonly call?: { readonly tool: string; readonly args: unknown }
  readonly result?: { readonly isError?: boolean }
  readonly expect: {
    readonly decision: 'allow' | 'deny' | 'accept' | 'block'
    readonly code?: ReasonClass
    readonly reasonIncludes?: readonly string[]
    readonly noticeIncludes?: readonly string[]
    readonly noticeAbsent?: boolean
  }
}

/** Сценарий T4. */
export interface RestScenario {
  readonly id: string
  readonly describe: string
  readonly gate: RestGate
  readonly cwd?: string
  readonly fixture?: {
    readonly write?: Readonly<Record<string, string>>
    readonly mkdir?: readonly string[]
    readonly contour?: unknown
  }
  /** Подготовка git: репозиторий, репозиторий с ворктри, сессия в ворктри. */
  readonly git?: 'repo' | 'repo-with-worktree' | 'worktree-session'
  /** Засев файлов состояния гейтов. */
  readonly seed?: {
    readonly staleLocks?: number
    readonly freshLocks?: number
    readonly brokenLocks?: number
    readonly oldState?: readonly string[]
    readonly newState?: readonly string[]
  }
  /** Станция kd и её стартовый скрипт. */
  readonly kd?: {
    readonly url?: string
    readonly station?: boolean
    readonly script?: boolean
  }
  readonly steps: readonly RestStep[]
  /** Проверки после прогона; пути session-clean — от корня состояния. */
  readonly expectAfter?: {
    readonly absent?: readonly string[]
    readonly present?: readonly string[]
    readonly marker?: boolean
  }
}

/** Окружение одного проигрывания. */
export interface RestRuntime {
  readonly fixture: Fixture
  readonly cwd: string
  readonly project: ContourProject | null
  readonly state: GateState
  readonly station: string
  /** Стартовый скрипт станции: подмена для гейта, `null` — недоступен. */
  readonly kdScript: string | null
}

/** Ответ шага в общем виде. */
export interface StepResult {
  readonly decision: 'allow' | 'deny' | 'accept' | 'block'
  readonly code: string
  /** Причина отказа либо текст заметки. */
  readonly text: string
}

/** git-команда в фикстуре. */
function git(cwd: string, ...args: string[]): string {
  return execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
}

/** Инициализировать git-репозиторий с одним коммитом. */
function initRepo(dir: string): void {
  git(dir, 'init', '-b', 'main')
  git(dir, 'config', 'user.email', 'test@example.com')
  git(dir, 'config', 'user.name', 'test')
  writeFileSync(join(dir, 'README.md'), 'test\n', 'utf8')
  git(dir, 'add', '-A')
  git(dir, 'commit', '-m', 'init')
}

/** Записать файл с созданием каталогов. */
function writePath(path: string, content: string): void {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, content, 'utf8')
}

/** Подготовить окружение сценария. */
export function prepare(scenario: RestScenario): RestRuntime {
  const fixture = createFixture({
    ...scenario.fixture?.write === undefined ? {} : { write: scenario.fixture.write },
    ...scenario.fixture?.mkdir === undefined ? {} : { mkdir: scenario.fixture.mkdir },
    ...scenario.fixture?.contour === undefined ? {} : { contour: scenario.fixture.contour },
  })
  const worktreePath = join(fixture.root, 'wt')
  if (scenario.git === 'repo' || scenario.git === 'repo-with-worktree' || scenario.git === 'worktree-session') {
    initRepo(fixture.tree)
  }
  if (scenario.git === 'repo-with-worktree' || scenario.git === 'worktree-session') {
    git(fixture.tree, 'worktree', 'add', worktreePath, '-b', 'feature')
  }
  const cwd = scenario.git === 'worktree-session' && scenario.cwd === undefined
    ? worktreePath
    : join(fixture.root, scenario.cwd ?? 'tree')

  const seed = scenario.seed ?? {}
  const now = Date.now()
  for (let index = 1; index <= (seed.staleLocks ?? 0); index += 1) {
    writePath(join(fixture.stateDir, 'ib-locks', `stale-${index}.json`), JSON.stringify({ expires_ts: 1 }))
  }
  for (let index = 1; index <= (seed.freshLocks ?? 0); index += 1) {
    writePath(join(fixture.stateDir, 'ib-locks', `fresh-${index}.json`), JSON.stringify({ expires_ts: now / 1000 + 3600 }))
  }
  for (let index = 1; index <= (seed.brokenLocks ?? 0); index += 1) {
    writePath(join(fixture.stateDir, 'ib-locks', `broken-${index}.json`), '{not json')
  }
  const oldSeconds = now / 1000 - 8 * 86400
  for (const relative of seed.oldState ?? []) {
    const path = join(fixture.stateDir, relative)
    writePath(path, '{}')
    utimesSync(path, oldSeconds, oldSeconds)
  }
  for (const relative of seed.newState ?? []) {
    writePath(join(fixture.stateDir, relative), '{}')
  }

  const station = join(fixture.root, 'station')
  if (scenario.kd?.station === true) {
    mkdirSync(station, { recursive: true })
    writeFileSync(join(station, 'pyproject.toml'), '[project]\nname = "kd"\n', 'utf8')
  }
  // Стартовый скрипт живёт в пакете контура; в фикстуре — подмена с маркером.
  // script: false — скрипт недоступен (null-шов гейта).
  let kdScript: string | null = null
  if (scenario.kd?.script === true) {
    kdScript = join(fixture.root, 'kd-start.sh')
    writePath(kdScript, `#!/bin/sh\necho "$@" > "${join(fixture.root, 'marker')}"\n`)
  }
  if (scenario.kd?.url !== undefined) {
    // Канон адреса станции — contour.json → 1c_kd.url (пресет и гейт).
    const contourPath = join(fixture.tree, '.dsh', '1c', 'contour.json')
    const existing = existsSync(contourPath)
      ? JSON.parse(readFileSync(contourPath, 'utf8')) as Record<string, unknown>
      : {}
    writePath(contourPath, JSON.stringify({ ...existing, '1c_kd': { url: scenario.kd.url } }))
  }

  const store = new Map<string, unknown>()
  const state = createGateState({
    sessionId: 's1',
    stateDir: fixture.stateDir,
    memory: {
      get: <T>(key: string): T | undefined => store.get(key) as T | undefined,
      set: (key: string, value: unknown): void => {
        store.set(key, value)
      },
    },
  })
  return { fixture, cwd, project: resolveProject(cwd), state, station, kdScript }
}

/** Освободить окружение. */
export function cleanup(runtime: RestRuntime): void {
  rmSync(runtime.fixture.root, { recursive: true, force: true })
}

/** Проиграть шаг TS-гейтом. */
export async function stepTs(
  gate: RestGate,
  step: RestStep,
  runtime: RestRuntime,
): Promise<StepResult> {
  const raw = step.call ?? { tool: '', args: {} }
  const call: GateCall = {
    tool: raw.tool,
    args: substitute(raw.args, runtime.fixture.root),
    cwd: runtime.cwd,
  }
  const executed: ExecutedCall = {
    call,
    isError: step.result?.isError === true,
    denied: false,
    resultText: '',
  }
  switch (gate) {
    case 'anon': {
      const decision = anonPre(call, runtime.state)
      return decision.kind === 'deny'
        ? { decision: 'deny', code: decision.code, text: decision.reason }
        : { decision: 'allow', code: decision.code, text: '' }
    }
    case 'retry': {
      if (step.phase === 'result') {
        retryResult(runtime.state, call, runtime.project, step.result?.isError === true, false)
        return { decision: 'accept', code: 'accept', text: '' }
      }
      const decision = retryPre(call, runtime.project, runtime.state)
      return decision.kind === 'deny'
        ? { decision: 'deny', code: decision.code, text: decision.reason }
        : { decision: 'allow', code: decision.code, text: '' }
    }
    case 'worktree-hint': {
      const decision = worktreePost(executed, runtime.project, runtime.state)
      if (decision.kind === 'block') return { decision: 'block', code: 'block', text: decision.feedback }
      return { decision: 'accept', code: 'accept', text: decision.notice ?? '' }
    }
    case 'session-clean': {
      cleanSessionState(runtime.fixture.stateDir, Date.now() / 1000)
      return { decision: 'accept', code: 'accept', text: '' }
    }
    case 'session-context':
      return { decision: 'accept', code: 'accept', text: sessionContext(runtime.project, runtime.cwd) ?? '' }
    case 'kd-session-start': {
      await ensureKdStation(runtime.project, {
        ...process.env,
        KD_RULES_STATION: runtime.station,
        KD_START_SCRIPT: runtime.kdScript ?? join(runtime.fixture.root, 'absent-kd-start.sh'),
      })
      return { decision: 'accept', code: 'accept', text: '' }
    }
  }
}

/** Дождаться маркера либо убедиться, что его нет. */
export async function markerState(path: string, expected: boolean): Promise<boolean> {
  if (!expected) {
    await new Promise((resolve) => setTimeout(resolve, 1000))
    return existsSync(path)
  }
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (existsSync(path)) return true
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  return false
}

