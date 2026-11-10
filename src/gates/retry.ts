/**
 * `retry_gate` на DSH: третий идентичный повтор упавшей bash-команды.
 *
 * Порт `reference/gates/retry_gate.py`. После двух подряд ошибок одной и той
 * же команды третий идентичный вызов блокируется с требованием диагностики.
 * Счётчик ведётся по sha1 команды в памяти сессии: `tools/result` наращивает
 * его на ошибке и сбрасывает на успехе, `tools/pre-execute` проверяет.
 * Отказ политики сбоем инструмента не считается и в счётчик не идёт.
 * @module dsh-1c-contour/gates/retry
 */

import { createHash } from 'node:crypto'

import type { ContourProject, GateCall, GateState, PreDecision } from '../contract.js'

/** Имя правила в журнале решений. */
export const RULE = 'retry-discipline'

/** Сколько подряд идущих ошибок допускается до блока. */
export const MAX_CONSECUTIVE = 2

/** Ключ памяти сессии со счётчиками команд. */
const KEY_COUNTS = 'retry:commands'

/** Счётчики команд: ключ sha1 — число подряд идущих ошибок. */
type Counts = Record<string, number>

/** Прочитать счётчики из памяти сессии. */
function loadCounts(state: GateState): Counts {
  const stored = state.memory.get<Counts>(KEY_COUNTS)
  return stored !== undefined && typeof stored === 'object' && stored !== null ? stored : {}
}

/** Записать счётчики в память сессии. */
function saveCounts(state: GateState, counts: Counts): void {
  state.memory.set(KEY_COUNTS, counts)
}

/**
 * Команда bash-вызова; `null` — вызов не предмет гейта.
 * @param call - вызов инструмента.
 * @returns текст команды либо `null`.
 */
function commandOf(call: GateCall): string | null {
  if (call.tool !== 'bash') return null
  const args = typeof call.args === 'object' && call.args !== null && !Array.isArray(call.args)
    ? call.args as Record<string, unknown>
    : {}
  const command = typeof args['command'] === 'string' ? args['command'] : ''
  return command === '' ? null : command
}

/**
 * Ключ команды — sha1 её текста, как у эталона.
 * @param command - текст команды.
 * @returns hex-дайджест.
 */
function commandKey(command: string): string {
  return createHash('sha1').update(command, 'utf8').digest('hex')
}

/** Причина отказа — текст канона. */
function blockMessage(count: number): string {
  return [
    'Третий идентичный повтор упавшей команды заблокирован (retry-discipline).',
    '',
    `Команда уже падала ${count} раза подряд. Повтор без изменения условий запрещён.`,
    '',
    'Дальше — развилка:',
    '  1. Диагностика: прочитать вывод ошибки целиком, сформулировать гипотезу.',
    '  2. Смена условий: другое дерево/инструмент/путь, исправить окружение.',
    '  3. Эскалация: сообщить пользователю с фактами (команда, вывод, что пробовал).',
    '',
    'Измени команду (хотя бы диагностической обвязкой) — счётчик идентичности сбросится.',
  ].join('\n')
}

/**
 * Решение до вызова: третий идентичный повтор после двух ошибок — отказ.
 * @param call - вызов инструмента.
 * @param state - состояние гейта (память и журнал).
 * @returns отказ либо пропуск.
 */
export function evaluatePre(call: GateCall, project: ContourProject | null, state: GateState): PreDecision {
  if (project === null) return { kind: 'allow', code: 'none' }
  const command = commandOf(call)
  if (command === null) return { kind: 'allow', code: 'none' }
  const count = loadCounts(state)[commandKey(command)]
  if (count === undefined || count < MAX_CONSECUTIVE) return { kind: 'allow', code: 'none' }
  state.audit(RULE, command.slice(0, 200), `третий идентичный повтор после ${count} ошибок подряд`)
  return { kind: 'deny', code: 'retry-discipline', reason: blockMessage(count) }
}

/**
 * Учесть финальный исход вызова из `tools/result`.
 *
 * Ошибка наращивает счётчик команды, успех — сбрасывает. Отказ политики
 * (`denied`) сбоем инструмента не является и не считается.
 * @param state - состояние гейта.
 * @param call - вызов инструмента.
 * @param isError - инструмент сообщил об ошибке.
 * @param denied - вызов отклонён политикой, а не упал в теле инструмента.
 */
export function observeResult(
  state: GateState,
  call: GateCall,
  project: ContourProject | null,
  isError: boolean,
  denied: boolean,
): void {
  if (project === null || denied) return
  const command = commandOf(call)
  if (command === null) return
  const key = commandKey(command)
  const counts = { ...loadCounts(state) }
  if (isError) {
    counts[key] = (counts[key] ?? 0) + 1
    saveCounts(state, counts)
    return
  }
  if (key in counts) {
    delete counts[key]
    saveCounts(state, counts)
  }
}