/**
 * Засев замков контура в фикстуру: запись по формату контракта.
 *
 * Паритет-корпус проигрывается на векторах (`tests/vectors/`); замок в
 * фикстуру кладётся записью `LockRecord` из `src/contract.ts` — тем же
 * форматом, который гейт читает с диска. Ключ и путь считаются по контракту
 * (`lockPath`/`standLockPath`), содержимое записи задаётся здесь явно.
 * @module tests/helpers/locks
 */

import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

import type { LockRecord } from '../../src/contract.js'
import { lockPath, resolveInfobaseConnection, standLockPath } from '../../src/lock.js'

/** Записать замок на диск, создав каталоги. */
function writeLock(path: string, lock: LockRecord): void {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, `${JSON.stringify(lock, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
}

/**
 * Положить замок инфобазы так, как его кладёт гейт-держатель.
 * @param options - дерево-держатель, дерево-источник строки подключения,
 * операция, сессия и корень состояния.
 * @returns строка подключения, взятая из дерева-источника.
 */
export function seedBaseLock(options: {
  readonly holderTree: string
  readonly connectionSourceTree: string
  readonly operation: string
  readonly sessionId: string
  readonly stateDir: string
}): string {
  const connection = resolveInfobaseConnection(options.connectionSourceTree)
  if (connection === null) {
    throw new Error(`строка подключения не найдена: ${options.connectionSourceTree}`)
  }
  const now = Date.now() / 1000
  writeLock(lockPath(options.stateDir, connection, options.holderTree), {
    connection,
    tree: options.holderTree,
    session_id: options.sessionId,
    operation: options.operation,
    started_at: new Date().toISOString(),
    expires_ts: now + 20 * 60,
  })
  return connection
}

/**
 * Положить stand-замок с живым владельцем.
 * @param options - дерево, строка подключения, pid владельца и корень состояния.
 */
export function seedStandLock(options: {
  readonly tree: string
  readonly connection: string
  readonly pid: number
  readonly stateDir: string
}): void {
  const now = Date.now() / 1000
  writeLock(standLockPath(options.stateDir, options.connection, options.tree), {
    connection: options.connection,
    tree: options.tree,
    session_id: '',
    operation: 'stand',
    started_at: new Date().toISOString(),
    expires_ts: now + 20 * 60,
    kind: 'stand',
    pid: options.pid,
  })
}
