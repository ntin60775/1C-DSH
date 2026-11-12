/**
 * Пресет `1c`: раскладка проекта → каталоги скиллов и MCP-записи.
 *
 * Чистые функции без Cordis: юнит-тесты проверяют их на временных деревьях,
 * а `index.ts` только монтирует результат. Источник адресов — проектный
 * `contour.json` с локальным оверлеем и `db-instances.yaml`; семантика
 * повторяет `scripts/wire_config.py`, который разводил те же записи в
 * ZCode-эпоху.
 *
 * Раскладка читается как `.dsh/1c`, затем legacy `.zcode/1c`: проект
 * переезжает мигратором, и до переезда записи должны оставаться живыми.
 * @module dsh-1c-contour/preset/config
 */

import { homedir } from 'node:os'
import { join } from 'node:path'

import type { ContourProject } from '../contract.js'

/** Идентификатор пресета: сессия без него доставки не получает. */
export const PRESET_ID = '1c'

/** Пресет, который владеет проектом, — только `1c`; прочие не трогаются. */
export const PRESET_ROW = `preset-${PRESET_ID}`

/** Адрес 1c-db по умолчанию: локальный прокси тулкита. */
export const DEFAULT_DB_URL = 'http://127.0.0.1:6003/mcp'

/** Адрес станции правил обмена по умолчанию. */
export const DEFAULT_KD_URL = 'http://127.0.0.1:8060/mcp'

/** Имя инстанса 1c-db, годного в имя MCP-сервера и matcher гейта. */
const INSTANCE_NAME_RE = /^[a-z0-9_-]{1,64}$/

/** Канал изоляции прокси: 1c-mcp-toolkit уводит иное в default молча. */
const CHANNEL_RE = /^[A-Za-z0-9_-]{1,64}$/

/** Конфигурация одного MCP-сервера в терминах `@deepseek-ai/dsh-mcp-client`. */
export interface McpServerConfig {
  readonly serverName: string
  readonly transport: 'stdio' | 'streamable-http'
  readonly command?: string
  readonly args?: string[]
  readonly env?: Record<string, string>
  readonly url?: string
  readonly cwd?: string
  readonly startupTimeoutMs?: number
  readonly failOnStartupError?: boolean
}

/** Строка доставки: идентификатор строки Loader и конфигурация сервера. */
export interface McpRow {
  readonly id: string
  readonly config: McpServerConfig
}

/** Окружение доставки: домашний каталог и подмены команд. */
export interface DeliveryEnv {
  /** Корень пакета контура: скрипты и скиллы. */
  readonly packageRoot: string
  /** Домашний каталог машины. */
  readonly home: string
  /** Окружение процесса: `UNICA_LAUNCHER`, `DSH_1C_TESTPILOT`. */
  readonly env?: NodeJS.ProcessEnv
}

/** Глобальная установка Юники: кэш рантаймов. */
export function unicaRuntimeCache(home: string): string {
  return join(home, '.local', 'share', 'unica', 'runtimes')
}

/** Скиллы Юники, поставленные на машину одной версией. */
export function unicaSkillsDir(home: string): string {
  return join(home, '.local', 'share', 'unica', 'skills')
}

/**
 * Каталог проектных данных контура.
 *
 * `contour.json` уже нашёл раскладку (`ContourProject.layout`); для проекта
 * на маркерах без конфига предпочитаем новую `.dsh/1c`, а если она не
 * создана — legacy `.zcode/1c`.
 * @param project - найденный проект контура.
 * @param exists - проверка существования каталога (для тестов подменяема).
 * @returns каталог, из которого читаются проектные файлы.
 */
export function dataDirOf(project: ContourProject, exists: (path: string) => boolean = () => false): string {
  if (project.layout === 'dsh') return join(project.root, '.dsh', '1c')
  if (project.layout === 'zcode') return join(project.root, '.zcode', '1c')
  const modern = join(project.root, '.dsh', '1c')
  return exists(modern) ? modern : join(project.root, '.zcode', '1c')
}

/** Чтение JSON-объекта; битый или отсутствующий файл — пустой объект. */
function readObject(text: string | null): Record<string, unknown> {
  if (text === null) return {}
  try {
    const parsed: unknown = JSON.parse(text)
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {}
  } catch {
    return {}
  }
}

