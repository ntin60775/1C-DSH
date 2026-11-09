/**
 * Исходы вызовов внутри сессии.
 *
 * `tools/result` — единственное событие, которое видит финальный исход
 * вызова, включая ошибку инструмента. Счётчики живут в памяти сессии: они
 * нужны гейту повторов (`retry_gate`, юнит Т4), поэтому собираются с первого
 * тика, а не тогда, когда гейт появится.
 * @module dsh-1c-contour/outcomes
 */

import type { ExecutedCall } from './contract.js'

/** Счётчик исходов по инструменту. */
export interface OutcomeCounters {
  /** Успешных вызовов. */
  readonly ok: number
  /** Вызовов с ошибкой. */
  readonly error: number
}

/**
 * Журнал исходов одного агента: счётчик на имя инструмента.
 */
export class OutcomeLog {
  readonly #counters = new Map<string, { ok: number; error: number }>()

  /**
   * Записать исход вызова.
   * @param tool - имя инструмента.
   * @param isError - инструмент сообщил об ошибке.
   */
  record(tool: string, isError: boolean): void {
    const current = this.#counters.get(tool) ?? { ok: 0, error: 0 }
    if (isError) current.error += 1
    else current.ok += 1
    this.#counters.set(tool, current)
  }

  /**
   * Прочитать счётчик инструмента.
   * @param tool - имя инструмента.
   * @returns счётчики; нули для ненаблюдавшегося инструмента.
   */
  get(tool: string): OutcomeCounters {
    const current = this.#counters.get(tool)
    return current === undefined ? { ok: 0, error: 0 } : { ...current }
  }
}

/**
 * Наблюдатель исхода: записать результат вызова в журнал.
 *
 * Отказ политики не записывается: он приходит тем же `isError`, но сбоем
 * инструмента не является, а счётчик повторов считает именно сбои.
 * @param log - журнал сессии.
 * @param executed - вызов и его финальный исход.
 */
export function observeOutcome(log: OutcomeLog, executed: ExecutedCall): void {
  if (executed.denied) return
  log.record(executed.call.tool, executed.isError)
}
