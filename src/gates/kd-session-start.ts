/**
 * `kd_session_start` на DSH: будим станцию kd-rules-mcp на старте сессии.
 *
 * Порт `reference/gates/kd_session_start.py`. MCP `1c-kd` разведён как
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
import { readFileSync, statSync } from 'node:fs'
import { request as httpRequest } from 'node:http'
import { request as httpsRequest } from 'node:https'
import { homedir } from 'node:os'
import { join } from 'node:path'

import type { ContourProject } from '../contract.js'
import { readJsonObject } from '../paths.js'

/** Таймаут пробы живости станции. */
const ALIVE_TIMEOUT_MS = 1000

/** Сколько не повторять подъём после запуска: станция поднимается не мгновенно. */
const WAKE_COOLDOWN_MS = 30_000

/** Последний запуск по ключу станции — защита от лавины параллельных сессий. */
const lastWake = new Map<string, number>()

/** Каталог станции по умолчанию; переопределяется `KD_RULES_STATION`. */
export function stationDir(env: NodeJS.ProcessEnv = process.env): string {
  const configured = env['KD_RULES_STATION']
  return configured !== undefined && configured !== '' ? configured : join(homedir(), '.local', 'share', '1c-zcode', 'kd-rules-mcp')
}

/**
 * Url из legacy-конфига хоста: `.zcode/config.json → mcp.servers["1c-kd"].url`.
 * В DSH запись MCP поднимает пресет, поэтому канон — `contour.json`; конфиг
 * остаётся для переездных проектов и python-эталона.
 */
function urlFromLegacyConfig(root: string): string {
  const config = readJsonObject(join(root, '.zcode', 'config.json'))
  const mcp = config['mcp']
  if (typeof mcp !== 'object' || mcp === null || Array.isArray(mcp)) return ''
  const servers = (mcp as Record<string, unknown>)['servers']
  if (typeof servers !== 'object' || servers === null || Array.isArray(servers)) return ''
  const server = (servers as Record<string, unknown>)['1c-kd']
  if (typeof server !== 'object' || server === null || Array.isArray(server)) return ''
  const url = (server as Record<string, unknown>)['url']
  return typeof url === 'string' ? url : ''
}

/**
 * URL станции `1c-kd`: `contour.json → 1c_kd.url`, затем конфиг хоста.
 * @param project - проект контура либо `null`.
 * @param root - корень проекта, когда конфига контура нет.
 * @returns url либо пустая строка.
 */
export function kdUrl(project: ContourProject | null, root: string): string {
  const contourKd = project?.contour['1c_kd']
  if (typeof contourKd === 'object' && contourKd !== null && !Array.isArray(contourKd)) {
    const url = (contourKd as Record<string, unknown>)['url']
    if (typeof url === 'string' && url !== '') return url
  }
  return urlFromLegacyConfig(project?.root ?? root)
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

/** Скрипт старта станции: новая раскладка первой, legacy второй. */
export function startScript(root: string): string | null {
  for (const name of ['.dsh', '.zcode']) {
    const path = join(root, name, '1c', 'scripts', 'start_kd_rules.sh')
    try {
      if (statSync(path).isFile()) return path
    } catch {
      // Нет файла — смотрим следующую раскладку.
    }
  }
  return null
}

/**
 * Поднять станцию, если она спит и разведена в проекте.
 * @param project - проект контура либо `null`.
 * @param cwd - каталог сессии.
 * @param env - окружение процесса.
 * @returns истина, когда стартовый скрипт запущен.
 */
export async function ensureKdStation(
  project: ContourProject | null,
  cwd: string,
  env: NodeJS.ProcessEnv = process.env,
): Promise<boolean> {
  const root = project?.root ?? cwd
  const url = kdUrl(project, root)
  if (url === '') return false
  if (await stationAlive(url)) return false
  const station = stationDir(env)
  try {
    if (!statSync(join(station, 'pyproject.toml')).isFile()) return false
  } catch {
    return false
  }
  const script = startScript(root)
  if (script === null) return false
  const key = `${station}|${script}|${url}`
  const last = lastWake.get(key) ?? 0
  if (Date.now() - last < WAKE_COOLDOWN_MS) return false
  try {
    const child = spawn('bash', [script, '--url', url], {
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