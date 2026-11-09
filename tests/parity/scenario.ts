/**
 * Паритет сценариев: TS-гейты против python-эталона.
 *
 * Гейты `doc`, `mcp`, `bsl-style`, `bsl-module` живут после вызова и ведут
 * состояние, поэтому корпус — не один вызов, а СЦЕНАРИЙ: шаги `pre`/`post`
 * по порядку. Оба адаптера проигрывают один сценарий: эталон — процессами
 * хука со своим файловым состоянием, TS — чистыми функциями с памятью сессии.
 *
 * Сверяются решение и класс причины; для блокирующих отчётов — ещё и то, что
 * причина называет нужный файл.
 * @module tests/parity/scenario
 */

import { readFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'

import type { ContourProject, ExecutedCall, GateCall, GateState } from '../../src/contract.js'
import { evaluatePost as modulePost } from '../../src/gates/bsl-module.js'
import { evaluatePost as stylePost } from '../../src/gates/bsl-style.js'
import { evaluatePost as docPost, evaluatePre as docPre } from '../../src/gates/doc.js'
import { evaluatePre as mcpPre } from '../../src/gates/mcp.js'
import { resolveProject } from '../../src/paths.js'
import { createGateState } from '../../src/state.js'
import type { StyleCheckerPort } from '../../src/contract.js'
import { createFixture, type Fixture } from '../helpers/fixture.js'
import { classifyOracle } from './classify.js'
import { gateScript, runOracle } from './oracle.js'

/** Гейт корпуса. */
export type GateName = 'doc' | 'mcp' | 'bsl-style' | 'bsl-module'

/** Шаг сценария. */
export interface ScenarioStep {
  readonly phase: 'pre' | 'post'
  readonly call: { readonly tool: string; readonly args: unknown }
  readonly result?: { readonly isError?: boolean; readonly text?: string }
  readonly expect: {
    readonly decision: 'allow' | 'deny' | 'accept' | 'block'
    readonly code?: string
    readonly reasonIncludes?: readonly string[]
    readonly noticeIncludes?: readonly string[]
  }
}

/** Сценарий корпуса. */
export interface Scenario {
  readonly id: string
  readonly describe: string
  readonly gate: GateName
  readonly cwd?: string
  readonly fixture?: {
    readonly write?: Readonly<Record<string, string>>
    readonly mkdir?: readonly string[]
    readonly contour?: unknown
    readonly layout?: 'zcode' | 'dsh'
  }
  /** Живой stand-замок на базу: кладётся эталоном до первого шага. */
  readonly seedStandLock?: { readonly connection: string }
  readonly steps: readonly ScenarioStep[]
}

/** Имена инструментов ZCode для адаптера эталона. */
const ZCODE_NAMES: Readonly<Record<string, string>> = {
  write: 'Write',
  edit: 'Edit',
  apply_patch: 'ApplyPatch',
  bash: 'Bash',
  read: 'Read',
}

/** Состояние одного сценария в TS. */
export interface ScenarioRuntime {
  readonly fixture: Fixture
  readonly project: ContourProject | null
  readonly paths: {
    readonly cwd: string
    readonly projectRoot: string
  }
  readonly state: GateState
  readonly sessionId: string
}

/**
 * Подготовить окружение сценария: дерево, память, чекер.
 * @param scenario - сценарий корпуса.
 * @param checker - порт чекера стиля для TS-гейта.
 * @returns окружение для шагов.
 */
export function prepare(scenario: Scenario, checker?: StyleCheckerPort): ScenarioRuntime {
  const fixture = createFixture({
    ...scenario.fixture?.write === undefined ? {} : { write: scenario.fixture.write },
    ...scenario.fixture?.mkdir === undefined ? {} : { mkdir: scenario.fixture.mkdir },
    ...scenario.fixture?.contour === undefined ? {} : { contour: scenario.fixture.contour },
    ...scenario.fixture?.layout === undefined ? {} : { layout: scenario.fixture.layout },
  })
  const cwd = join(fixture.root, scenario.cwd ?? 'tree')
  const memoryStore = new Map<string, unknown>()
  const state = createGateState({
    sessionId: 's1',
    stateDir: fixture.stateDir,
    memory: {
      get: <T>(key: string): T | undefined => memoryStore.get(key) as T | undefined,
      set: (key: string, value: unknown): void => {
        memoryStore.set(key, value)
      },
    },
    ...checker === undefined ? {} : { styleChecker: checker },
  })
  return {
    fixture,
    project: resolveProject(cwd),
    paths: { cwd, projectRoot: join(fixture.root, 'tree') },
    state,
    sessionId: 's1',
  }
}

/** Освободить окружение сценария. */
export function cleanup(runtime: ScenarioRuntime): void {
  rmSync(runtime.fixture.root, { recursive: true, force: true })
}

/** Подставить `$TMP` во все строки значения. */
export function substitute<T>(value: T, root: string): T {
  if (typeof value === 'string') return value.replaceAll('$TMP', root) as T
  if (Array.isArray(value)) return value.map((item) => substitute(item, root)) as T
  if (typeof value === 'object' && value !== null) {
    const result: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(value)) result[key] = substitute(item, root)
    return result as T
  }
  return value
}

