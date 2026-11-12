/**
 * Проигрывание сценариев корпуса на TS-гейтах.
 *
 * Гейты `doc`, `mcp`, `bsl-style`, `bsl-module` живут после вызова и ведут
 * состояние, поэтому корпус — не один вызов, а СЦЕНАРИЙ: шаги `pre`/`post`
 * по порядку. Ожидания шагов заморожены в векторах `tests/vectors/`
 * (решение, класс причины, фрагменты текста); до снятия python-эталона в
 * Т8 каждый шаг сверялся и с эталоном — векторы хранят его решения.
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
  /** Живой stand-замок на базу: кладётся в фикстуру до первого шага. */
  readonly seedStandLock?: { readonly connection: string }
  readonly steps: readonly ScenarioStep[]
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
 * Раскладка, в которую кладутся проектные входы сценария.
 *
 * Корпус написан на legacy `.zcode`; канон `.dsh/1c` доказывается повторным
 * прогоном того же сценария с перенесёнными входами. `contour.json`
 * переезжает вместе с ними: его место задаёт `layout`, а не имя файла.
 */
export type FixtureLayout = 'legacy' | 'canon'

/** Перенести входы сценария в канон `.dsh/1c`. */
function canon(inputs: Readonly<Record<string, string>>): Record<string, string> {
  const result: Record<string, string> = {}
  for (const [path, text] of Object.entries(inputs)) {
    result[path.replace(/^tree\/\.zcode\//, 'tree/.dsh/1c/')] = text
  }
  return result
}

/**
 * Подготовить окружение сценария: дерево, память, чекер.
 * @param scenario - сценарий корпуса.
 * @param checker - порт чекера стиля для TS-гейта.
 * @param layout - раскладка входов: как в корпусе или канон `.dsh/1c`.
 * @returns окружение для шагов.
 */
export function prepare(
  scenario: Scenario,
  checker?: StyleCheckerPort,
  layout: FixtureLayout = 'legacy',
): ScenarioRuntime {
  const write = scenario.fixture?.write === undefined
    ? undefined
    : layout === 'canon' ? canon(scenario.fixture.write) : scenario.fixture.write
  const contourLayout = layout === 'canon' ? 'dsh' : scenario.fixture?.layout
  const fixture = createFixture({
    ...write === undefined ? {} : { write },
    ...scenario.fixture?.mkdir === undefined ? {} : { mkdir: scenario.fixture.mkdir },
    ...scenario.fixture?.contour === undefined ? {} : { contour: scenario.fixture.contour },
    ...contourLayout === undefined ? {} : { layout: contourLayout },
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

/** Прочитать корпус сценариев. */
export function loadScenarios(url: URL): Scenario[] {
  return JSON.parse(readFileSync(url, 'utf8')) as Scenario[]
}
