/**
 * Замок на информационную базу: один файл состояния на ключ подключения.
 *
 * Формат и ключ совпадают с `reference/gates/ib_lock.py`: файл замка читают
 * обе стороны, пока python-эталон жив (до паритета и пилота). Отличие одно —
 * корень состояния у DSH свой: `DSH_1C_STATE_DIR` задаёт его напрямую.
 * @module dsh-1c-contour/lock
 */

import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { LockRecord } from './contract.js'

/** Время жизни замка без продления — как у эталона: 20 минут. */
export const DEFAULT_TTL_SECONDS = 20 * 60

/**
 * Прочитать `infobase.connection` (legacy) или `infobases.origin.connection` (0.15)
 * из файла проекта построчным парсером.
 * @param path - путь к `v8project.yaml` или его локальному оверлею.
 * @returns строка подключения либо `null`.
 */
export function connectionFromFile(path: string): string | null {
  let lines: string[]
  try {
    lines = readFileSync(path, 'utf8').split('\n')
  } catch {
    return null
  }
  let top: string | null = null
  let inOrigin = false
  for (const line of lines) {
    const first = line.slice(0, 1)
    if (first !== ' ' && first !== '\t') {
      top = line.split(':', 1)[0]?.trim() ?? null
      inOrigin = false
      continue
    }
    const stripped = line.trim()
    if (top === 'infobases' && stripped.replace(/:$/, '').trim() === 'origin') {
      inOrigin = true
      continue
    }
    if (!stripped.startsWith('connection:')) continue
    if (top === 'infobase' || (top === 'infobases' && inOrigin)) {
      const value = stripped.slice('connection:'.length).trim().replace(/^['"]|['"]$/g, '')
      if (value !== '') return value
    }
  }
  return null
}

/**
 * Строка подключения к базе проекта: локальный оверлей перекрывает основной файл.
 * @param tree - дерево проекта.
 * @returns строка подключения либо `null`.
 */
export function resolveInfobaseConnection(tree: string): string | null {
  return connectionFromFile(join(tree, 'v8project.local.yaml')) ?? connectionFromFile(join(tree, 'v8project.yaml'))
}

/**
 * Нормализованная строка подключения: регистр и пробелы прочь, относительный
 * путь файловой базы разворачивается от дерева.
 * @param connection - исходная строка.
 * @param tree - дерево проекта.
 * @returns нормализованная строка.
 */
export function normalizedConnection(connection: string, tree: string): string {
  const norm = connection.toLowerCase().replaceAll(/\s+/g, '')
  if (!norm.startsWith('file=')) return norm
  let filePart = norm.slice('file='.length)
  if (!filePart.startsWith('/')) filePart = join(tree, filePart)
  return `file=${filePart}`
}

/**
 * Ключ замка инфобазы.
 * @param connection - строка подключения.
 * @param tree - дерево проекта.
 * @returns sha1 нормализованной строки подключения.
 */
export function lockKey(connection: string, tree: string): string {
  return createHash('sha1').update(normalizedConnection(connection, tree)).digest('hex')
}

/**
 * Путь файла замка инфобазы.
 * @param stateDir - корень состояния контура.
 * @param connection - строка подключения.
 * @param tree - дерево проекта.
 * @returns абсолютный путь файла замка.
 */
export function lockPath(stateDir: string, connection: string, tree: string): string {
  return join(stateDir, 'ib-locks', `${lockKey(connection, tree)}.json`)
}

/** Прочитать запись замка; отсутствующий или битый файл — `null`. */
function readLock(path: string): LockRecord | null {
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, 'utf8'))
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return null
    return parsed as LockRecord
  } catch {
    return null
  }
}

/**
 * Замок мёртв: истёк срок. Проверки pid здесь нет — в хосте держатель
 * идентифицируется деревом и сессией, а не процессом.
 * @param lock - запись замка.
 * @param nowSeconds - текущее время в секундах эпохи.
 * @returns истина, когда замок просрочен.
 */
function isStale(lock: Partial<LockRecord>, nowSeconds: number): boolean {
  const expires = Number(lock.expires_ts ?? 0)
  return !Number.isFinite(expires) || expires <= nowSeconds
}

/** ISO-время для поля `started_at`. */
function isoNow(): string {
  return new Date().toISOString()
}

/**
 * Взять или продлить замок инфобазы.
 *
 * Своё дерево продлевает замок и сохраняет `started_at`; чужое дерево при
 * живом замке получает `busy` с записью держателя. Ошибка записи в каталог
 * состояния работу не блокирует.
 * @param stateDir - корень состояния контура.
 * @param request - дерево, подключение, операция и сессия.
 * @param nowSeconds - текущее время в секундах эпохи.
 * @returns `ok` либо `busy` с держателем.
 */
export function acquireBaseLock(
  stateDir: string,
  request: { tree: string; connection: string; operation: string; sessionId: string },
  nowSeconds: number,
): { status: 'ok' } | { status: 'busy'; holder: LockRecord } {
  const path = lockPath(stateDir, request.connection, request.tree)
  const existing = readLock(path)
  if (existing !== null && !isStale(existing, nowSeconds) && existing.tree !== request.tree) {
    return { status: 'busy', holder: existing }
  }
  const lock: LockRecord = {
    connection: request.connection,
    tree: request.tree,
    session_id: request.sessionId,
    operation: request.operation,
    started_at: existing !== null && existing.tree === request.tree ? existing.started_at : isoNow(),
    expires_ts: nowSeconds + DEFAULT_TTL_SECONDS,
  }
  try {
    mkdirSync(join(stateDir, 'ib-locks'), { recursive: true })
    writeFileSync(path, `${JSON.stringify(lock, null, 2)}`, 'utf8')
  } catch {
    // Нет доступа к каталогу состояния — из-за этого работу не блокируем.
  }
  return { status: 'ok' }
}

/**
 * Отпустить замок своего дерева.
 * @param stateDir - корень состояния контура.
 * @param request - дерево, подключение и сессия.
 */
export function releaseBaseLock(
  stateDir: string,
  request: { tree: string; connection: string; sessionId: string },
): void {
  const path = lockPath(stateDir, request.connection, request.tree)
  const lock = readLock(path)
  if (lock === null || lock.tree !== request.tree) return
  if (lock.session_id !== request.sessionId && lock.session_id !== null) return
  try {
    unlinkSync(path)
  } catch {
    // Истечёт по TTL.
  }
}

/**
 * Описание держателя для сообщения о блокировке.
 * @param lock - запись замка.
 * @returns строка «дерево — операция, с …, сессия …».
 */
export function describeHolder(lock: LockRecord): string {
  const started = lock.started_at === '' ? '?' : lock.started_at
  const operation = lock.operation === '' ? '?' : lock.operation
  return `${lock.tree} — операция ${operation}, с ${started}, сессия ${lock.session_id === '' ? '?' : lock.session_id}`
}
