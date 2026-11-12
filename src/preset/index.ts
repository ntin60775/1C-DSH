/**
 * Пресет `1c` в доставке: скиллы, команды и MCP только в 1С-сессии.
 *
 * Плагин монтируется строкой пресета (`preset-1c`), поэтому действует только
 * на сессии этого пресета. На старте агента он резолвит проект от каталога
 * сессии и, если это проект 1С:
 *
 * - поднимает провайдер скиллов в области агента — 15 контурных скиллов,
 *   4 команды (`/1c-*`) и скиллы Юники с машины; вне проекта 1С их нет;
 * - монтирует MCP-серверы проекта (`unica`, `1c-testpilot`, `1c-db` с
 *   инстансами, `1c-kd`) в области агента: `unica` получает cwd проекта и
 *   глобальный кэш рантаймов, адреса тулкита — из `.dsh/1c`;
 * - публикует `DSH_1C_ROOT` — путь к пакету контура для скриптов команд.
 *
 * Готовые реализации берутся из установки DSH через `ctx.loader.import`:
 * своя копия `@deepseek-ai/dsh-mcp-client` дала бы второй экземпляр
 * `dsh-scope` и потеряла область агента.
 * @module dsh-1c-contour/preset
 */

import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'

import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'

import type { ContourProject } from '../contract.js'
import { resolveProject } from '../paths.js'
import {
  dataDirOf,
  mcpRows,
  PRESET_ID,
  readProject,
  skillDirs,
  type DeliveryEnv,
  type McpRow,
} from './config.js'

/** Имя плагина в профиле: строка пресета `preset-1c`. */
export const name = 'dsh-1c-contour/preset'

/** Минимальные формы сервисов, которые нужны доставке. */
interface Services {
  readonly loader: { import: (specifier: string) => Promise<unknown> } | undefined
  readonly shellEnv: {
    register: (contributor: {
      readonly name: string
      readonly variables: Record<string, { readonly description: string }>
      readonly resolve: (execution: unknown) => Record<string, string>
    }) => void
  } | undefined
  readonly workingDirectory: { ensure: (agent: Agent) => Promise<string> } | undefined
  readonly agentPresets: { presetForSession: (session: Agent['session']) => string | null } | undefined
}

/** Область агента умеет монтировать дочерние плагины и переживает их до своей уборки. */
interface AgentScope {
  plugin: (plugin: unknown, config?: unknown) => Promise<unknown> | unknown
}

/** Корень пакета контура: из `lib/preset/` — на два уровня вверх. */
export function packageRoot(): string {
  return fileURLToPath(new URL('../../', import.meta.url))
}

/** Чтение файла как текста; отсутствующий файл — `null`. */
function readText(path: string): string | null {
  try {
    return readFileSync(path, 'utf8')
  } catch {
    return null
  }
}

/**
 * Пресет, под которым реально идёт сессия.
 *
 * Проекция реестра пресетов перекрывает заголовок сессии: пресет могли
 * выбрать в пустой сессии после создания.
 * @param agent - агент сессии.
 * @param services - реестр пресетов, если он есть.
 * @returns идентификатор пресета либо `null`.
 */
export function presetOf(agent: Agent, services: Services): string | null {
  const current = services.agentPresets?.presetForSession(agent.session)
  if (current !== undefined && current !== null && current !== '') return current
  const header = (agent.session as unknown as { header?: { agentPreset?: string } }).header
  return header?.agentPreset ?? null
}

/** Доставка одному агенту: провайдер скиллов и MCP-серверы проекта. */
export interface Delivery {
  /** Проект 1С, к которому привязана доставка. */
  readonly project: ContourProject
  /** Корни скиллов: пакет контура и глобальная установка Юники. */
  readonly skills: readonly string[]
  /** MCP-записи проекта. */
  readonly mcp: readonly McpRow[]
  /** Проблемы чтения проектных входов. */
  readonly problems: readonly string[]
}

/**
 * Собрать доставку для проекта: чистая логика без монтирования.
 * @param project - найденный проект контура.
 * @param options - корень пакета, домашний каталог и окружение.
 * @returns каталоги скиллов, MCP-записи и проблемы чтения.
 */
export function planDelivery(
  project: ContourProject,
  options: { readonly root: string; readonly home: string; readonly env?: NodeJS.ProcessEnv },
): Delivery {
  const dataDir = dataDirOf(project)
  const reading = readProject(dataDir, readText)
  const deliveryEnv: DeliveryEnv = options.env === undefined
    ? { packageRoot: options.root, home: options.home }
    : { packageRoot: options.root, home: options.home, env: options.env }
  return {
    project,
    skills: skillDirs(options.root, options.home),
    mcp: mcpRows(project, reading, deliveryEnv),
    problems: reading.problems,
  }
}

