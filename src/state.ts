/**
 * Состояние гейтов: журнал решений, замки, время.
 *
 * Корень состояния — `DSH_1C_STATE_DIR`, по умолчанию `~/.dsh/state/1c`
 * (решение 7 спеки). Замки и журнал лежат прямо под ним, без лишнего
 * `state/1c`: путь переменной уже называет контур.
 * @module dsh-1c-contour/state
 */

import { appendFileSync, mkdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

import type { GateMemory, GateState, LockRecord, LockRequest, StyleCheckerPort } from './contract.js'
import { acquireBaseLock, releaseBaseLock, standLockStatus } from './lock.js'

/**
 * Решение в журнале: `blocked` — отказ, `confirmed` — осознанное разрешение
 * (эскалация), `advisory` — замечание без отказа (план правки, холодные
 * нарушения вне правки). Набор совпадает с python-эталоном.
 */
export type AuditDecision = 'blocked' | 'confirmed' | 'advisory'

/** Запись журнала решений гейтов — формат omp сохранён. */
export interface AuditEntry {
  readonly rule: string
  readonly action: string
  readonly decision: AuditDecision
  readonly reason: string
  readonly timestamp: string
}

/**
 * Корень состояния контура.
 * @param env - окружение процесса.
 * @returns `DSH_1C_STATE_DIR` либо `~/.dsh/state/1c`.
 */
export function defaultStateDir(env: NodeJS.ProcessEnv = process.env): string {
  const configured = env['DSH_1C_STATE_DIR']
  if (configured !== undefined && configured !== '') {
    return configured.startsWith('~') ? join(homedir(), configured.slice(1)) : configured
  }
  return join(homedir(), '.dsh', 'state', '1c')
}

/**
 * Дописать запись в журнал решений. Ошибка записи не мешает работе гейта.
 * @param stateDir - корень состояния контура.
 * @param entry - запись журнала.
 */
export function appendAudit(stateDir: string, entry: AuditEntry): void {
  try {
    mkdirSync(join(stateDir, 'logs'), { recursive: true })
    appendFileSync(join(stateDir, 'logs', 'rule-audit.jsonl'), `${JSON.stringify(entry)}\n`, 'utf8')
  } catch {
    // Журнал — best-effort: недоступный каталог не повод пропускать решение.
  }
}

/** Параметры создания состояния. */
export interface GateStateOptions {
  /** Сессия-держатель; её видят журнал и замки. */
  readonly sessionId: string
  /** Корень состояния контура. */
  readonly stateDir?: string
  /** Часы в секундах эпохи — подменяются в тестах. */
  readonly nowSeconds?: () => number
  /** Память сессии: состояние гейтов между вызовами. */
  readonly memory?: GateMemory
  /** Чекер стиля BSL; без него стиль-гейт молчит. */
  readonly styleChecker?: StyleCheckerPort
}

/**
 * Память процесса по умолчанию: гейты без сессии не должны падать.
 * @returns память на одну жизнь процесса.
 */
function detachedMemory(): GateMemory {
  const store = new Map<string, unknown>()
  return {
    get: <T>(key: string): T | undefined => store.get(key) as T | undefined,
    set: (key: string, value: unknown): void => {
      store.set(key, value)
    },
  }
}

/**
 * Состояние гейта поверх файловой системы.
 * @param options - сессия, корень состояния и часы.
 * @returns порт побочных эффектов для чистых функций гейта.
 */
export function createGateState(options: GateStateOptions): GateState {
  const stateDir = options.stateDir ?? defaultStateDir()
  const clock = options.nowSeconds ?? (() => Date.now() / 1000)
  const nowSeconds = clock()
  return {
    sessionId: options.sessionId,
    nowSeconds,
    memory: options.memory ?? detachedMemory(),
    ...options.styleChecker === undefined ? {} : { styleChecker: options.styleChecker },
    audit(rule: string, action: string, reason: string, decision: AuditDecision = 'blocked'): void {
      appendAudit(stateDir, {
        rule,
        action,
        decision,
        reason,
        timestamp: new Date().toISOString(),
      })
    },
    acquireBaseLock(request: LockRequest): { readonly status: 'ok' } | { readonly status: 'busy'; readonly holder: LockRecord } {
      return acquireBaseLock(stateDir, request, nowSeconds)
    },
    releaseBaseLock(request: Omit<LockRequest, 'operation'>): void {
      releaseBaseLock(stateDir, request)
    },
    standLockStatus(request: { tree: string; connection: string }): LockRecord | null {
      return standLockStatus(stateDir, request.connection, request.tree)
    },
  }
}