/** Вложенный объект по ключу; не объект — пустой. */
function objectAt(source: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = source[key]
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

/** Непустая строка из поля JSON. */
function stringAt(source: Record<string, unknown>, key: string): string | undefined {
  const value = source[key]
  return typeof value === 'string' && value !== '' ? value : undefined
}

/**
 * Фактический адрес 1c-db: `contour.json`, поверх — локальный оверлей.
 *
 * Локальный слой применяется, только пока его `desired_url` совпадает с
 * желаемым: смена адреса в `contour.json` снимает дрейф порта.
 * @param contour - содержимое `contour.json`.
 * @param local - содержимое `contour.local.json` (или `null`).
 * @returns адрес прокси тулкита.
 */
export function actualDbUrl(
  contour: Readonly<Record<string, unknown>>,
  local: Readonly<Record<string, unknown>> | null,
): string {
  const url = stringAt(objectAt(contour, '1c_db'), 'url') ?? DEFAULT_DB_URL
  if (local === null) return url
  const layer = objectAt(local, '1c_db')
  if (stringAt(layer, 'desired_url') !== url) return url
  return stringAt(layer, 'url') ?? url
}

/** Адрес станции правил обмена из `contour.json`. */
export function kdUrl(contour: Readonly<Record<string, unknown>>): string {
  return stringAt(objectAt(contour, '1c_kd'), 'url') ?? DEFAULT_KD_URL
}

/** Привязать MCP-сессию тулкита к каналу прокси. */
export function channelUrl(url: string, channel: string): string {
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}channel=${channel}`
}

/** Инстанс мультистенда: имя сервера и канал. */
export interface DbInstance {
  readonly name: string
  readonly channel: string
}

/** Разбор строки `key: value` минимального YAML. */
function yamlScalar(raw: string): string {
  const text = raw.trim()
  if (text === '') return ''
  if (text.length >= 2 && text.startsWith(text[0] ?? '') && (text[0] === "'" || text[0] === '"') && text.endsWith(text[0] ?? '')) {
    return text.slice(1, -1)
  }
  return text
}

/**
 * Разбор `db-instances.yaml` — минимальный строгий поднабор YAML.
 *
 * Повторяет `parse_instances` из `scripts/wire_config.py`: блок `instances:`,
 * записи с отступом, скаляры. Имя или канал вне допустимого набора — ошибка:
 * сервер вне matcher гейта был бы тихим обходом прод-блокировки.
 * @param text - содержимое файла.
 * @returns записи в порядке объявления.
 * @throws Error с путём и строкой, когда файл не разобран или имя негодно.
 */
export function parseDbInstances(text: string): DbInstance[] {
  const instances: { name: string; channel: string }[] = []
  let inBlock = false
  let current: { name: string; channel: string } | null = null
  for (const [index, line] of text.split('\n').entries()) {
    if (line.includes('\t')) throw new Error(`строка ${index + 1}: табуляция в YAML — отбивай пробелами`)
    if (line.trim() === '' || line.trimStart().startsWith('#')) continue
    const indent = line.length - line.trimStart().length
    const content = line.trim()
    if (indent === 0) {
      if (content === 'instances:') inBlock = true
      else if (inBlock) break
      continue
    }
    if (!inBlock) continue
    if (indent <= 2 && content.endsWith(':')) {
      current = { name: content.slice(0, -1).trim(), channel: '' }
      instances.push(current)
      continue
    }
    if (current !== null && content.includes(':')) {
      const [key = '', ...rest] = content.split(':')
      if (key.trim() === 'channel') current.channel = yamlScalar(rest.join(':'))
    }
  }
  return instances.map((instance) => {
    if (!INSTANCE_NAME_RE.test(instance.name)) {
      throw new Error(
        `имя инстанса ${JSON.stringify(instance.name)} вне [a-z0-9_-]{1,64} — `
        + 'сервер 1c-db-<имя> не попал бы в matcher mcp-гейта',
      )
    }
    const channel = instance.channel === '' ? instance.name : instance.channel
    if (!CHANNEL_RE.test(channel)) {
      throw new Error(`канал ${JSON.stringify(channel)} инстанса ${instance.name} вне [A-Za-z0-9_-]{1,64}`)
    }
    return { name: instance.name, channel }
  })
}

/** Что прочитано из проекта: конфиг, оверлей и инстансы. */
export interface ProjectReading {
  /** Каталог, из которого прочитаны входы: `mcpRows` берёт из него профили. */
  readonly dataDir: string
  readonly contour: Readonly<Record<string, unknown>>
  readonly local: Readonly<Record<string, unknown>> | null
  /** Инстансы мультистенда; пусто в моно-режиме. */
  readonly instances: readonly DbInstance[]
  /** Причины, по которым файл пропущен (для журнала и doctor). */
  readonly problems: readonly string[]
}

/**
 * Прочитать проектные входы через переданный доступ к файлам.
 *
 * Доступ подменяем, чтобы тесты не трогали диск и видели ровно нужный набор.
 * @param dataDir - каталог проектных данных.
 * @param read - чтение файла как текста либо `null`.
 * @returns разобранные входы и список проблем.
 */
export function readProject(
  dataDir: string,
  read: (path: string) => string | null,
): ProjectReading {
  const problems: string[] = []
  const contourText = read(join(dataDir, 'contour.json'))
  const contour = readObject(contourText)
  if (contourText !== null && Object.keys(contour).length === 0) {
    problems.push('contour.json: не разобран — адреса MCP взяты по умолчанию')
  }
  const localText = read(join(dataDir, 'contour.local.json'))
  const local = localText === null ? null : readObject(localText)
  const instancesText = read(join(dataDir, 'db-instances.yaml'))
  let instances: DbInstance[] = []
  if (instancesText !== null) {
    try {
      instances = parseDbInstances(instancesText)
    } catch (error: unknown) {
      problems.push(`db-instances.yaml: ${error instanceof Error ? error.message : String(error)}`)
    }
  }
  const seen = new Set<string>()
  const unique = instances.filter((instance) => {
    if (seen.has(instance.name)) {
      problems.push(`db-instances.yaml: инстанс ${instance.name} объявлен дважды — второй пропущен`)
      return false
    }
    seen.add(instance.name)
    return true
  })
  return { dataDir, contour, local, instances: unique, problems }
}

/**
 * MCP-записи пресета для проекта.
 *
 * Состав: `unica` (stdio, cwd проекта, глобальный кэш рантаймов),
 * `1c-testpilot` (stdio, профили проекта), `1c-db` (HTTP, адрес проекта) и
 * `1c-kd` (HTTP). Мультистенд добавляет `1c-db-<имя>` с `?channel=…`;
 * запись `1c-db` остаётся за main, дубля `1c-db-main` нет.
 * @param project - найденный проект контура.
 * @param reading - прочитанные проектные входы.
 * @param env - домашний каталог и подмены команд.
 * @returns строки доставки в стабильном порядке.
 */
export function mcpRows(
  project: ContourProject,
  reading: ProjectReading,
  env: DeliveryEnv,
): McpRow[] {
  const home = env.home
  const processEnv = env.env ?? {}
  const unica = processEnv['UNICA_LAUNCHER'] ?? join(home, '.local', 'bin', 'unica')
  const testpilot = processEnv['DSH_1C_TESTPILOT'] ?? '1c-testpilot'
  const db = actualDbUrl(reading.contour, reading.local)
  const kd = kdUrl(reading.contour)
  const profiles = join(reading.dataDir, 'testpilot', 'profiles.yaml')

  const rows: McpRow[] = [
    {
      id: 'mcp-unica',
      config: {
        serverName: 'unica',
        transport: 'stdio',
        // Юника отдаёт ответ только в `structuredContent`; клиент MCP DSH
        // проецирует его в текст модели начиная с 0.2.2-rc.2. Для стокового
        // 0.2.2-rc.1 есть фолбэк `scripts/unica_mcp_proxy.py` (docs/UNICA.md).
        command: unica,
        args: ['mcp'],
        cwd: project.root,
        env: {
          UNICA_RUNTIME_CACHE_DIR: unicaRuntimeCache(home),
        },
        startupTimeoutMs: 30_000,
      },
    },
    {
      id: 'mcp-1c-testpilot',
      config: {
        serverName: '1c-testpilot',
        transport: 'stdio',
        command: testpilot,
        cwd: project.root,
        env: {
          TC1C_TRANSPORT: 'stdio',
          TC1C_PROFILES_FILE: profiles,
          TC1C_CODE_EXECUTION: 'false',
          TC1C_QUERY_EXECUTION: 'false',
          TC1C_METADATA: 'false',
          TC1C_FUNCTIONS: 'false',
        },
        startupTimeoutMs: 15_000,
      },
    },
    { id: 'mcp-1c-db', config: { serverName: '1c-db', transport: 'streamable-http', url: db, startupTimeoutMs: 15_000 } },
    { id: 'mcp-1c-kd', config: { serverName: '1c-kd', transport: 'streamable-http', url: kd, startupTimeoutMs: 15_000 } },
  ]
  for (const instance of reading.instances) {
    rows.push({
      id: `mcp-1c-db-${instance.name}`,
      config: {
        serverName: `1c-db-${instance.name}`,
        transport: 'streamable-http',
        url: channelUrl(db, instance.channel),
        startupTimeoutMs: 15_000,
      },
    })
  }
  return rows
}

/**
 * Каталоги скиллов пресета.
 *
 * Контурные скиллы и четыре команды живут в пакете контура, скиллы Юники —
 * в глобальной установке на машине. Порядок задаёт приоритет имён: контур
 * перекрывает одноимённый скилл Юники.
 * @param packageRoot - корень пакета контура.
 * @param home - домашний каталог машины.
 * @returns абсолютные корни для провайдера скиллов.
 */
export function skillDirs(packageRoot: string, home: string): string[] {
  return [join(packageRoot, 'skills'), join(packageRoot, 'commands'), unicaSkillsDir(home)]
}

/** Домашний каталог по умолчанию (выделено для тестов). */
export function defaultHome(): string {
  return homedir()
}