/** Монтировать дочерний плагин в области агента, не роняя старт сессии. */
async function mount(
  ctx: Context,
  agent: Agent,
  specifier: string,
  config: unknown,
  loader: Services['loader'],
): Promise<void> {
  if (loader === undefined) {
    ctx.logger.warn(`dsh-1c-contour: ${specifier} не смонтирован — у хоста нет загрузчика плагинов`)
    return
  }
  try {
    const plugin = await loader.import(specifier)
    await (agent.ctx as unknown as AgentScope).plugin(plugin, config)
  } catch (error: unknown) {
    ctx.logger.warn(`dsh-1c-contour: ${specifier} не смонтирован: ${String(error)}`)
  }
}

/** Подключить доставку пресета к хосту. */
export function apply(ctx: Context): void {
  const root = packageRoot()
  // Службы читаются через `ctx.get`: у пресета их может не быть (SDK, тесты),
  // а Cordis запрещает трогать незаявленную службу как свойство.
  const service = (key: keyof Services): Services[keyof Services] =>
    (ctx.get(key) as Services[keyof Services])
  const services: Services = {
    get loader() { return service('loader') as Services['loader'] },
    get shellEnv() { return service('shellEnv') as Services['shellEnv'] },
    get workingDirectory() { return service('workingDirectory') as Services['workingDirectory'] },
    get agentPresets() { return service('agentPresets') as Services['agentPresets'] },
  }
  const shellEnv = services.shellEnv
  if (shellEnv !== undefined) {
    try {
      shellEnv.register({
        name: 'dsh-1c-contour',
        variables: { DSH_1C_ROOT: { description: 'Корень пакета контура 1С: скрипты и скиллы.' } },
        resolve: () => ({ DSH_1C_ROOT: root }),
      })
    } catch (error: unknown) {
      ctx.logger.warn(`dsh-1c-contour: DSH_1C_ROOT не объявлен: ${String(error)}`)
    }
  }

  /** Доставка ровно один раз на агента: пресет могли выбрать в пустой сессии. */
  const delivered = new WeakSet<Agent>()
  const pending = new WeakMap<Agent, Promise<void>>()
  const ensure = (agent: Agent): Promise<void> => {
    if (delivered.has(agent)) return Promise.resolve()
    const running = pending.get(agent)
    if (running !== undefined) return running
    const task = deliver(agent).finally(() => { pending.delete(agent) })
    pending.set(agent, task)
    return task
  }
  const deliver = async (agent: Agent): Promise<void> => {
    if (delivered.has(agent)) return
    const preset = presetOf(agent, services)
    if (preset !== PRESET_ID) return
    const directory = services.workingDirectory
    if (directory === undefined) return
    let cwd: string
    try {
      cwd = await directory.ensure(agent)
    } catch (error: unknown) {
      ctx.logger.warn(`dsh-1c-contour: каталог сессии недоступен: ${String(error)}`)
      return
    }
    const project = resolveProject(cwd)
    if (project === null) return
    delivered.add(agent)
    const delivery = planDelivery(project, { root, home: homedir(), env: process.env })
    for (const problem of delivery.problems) ctx.logger.warn(`dsh-1c-contour: ${problem}`)
    await mount(ctx, agent, '@deepseek-ai/dsh-skill-filesystem', {
      providerName: '1c-contour',
      includeDefaultRoots: false,
      customSkillDirs: [...delivery.skills],
      watch: true,
    }, services.loader)
    for (const row of delivery.mcp) {
      await mount(ctx, agent, '@deepseek-ai/dsh-mcp-client', { ...row.config, failOnStartupError: false }, services.loader)
    }
  }

  const agents = new Map<string, Agent>()
  // Событие создания агента идёт в области агента, а пресет живёт своей
  // областью: без `global` подписка не увидит ни одной сессии.
  ctx.on('agent/created', ({ agent }) => {
    agents.set(agent.id, agent)
    // Создание агента не ждёт MCP: ожидание — на первом шаге (pre-step),
    // иначе недоступная станция держит старт сессии десятки секунд.
    void ensure(agent).catch(() => undefined)
    return undefined
  }, { global: true })
  ctx.on('agent/disposed', ({ agent }) => { agents.delete(agent.id) }, { global: true })
  // Пресет, выбранный в пустой сессии, агента не создаёт: сессия уже была
  // под пресетом по умолчанию, а выбор лишь пересобирает композицию. Реестр
  // объявляет выбор host-событием — по нему доставка и догоняет, до первого
  // запроса модели, иначе каталог инструментов успевает собраться без MCP.
  interface PresetSelection {
    on: (name: string, listener: (id: string, preset: string) => void, options: { global: boolean }) => void
  }
  ;(ctx as unknown as PresetSelection).on('agent-preset/selected', (sessionId, preset) => {
    if (preset !== PRESET_ID) return
    const agent = agents.get(sessionId)
    if (agent !== undefined) void ensure(agent)
  }, { global: true })
  // Пресет, выбранный в пустой сессии, не создаёт агента заново: доставка
  // догоняет на первом шаге. `prepend` ставит её раньше публикатора каталога
  // скиллов, иначе первый запрос уйдёт без контурных скиллов и MCP.
  ctx.on('agent/pre-step', async ({ agent }, next) => {
    agents.set(agent.id, agent)
    await ensure(agent)
    return next()
  }, { global: true, prepend: true })
}