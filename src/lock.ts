/**
 * Замок на информационную базу: один файл состояния на ключ подключения.
 *
 * Формат и ключ унаследованы от ZCode-гейтов (`ib_lock`): замок — файл
 * состояния на ключ подключения. Корень состояния у DSH свой:
 * `DSH_1C_STATE_DIR` задаёт его напрямую.
 * @module dsh-1c-contour/lock
 */

import { createHash } from 'node:crypto'
import { mkdirSync, readdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { LockRecord } from './contract.js'
import { resolvedPath } from './paths.js'

/** Время жизни замка без продления — как у ZCode-эталона: 20 минут. */
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
  if (!filePart.startsWith('/')) filePart = resolvedPath(join(tree, filePart))
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

/**
 * Вымести просроченные замки инфобаз (старт сессии).
 *
 * Живёт рядом с форматом замка, чтобы правило «просрочен» было одно:
 * ZCode-эталон делал так же (`ib_lock.purge_stale_locks`).
 * @param stateDir - корень состояния контура.
 * @param nowSeconds - текущее время в секундах эпохи.
 * @returns сколько файлов удалено.
 */
export function purgeStaleLocks(stateDir: string, nowSeconds: number): number {
  const directory = join(stateDir, 'ib-locks')
  let entries: string[]
  try {
    entries = readdirSync(directory)
  } catch {
    return 0
  }
  let removed = 0
  for (const name of entries) {
    if (!name.endsWith('.json')) continue
    const path = join(directory, name)
    if (!isStaleLock(readLock(path) ?? {}, nowSeconds)) continue
    try {
      unlinkSync(path)
      removed += 1
    } catch {
      // Уже удалён или нет прав — уборка не повод падать.
    }
  }
  return removed
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
export function isStaleLock(lock: Partial<LockRecord>, nowSeconds: number): boolean {
  const expires = Number(lock.expires_ts ?? 0)
  return !Number.isFinite(expires) || expires <= nowSeconds
}

/** ISO-время для поля `started_at`. */
function isoNow(): string {
  return new Date().toISOString()
}

/**
 * Строка подключения без учётных данных.
 *
 * Пароль не должен жить в файлах состояния: `connection` — только для
 * разбора человеком, ключ замка считается отдельно и `Usr=`/`Pwd=` не
 * содержит.
 * @param connection - строка подключения проекта.
 * @returns строка без сегментов `Usr=` и `Pwd=`.
 */
export function withoutCredentials(connection: string): string {
  return connection
    .split(';')
    .filter((part) => {
      const head = part.trim().toLowerCase()
      return !head.startsWith('usr=') && !head.startsWith('pwd=')
    })
    .join(';')
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
  if (existing !== null && !isStaleLock(existing, nowSeconds) && existing.tree !== request.tree) {
    return { status: 'busy', holder: existing }
  }
  const lock: LockRecord = {
    connection: withoutCredentials(request.connection),
    tree: request.tree,
    session_id: request.sessionId,
    operation: request.operation,
    started_at: existing !== null && existing.tree === request.tree ? existing.started_at : isoNow(),
    expires_ts: nowSeconds + DEFAULT_TTL_SECONDS,
  }
  try {
    mkdirSync(join(stateDir, 'ib-locks'), { recursive: true })
    writeFileSync(path, `${JSON.stringify(lock, null, 2)}`, { encoding: 'utf8', mode: 0o600 })
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
 * Ключ stand-замка: `stand:` + нормализация без `Usr=`/`Pwd=`.
 *
 * Пароль в ключ не входит: смена пароля в local-слое не должна создавать
 * второй стенд на ту же базу. Пустые сегменты (хвост «;») отбрасываются.
 * @param connection - строка подключения.
 * @param tree - дерево проекта.
 * @returns sha1 нормализованной строки без учётных данных.
 */
export function standLockKey(connection: string, tree: string): string {
  const normalized = normalizedConnection(connection, tree)
    .split(';')
    .filter((part) => part !== '' && !part.startsWith('usr=') && !part.startsWith('pwd='))
    .join(';')
  return createHash('sha1').update(`stand:${normalized}`).digest('hex')
}

/**
 * Путь файла stand-замка.
 * @param stateDir - корень состояния контура.
 * @param connection - строка подключения.
 * @param tree - дерево проекта.
 * @returns абсолютный путь файла stand-замка.
 */
export function standLockPath(stateDir: string, connection: string, tree: string): string {
  return join(stateDir, 'stand-locks', `${standLockKey(connection, tree)}.json`)
}

/**
 * Живость владельца stand-замка свежей пробой `/proc/<pid>/stat`.
 *
 * Зомби (`Z`) считается мёртвым: базу он не держит. Процесса нет — мёртв;
 * `/proc` не читается (не Linux, hidepid) — владелец считается живым: смерть
 * живого стенда не угадывается. Битый pid — замок мёртв.
 * @param pid - pid владельца из записи замка.
 * @returns истина, когда владелец жив.
 */
export function standOwnerAlive(pid: unknown): boolean {
  const number = Number(pid)
  if (!Number.isInteger(number) || number <= 0) return false
  let stat: string
  try {
    stat = readFileSync(`/proc/${number}/stat`, 'utf8')
  } catch (error: unknown) {
    const code = (error as NodeJS.ErrnoException).code
    return code === 'ENOENT' ? false : true
  }
  const fields = stat.slice(stat.lastIndexOf(')') + 1).trim().split(/\s+/)
  return fields.length > 0 && fields[0] !== 'Z'
}

/**
 * Живой stand-замок на базу: запись либо `null`.
 *
 * Чистку не делает: протухший замок подчищает взятие.
 * @param stateDir - корень состояния контура.
 * @param connection - строка подключения.
 * @param tree - дерево проекта.
 * @returns запись замка с живым владельцем либо `null`.
 */
export function standLockStatus(stateDir: string, connection: string, tree: string): LockRecord | null {
  const lock = readLock(standLockPath(stateDir, connection, tree))
  if (lock !== null && standOwnerAlive(lock.pid)) return lock
  return null
}

/**
 * Описание держателя для сообщения о блокировке.
 * @param lock - запись замка.
 * @returns строка «дерево — операция, с …, сессия …».
 */
export function describeHolder(lock: LockRecord): string {
  const started = lock.started_at === '' || lock.started_at === undefined ? '?' : lock.started_at
  const operation = lock.operation === '' || lock.operation === undefined ? '?' : lock.operation
  if (lock.kind === 'stand') {
    return `${lock.tree} — операция ${operation}, с ${started}, процесс ${String(lock.pid ?? '?')}`
  }
  const session = lock.session_id === '' || lock.session_id === undefined ? '?' : lock.session_id
  return `${lock.tree} — операция ${operation}, с ${started}, сессия ${session}`
}
