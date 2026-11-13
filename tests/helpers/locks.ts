/**
 * Засев замков контура в фикстуру: формат независимого писателя.
 *
 * Stand-замок кладётся литералом ровно в том формате, который пишет
 * python-стартер (`scripts/ib_lock.py`, `acquire_stand_lock`): поля
 * `kind/connection/tree/pid/operation/started_at`, без `session_id` и
 * `expires_ts`. TS-гейт обязан прочитать запись другого рантайма — это
 * инвариант совместимости, а не пересказ собственного формата.
 * Замок инфобазы кладётся по формату контракта (`LockRecord`).
 * Ключ и путь считаются по контракту (`lockPath`/`standLockPath`).
 * @module tests/helpers/locks
 */

import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

import type { LockRecord } from '../../src/contract.js'
import { lockPath, resolveInfobaseConnection, standLockPath } from '../../src/lock.js'

/** Записать замок на диск, создав каталоги. */
function writeLock(path: string, lock: Record<string, unknown>): void {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, `${JSON.stringify(lock, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
}

/** Строка подключения без Usr=/Pwd= — правило маскирования контура
 * (`ib_lock.without_credentials`; на входе тестов кавычек с `;` нет). */
function withoutCredentials(connection: string): string {
  return connection
    .split(';')
    .filter((part) => !['usr=', 'pwd='].includes(part.trim().toLowerCase().slice(0, 4)))
    .join(';')
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
 * Положить stand-замок с живым владельцем — формат python-стартера.
 * @param options - дерево, строка подключения, pid владельца и корень состояния.
 */
export function seedStandLock(options: {
  readonly tree: string
  readonly connection: string
  readonly pid: number
  readonly stateDir: string
}): void {
  writeLock(standLockPath(options.stateDir, options.connection, options.tree), {
    kind: 'stand',
    connection: withoutCredentials(options.connection),
    tree: options.tree,
    pid: options.pid,
    operation: 'start_client',
    started_at: new Date().toISOString(),
  })
}
