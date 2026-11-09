/**
 * Классификатор решения python-эталона по наблюдаемому выводу.
 *
 * Эталон не печатает класс причины — только текст. Класс выводится из текста
 * независимо от TS-реализации: это и есть сверка «класс причины совпадает»,
 * а не пересказ собственного кода.
 * @module tests/parity/classify
 */

import type { OracleResult } from './oracle.js'

/** Класс решения эталона. */
export type OracleClass =
  | 'allow'
  | 'source-edit'
  | 'source-bash'
  | 'worktree-uninit'
  | 'rebuild-rule'
  | 'ib-lock'
  | 'docs-unverified'
  | 'mcp-unsafe'
  | 'stand-lock'
  | 'bsl-style'
  | 'bsl-module'
  | 'unknown'

/** Маркеры блокирующих сообщений эталона — по одному на класс. */
const MARKERS: readonly { readonly marker: string; readonly code: Exclude<OracleClass, 'allow' | 'unknown'> }[] = [
  { marker: 'Прямое редактирование исходников 1С', code: 'source-edit' },
  { marker: 'Запись в исходники 1С через bash', code: 'source-bash' },
  { marker: 'Ворктри не инициализирован', code: 'worktree-uninit' },
  { marker: 'База занята другим деревом', code: 'ib-lock' },
  { marker: 'без сверки с документацией платформы', code: 'docs-unverified' },
  { marker: 'меняет данные/состояние базы', code: 'mcp-unsafe' },
  { marker: 'не поднимает второй клиент', code: 'stand-lock' },
  { marker: 'нарушает стиль BSL', code: 'bsl-style' },
  { marker: 'нарушает контракт носителя', code: 'bsl-module' },
]

/**
 * Определить класс решения эталона.
 *
 * Код возврата 0 — пропуск; 2 — блок, класс по маркеру сообщения. Остальные
 * блоки эталон отдаёт только сообщением правила пересборки, у которого нет
 * своего маркера.
 * @param result - код возврата и stderr гейта-процесса.
 * @returns класс решения.
 */
export function classifyOracle(result: OracleResult): OracleClass {
  return classifyText(result.exitCode, result.stderr)
}

/**
 * Класс причины по тексту блокирующего сообщения.
 * @param exitCode - код возврата.
 * @param stderr - текст причины.
 * @returns класс решения.
 */
export function classifyText(exitCode: number, stderr: string): OracleClass {
  if (exitCode === 0) return 'allow'
  for (const { marker, code } of MARKERS) {
    if (stderr.includes(marker)) return code
  }
  // Блок без маркера гейт unica_source отдаёт только сообщением правила
  // пересборки; кейсы корпуса сверяют там ещё и точный текст причины.
  if (exitCode === 2) return 'rebuild-rule'
  return 'unknown'
}
