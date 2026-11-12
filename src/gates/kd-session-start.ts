/**
 * `kd_session_start` на DSH: будим станцию kd-rules-mcp на старте сессии.
 *
 * Порт ZCode-гейта `kd_session_start`. MCP `1c-kd` разведён как
 * http-запись: хост подключается к уже работающему сервису и процесс сам не
 * поднимает. Гейт закрывает разрыв: в проекте с разведённым `1c-kd` пробует
 * URL, станция спит — поднимает `start_kd_rules.sh` фоном. Станция общая на
 * машину и гейтом не гасится.
 *
 * Правила: готовности не ждём (стартовый скрипт сам дожидается и пишет лог),
 * станции нет на машине — молча выходим, ошибки не роняют сессию.
 * @module dsh-1c-contour/gates/kd-session-start
 */

import { spawn } from 'node:child_process'
import { statSync } from 'node:fs'
import { request as httpRequest } from 'node:http'
import { request as httpsRequest } from 'node:https'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { ContourProject } from '../contract.js'

/** Таймаут пробы живости станции. */
const ALIVE_TIMEOUT_MS = 1000

/** Сколько не повторять подъём после запуска: станция поднимается не мгновенно. */
const WAKE_COOLDOWN_MS = 30_000

/** Последний запуск по ключу станции — защита от лавины параллельных сессий. */
const lastWake = new Map<string, number>()

/** Корень пакета контура: скрипты живут в нём, проект их не вендорит. */
const PACKAGE_ROOT = fileURLToPath(new URL('../../', import.meta.url))

/** Каталог станции по умолчанию; переопределяется `KD_RULES_STATION`. */
export function stationDir(env: NodeJS.ProcessEnv = process.env): string {
  const configured = env['KD_RULES_STATION']
  return configured !== undefined && configured !== '' ? configured : join(homedir(), '.local', 'share', '1c-dsh', 'kd-rules-mcp')
}

/**
 * URL станции `1c-kd`: канон `contour.json → 1c_kd.url`. Legacy-фолбэк
 * конфига хоста снят после перевода парка (Т10): запись MCP поднимает
 * пресет, адрес живёт только во входах контура.
 * @param project - проект контура либо `null`.
 * @returns url либо пустая строка.
 */
export function kdUrl(project: ContourProject | null): string {
  const contourKd = project?.contour['1c_kd']
  if (typeof contourKd === 'object' && contourKd !== null && !Array.isArray(contourKd)) {
    const url = (contourKd as Record<string, unknown>)['url']
    if (typeof url === 'string' && url !== '') return url
  }
  return ''
}

/**
 * Станция отвечает на HTTP; 406 на голый GET — тоже ответ, то есть жива.
 * @param url - адрес MCP-сервера.
 * @returns истина, когда станция жива.
 */
export function stationAlive(url: string, timeoutMs = ALIVE_TIMEOUT_MS): Promise<boolean> {
  return new Promise((resolve) => {
    let parsed: URL
    try {
      parsed = new URL(url)
    } catch {
      resolve(false)
      return
    }
    const send = parsed.protocol === 'https:' ? httpsRequest : parsed.protocol === 'http:' ? httpRequest : null
    if (send === null) {
      resolve(false)
      return
    }
    let settled = false
    const finish = (alive: boolean): void => {
      if (settled) return
      settled = true
      resolve(alive)
    }
    const req = send(parsed, { method: 'GET', timeout: timeoutMs }, (res) => {
      res.resume()
      finish(true)
    })
    req.on('timeout', () => {
      req.destroy()
      finish(false)
    })
    req.on('error', () => finish(false))
    req.end()
  })
}

/**
 * Скрипт старта станции: канон `$DSH_1C_ROOT/scripts` пакета контура;
 * переопределяется `KD_START_SCRIPT` (тестовый шов и нестандартная поставка).
 * @param env - окружение процесса.
 * @returns путь скрипта либо `null`, когда файла нет.
 */
export function startScript(env: NodeJS.ProcessEnv = process.env): string | null {
  const configured = env['KD_START_SCRIPT']
  const path = configured !== undefined && configured !== ''
    ? configured
    : join(PACKAGE_ROOT, 'scripts', 'start_kd_rules.sh')
  try {
    if (statSync(path).isFile()) return path
  } catch {
    // Скрипта нет в поставке — поднимать нечем.
  }
  return null
}

/**
 * Поднять станцию, если она спит и разведена в проекте.
 * @param project - проект контура либо `null`.
 * @param env - окружение процесса; `KD_START_SCRIPT` переопределяет
 *   стартовый скрипт (тестовый шов и нестандартная поставка).
 * @returns истина, когда стартовый скрипт запущен.
 */
export async function ensureKdStation(
  project: ContourProject | null,
  env: NodeJS.ProcessEnv = process.env,
): Promise<boolean> {
  const url = kdUrl(project)
  if (url === '') return false
  if (await stationAlive(url)) return false
  const station = stationDir(env)
  try {
    if (!statSync(join(station, 'pyproject.toml')).isFile()) return false
  } catch {
    return false
  }
  const startPath = startScript(env)
  if (startPath === null) return false
  const key = `${station}|${startPath}|${url}`
  const last = lastWake.get(key) ?? 0
  if (Date.now() - last < WAKE_COOLDOWN_MS) return false
  try {
    const child = spawn('bash', [startPath, '--url', url], {
      detached: true,
      stdio: 'ignore',
    })
    child.unref()
    lastWake.set(key, Date.now())
    return true
  } catch {
    return false
  }
}