/**
 * Состояние сессий контура и наблюдатель исхода вызова.
 *
 * Состояние живёт в памяти хоста и привязано к агенту: сессия — единица
 * изоляции счётчиков. Реестр модульный, потому что одного агента видят и
 * адаптер события, и тест разводки; ключ — сам объект агента, поэтому запись
 * исчезает вместе с ним.
 *
 * `tools/result` — единственное событие с финальным исходом, включая ошибку
 * инструмента; счётчики повторов (гейт `retry_gate`) считаются здесь.
 * @module dsh-1c-contour/session
 */

import type { Agent } from '@deepseek-ai/dsh-agent'

import type { ExecutedCall, GateCall, GateMemory } from './contract.js'
import { observeOutcome, OutcomeLog } from './outcomes.js'

/** Состояние одного агента в памяти хоста. */
export interface SessionState {
  /** Счётчики исходов вызовов. */
  readonly outcomes: OutcomeLog
  /** Память гейтов с состоянием: флаг сверки, планы правок. */
  readonly memory: GateMemory
}

/** Память на карте: значения живут, пока жива сессия. */
function createMemory(): GateMemory {
  const store = new Map<string, unknown>()
  return {
    get: <T>(key: string): T | undefined => store.get(key) as T | undefined,
    set: (key: string, value: unknown): void => {
      store.set(key, value)
    },
  }
}

const states = new WeakMap<Agent, SessionState>()

/** Состояние вызовов без агента: подпроцессы и вызовы вне сессии. */
const detached: SessionState = { outcomes: new OutcomeLog(), memory: createMemory() }

/**
 * Состояние агента; создаётся при первом обращении.
 * @param agent - агент сессии либо `undefined` вне сессии.
 * @returns состояние, привязанное к агенту.
 */
export function sessionStateOf(agent: Agent | undefined): SessionState {
  if (agent === undefined) return detached
  const known = states.get(agent)
  if (known !== undefined) return known
  const created: SessionState = { outcomes: new OutcomeLog(), memory: createMemory() }
  states.set(agent, created)
  return created
}

/**
 * Учесть финальный исход вызова по событию `tools/result`.
 * @param state - состояние агента.
 * @param call - вызов инструмента.
 * @param isError - вызов завершился ошибкой (включая ненулевой exit).
 * @param denied - вызов отклонён политикой, а не упал в теле инструмента.
 */
export function observeResult(
  state: SessionState,
  call: GateCall,
  isError: boolean,
  denied: boolean,
): void {
  const executed: ExecutedCall = { call, isError, denied, resultText: '' }
  observeOutcome(state.outcomes, executed)
}
