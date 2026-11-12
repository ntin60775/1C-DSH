/**
 * Классификатор блокирующего сообщения по классу причины.
 *
 * Векторы корпуса (`tests/vectors/`) хранят класс причины каждого отказа;
 * текст сообщения гейта классифицируется независимо по маркерам — тем же
 * словарём, что был сверен с python-эталоном до его снятия (Т8). Так
 * корпус проверяет и класс, и текст, а не пересказ собственного кода.
 * @module tests/parity/classify
 */

import type { ReasonCode } from '../../src/contract.js'

/** Класс решения в корпусе: пропуск, класс причины либо нераспознанный блок. */
export type ReasonClass =
  | 'allow'
  | Exclude<ReasonCode, 'none' | 'escalation-granted'>
  | 'unknown'

/** Маркеры блокирующих сообщений — по одному на класс. */
export const MARKERS: readonly { readonly marker: string; readonly code: Exclude<ReasonClass, 'allow' | 'unknown'> }[] = [
  { marker: 'Прямое редактирование исходников 1С', code: 'source-edit' },
  { marker: 'Запись в исходники 1С через bash', code: 'source-bash' },
  { marker: 'Ворктри не инициализирован', code: 'worktree-uninit' },
  { marker: 'База занята другим деревом', code: 'ib-lock' },
  { marker: 'без сверки с документацией платформы', code: 'docs-unverified' },
  { marker: 'меняет данные/состояние базы', code: 'mcp-unsafe' },
  { marker: 'не поднимает второй клиент', code: 'stand-lock' },
  { marker: 'нарушает стиль BSL', code: 'bsl-style' },
  { marker: 'нарушает контракт носителя', code: 'bsl-module' },
  { marker: 'отключение анонимизации', code: 'anon-raw' },
  { marker: 'Третий идентичный повтор', code: 'retry-discipline' },
]

/**
 * Класс причины по тексту блокирующего сообщения.
 *
 * Блок без маркера гейт unica_source отдаёт только сообщением правила
 * пересборки; кейсы корпуса сверяют там ещё и точный текст причины.
 * @param exitCode - код решения: 0 — пропуск, 2 — блок.
 * @param text - текст причины.
 * @returns класс решения.
 */
export function classifyText(exitCode: number, text: string): ReasonClass {
  if (exitCode === 0) return 'allow'
  for (const { marker, code } of MARKERS) {
    if (text.includes(marker)) return code
  }
  if (exitCode === 2) return 'rebuild-rule'
  return 'unknown'
}
