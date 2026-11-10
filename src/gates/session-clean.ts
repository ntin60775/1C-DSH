/**
 * `session_clean` на DSH: уборка состояния на старте сессии.
 *
 * Порт `reference/gates/session_clean.py`. В DSH нет события остановки
 * сессии, поэтому старт каждой сессии выметает:
 *
 * - просроченные замки инфобаз (`ib-locks/`, TTL истёк);
 * - файлы состояния гейтов старше семи дней (`doc-gate`, `retry`,
 *   `style-bridge`) — унаследованные файлы ZCode; в DSH эти гейты живут
 *   в памяти сессии, поэтому уборка снимает только переездные остатки.
 *
 * Уборка best-effort: ошибка не роняет сессию, счётчики возвращаются для
 * журнала хоста.
 * @module dsh-1c-contour/gates/session-clean
 */

import { readdirSync, statSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'

import { purgeStaleLocks } from '../lock.js'

/** Через сколько дней файлы состояния считаются забытыми. */
export const CLEAN_AFTER_DAYS = 7

/** Каталоги файлового состояния гейтов, которые выметаются по возрасту. */
const STATE_DIRS: readonly string[] = ['doc-gate', 'retry', 'style-bridge']

/** Итог уборки. */
export interface CleanReport {
  /** Сколько просроченных замков удалено. */
  readonly locks: number
  /** Сколько старых файлов состояния удалено. */
  readonly files: number
}

/** Удалить файлы состояния старше `CLEAN_AFTER_DAYS`. */
function purgeOldState(stateDir: string, nowSeconds: number): number {
  const cutoff = nowSeconds - CLEAN_AFTER_DAYS * 86400
  let removed = 0
  for (const name of STATE_DIRS) {
    const directory = join(stateDir, name)
    let entries: string[]
    try {
      entries = readdirSync(directory)
    } catch {
      continue
    }
    for (const file of entries) {
      if (!file.endsWith('.json')) continue
      const path = join(directory, file)
      try {
        if (statSync(path).mtimeMs / 1000 < cutoff) {
          unlinkSync(path)
          removed += 1
        }
      } catch {
        // Нет файла или прав — пропускаем.
      }
    }
  }
  return removed
}

/**
 * Вымести просроченные замки и старые файлы состояния.
 * @param stateDir - корень состояния контура (`DSH_1C_STATE_DIR`).
 * @param nowSeconds - текущее время в секундах эпохи.
 * @returns счётчики уборки.
 */
export function cleanSessionState(stateDir: string, nowSeconds: number): CleanReport {
  return {
    locks: purgeStaleLocks(stateDir, nowSeconds),
    files: purgeOldState(stateDir, nowSeconds),
  }
}