/**
 * Состояние сессии контура и наблюдатель исхода вызова.
 *
 * Вынесено из адаптера, чтобы логика исходов проверялась без поднятия хоста:
 * `tools/result` — единственное событие с финальным исходом, и счётчики
 * повторов (гейт `retry_gate`) считаются уже здесь.
 * @module dsh-1c-contour/session
 */

import type { ToolExecutionResult } from '@deepseek-ai/dsh-tools'

import type { ExecutedCall, GateCall } from './contract.js'
import { observeOutcome, OutcomeLog } from './outcomes.js'

/** Состояние одного агента в памяти хоста. */
export interface SessionState {
  /** Счётчики исходов вызовов. */
  readonly outcomes: OutcomeLog
}

/**
 * Создать состояние агента.
 * @returns пустое состояние с журналом исходов.
 */
export function createSessionState(): SessionState {
  return { outcomes: new OutcomeLog() }
}

/**
 * Учесть финальный исход вызова по событию `tools/result`.
 * @param state - состояние агента.
 * @param call - вызов инструмента.
 * @param result - финальный исход, включая ошибку.
 * @param deniedCode - код структурированной ошибки отказа политики.
 */
export function observeResult(
  state: SessionState,
  call: GateCall,
  result: Readonly<ToolExecutionResult>,
  deniedCode: string,
): void {
  const executed: ExecutedCall = {
    call,
    isError: result.isError,
    denied: result.isError && result.error.info?.code === deniedCode,
  }
  observeOutcome(state.outcomes, executed)
}