/** Скрипт эталонного гейта для имени гейта. */
const GATE_SCRIPTS: Readonly<Record<GateName, string>> = {
  doc: 'doc_gate.py',
  mcp: 'mcp_gate.py',
  'bsl-style': 'bsl_style_gate.py',
  'bsl-module': 'bsl_module_gate.py',
}

/** ZCode-имя инструмента для адаптера эталона. */
function zcodeName(tool: string): string {
  return ZCODE_NAMES[tool] ?? tool
}

/**
 * Проиграть один шаг TS-гейтом.
 * @param gate - имя гейта.
 * @param step - шаг сценария.
 * @param runtime - окружение сценария.
 * @returns решение в общем виде.
 */
export async function stepTs(
  gate: GateName,
  step: ScenarioStep,
  runtime: ScenarioRuntime,
): Promise<{ decision: 'allow' | 'deny' | 'accept' | 'block'; code: string; text: string }> {
  const call: GateCall = {
    tool: step.call.tool,
    args: substitute(step.call.args, runtime.fixture.root),
    cwd: runtime.paths.cwd,
  }
  const executed: ExecutedCall = {
    call,
    isError: step.result?.isError === true,
    denied: false,
    resultText: step.result?.text ?? '',
  }
  if (step.phase === 'pre') {
    const decision = gate === 'mcp' ? mcpPre(call, runtime.project, runtime.state) : docPre(call, runtime.project, runtime.state)
    if (decision.kind === 'deny') return { decision: 'deny', code: decision.code, text: decision.reason }
    return { decision: 'allow', code: decision.code, text: '' }
  }
  const decision = gate === 'doc'
    ? docPost(executed, runtime.project, runtime.state)
    : gate === 'bsl-style'
      ? await stylePost(executed, runtime.project, runtime.state)
      : modulePost(executed, runtime.project, runtime.state)
  if (decision.kind === 'block') return { decision: 'block', code: 'block', text: decision.feedback }
  return { decision: 'accept', code: 'accept', text: decision.notice ?? '' }
}

/**
 * Проиграть один шаг python-эталоном.
 * @param step - шаг сценария.
 * @param runtime - окружение сценария (каталог состояния эталона).
 * @returns решение в общем виде.
 */
export function stepOracle(
  gate: GateName,
  step: ScenarioStep,
  runtime: ScenarioRuntime,
): { decision: 'allow' | 'deny' | 'accept' | 'block'; code: string; text: string } {
  const isPost = step.phase === 'post'
  const event = {
    hook_event_name: isPost ? 'PostToolUse' : 'PreToolUse',
    session_id: runtime.sessionId,
    tool_name: zcodeName(step.call.tool),
    tool_input: substitute(step.call.args, runtime.fixture.root),
    cwd: runtime.paths.cwd,
    is_error: step.result?.isError === true,
    ...isPost && step.result?.text === undefined
      ? {}
      : isPost ? { tool_response: { content: [{ type: 'text', text: step.result?.text ?? '' }] } } : {},
  }
  const result = runOracle(event, {
    stateDir: runtime.fixture.legacyStateDir,
    cwd: runtime.paths.cwd,
    script: gateScript(GATE_SCRIPTS[gate]),
  })
  const code = classifyOracle(result)
  const decision: 'allow' | 'deny' | 'accept' | 'block' = result.exitCode === 0
    ? (isPost ? 'accept' : 'allow')
    : (isPost ? 'block' : 'deny')
  return { decision, code, text: result.stderr }
}

/** Прочитать корпус сценариев. */
export function loadScenarios(url: URL): Scenario[] {
  return JSON.parse(readFileSync(url, 'utf8')) as Scenario[]
}
