/**
 * Адаптер python-эталона: ZCode-payload на stdin, код возврата и stderr.
 *
 * Эталон запускается процессом — ровно так, как его вызывал ZCode. Никакого
 * импорта python-кода в тест: паритет сверяет наблюдаемое поведение, а не
 * общую реализацию.
 * @module tests/parity/oracle
 */

import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

/** Каталог эталонных гейтов. */
export const GATES_DIR = fileURLToPath(new URL('../../reference/gates/', import.meta.url))

/** Скрипт гейта в эталоне по умолчанию. */
export const GATE_SCRIPT = fileURLToPath(new URL('../../reference/gates/unica_source_gate.py', import.meta.url))

/**
 * Скрипт эталонного гейта по имени файла.
 * @param name - имя файла гейта в `reference/gates`.
 * @returns абсолютный путь скрипта.
 */
export function gateScript(name: string): string {
  return fileURLToPath(new URL(`../../reference/gates/${name}`, import.meta.url))
}

/** Путь моста замков ИБ. */
const LOCK_BRIDGE = fileURLToPath(new URL('../helpers/ib-lock-bridge.py', import.meta.url))

/** Результат прогона гейта-процесса. */
export interface OracleResult {
  /** Код возврата: 0 — проходит, 2 — блокирует. */
  readonly exitCode: number
  /** Причина в stderr при блоке. */
  readonly stderr: string
}

/** Интерпретатор python; переопределяется переменной `PYTHON`. */
function python(): string {
  return process.env['PYTHON'] ?? 'python3'
}

/**
 * Прогнать эталонный гейт.
 * @param event - ZCode-payload хука.
 * @param options - корень состояния и рабочий каталог процесса.
 * @returns код возврата и stderr.
 */
export function runOracle(
  event: unknown,
  options: { stateDir: string; cwd: string; script?: string },
): OracleResult {
  const script = options.script ?? GATE_SCRIPT
  const result = spawnSync(python(), [script], {
    input: JSON.stringify(event),
    cwd: options.cwd,
    encoding: 'utf8',
    env: { ...process.env, ZCODE_1C_STATE_DIR: options.stateDir },
  })
  return { exitCode: result.status ?? -1, stderr: result.stderr ?? '' }
}

/**
 * Положить stand-замок так, как это делает эталон.
 * @param options - дерево, строка подключения и pid живого владельца.
 */
export function seedStandLock(options: {
  readonly tree: string
  readonly connection: string
  readonly pid: number
  readonly legacyStateDir: string
}): void {
  const result = spawnSync(python(), [LOCK_BRIDGE, 'stand-acquire', options.tree, options.connection, String(options.pid)], {
    encoding: 'utf8',
    env: {
      ...process.env,
      ZCODE_1C_STATE_DIR: options.legacyStateDir,
      CONTOUR_GATES: GATES_DIR,
    },
  })
  if (result.status !== 0) throw new Error(`stand-замок не встал: ${result.stderr || result.stdout}`)
}

/**
 * Положить замок инфобазы так, как это делает эталон.
 * @param options - деревья, операция, сессия и общий корень состояния.
 * @returns строка подключения, взятая эталоном из дерева.
 */
export function seedPythonLock(options: {
  readonly holderTree: string
  readonly connectionSourceTree: string
  readonly operation: string
  readonly sessionId: string
  readonly legacyStateDir: string
}): string {
  const result = spawnSync(
    python(),
    [LOCK_BRIDGE, 'acquire', options.holderTree, options.operation, options.sessionId, options.connectionSourceTree],
    {
      encoding: 'utf8',
      env: {
        ...process.env,
        ZCODE_1C_STATE_DIR: options.legacyStateDir,
        CONTOUR_GATES: GATES_DIR,
      },
    },
  )
  if (result.status !== 0) {
    throw new Error(`замок python не встал: ${result.stderr || result.stdout}`)
  }
  const parsed = JSON.parse(result.stdout) as { status?: string; connection?: string; error?: string }
  if (parsed.status !== 'ok' || typeof parsed.connection !== 'string') {
    throw new Error(`замок python не встал: ${result.stdout}`)
  }
  return parsed.connection
}
