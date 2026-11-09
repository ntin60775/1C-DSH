/**
 * Счётчики исходов: успех и ошибка считаются, отказ политики — нет.
 *
 * invariant: отказ гейта приходит тем же `isError`, но сбоем инструмента
 * не является и в счётчик повторов (гейт Т4) не идёт.
 * @module tests/unit/outcomes.spec
 */

import { describe, expect, it } from 'vitest'

import type { ExecutedCall, GateCall } from '../../src/contract.js'
import { observeOutcome, OutcomeLog } from '../../src/outcomes.js'

const CALL: GateCall = { tool: 'bash', args: { command: 'ls' }, cwd: '/tmp' }

/** Собрать исход вызова. */
function executed(isError: boolean, denied = false): ExecutedCall {
  return { call: CALL, isError, denied }
}

describe('OutcomeLog', () => {
  it('считает успех и ошибку по имени инструмента', () => {
    const log = new OutcomeLog()
    observeOutcome(log, executed(false))
    observeOutcome(log, executed(true))
    observeOutcome(log, executed(true))
    expect(log.get('bash')).toEqual({ ok: 1, error: 2 })
  })

  it('для ненаблюдавшегося инструмента отдаёт нули', () => {
    expect(new OutcomeLog().get('write')).toEqual({ ok: 0, error: 0 })
  })

  it('отказ политики не считается сбоем инструмента', () => {
    const log = new OutcomeLog()
    observeOutcome(log, executed(true, true))
    expect(log.get('bash')).toEqual({ ok: 0, error: 0 })
  })
})
