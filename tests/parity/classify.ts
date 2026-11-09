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
  | 'unknown'

/** Маркеры блокирующих сообщений эталона — по одному на класс. */
const MARKERS: readonly { readonly marker: string; readonly code: Exclude<OracleClass, 'allow' | 'unknown'> }[] = [
  { marker: 'Прямое редактирование исходников 1С', code: 'source-edit' },
  { marker: 'Запись в исходники 1С через bash', code: 'source-bash' },
  { marker: 'Ворктри не инициализирован', code: 'worktree-uninit' },
  { marker: 'База занята другим деревом', code: 'ib-lock' },
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
  if (result.exitCode === 0) return 'allow'
  for (const { marker, code } of MARKERS) {
    if (result.stderr.includes(marker)) return code
  }
  if (result.exitCode === 2) return 'rebuild-rule'
  return 'unknown'
}
