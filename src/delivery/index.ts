/**
 * Доставка контура 1С: скиллы, команды и MCP в сессии 1С-проекта.
 *
 * Доставку монтирует хост-плагин `dsh-1c-contour` ({@link applyDelivery}),
 * поэтому она действует поверх ЛЮБОГО пресета сессии: правило одно — каталог
 * сессии резолвится в проект 1С. Отдельного пресета контура нет, умолчание
 * профиля не требуется; вне 1С-проекта доставка не монтирует ничего.
 *
 * На старте агента и перед первым шагом:
 *
 * - поднимает провайдер скиллов в области агента — 15 контурных скиллов,
 *   3 команды (`/1c-*`), вендоренные паки проекта (`.dsh/1c/skills`) и
 *   скиллы Юники с машины;
 * - монтирует MCP-серверы проекта (`unica`, `1c-testpilot`, `1c-db` с
 *   инстансами, `1c-kd`) в области агента: `unica` получает cwd проекта и
 *   глобальный кэш рантаймов, адреса тулкита — из `.dsh/1c`;
 * - публикует `DSH_1C_ROOT` — путь к пакету контура для скриптов команд.
 *
 * Готовые реализации берутся из установки DSH через `ctx.loader.import`:
 * своя копия `@deepseek-ai/dsh-mcp-client` дала бы второй экземпляр
 * `dsh-scope` и потеряла область агента.
 * @module dsh-1c-contour/delivery
 */

import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'

import type { ContourProject } from '../contract.js'
import { resolveProject } from '../paths.js'
import {
  dataDirOf,
  mcpRows,
  readProject,
  skillDirs,
  type DeliveryEnv,
  type McpRow,
} from './config.js'

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
}

/** Область агента умеет монтировать дочерние плагины и переживает их до своей уборки. */
interface AgentScope {
  plugin: (plugin: unknown, config?: unknown) => Promise<unknown> | unknown
}

/** Корень пакета контура: из `lib/delivery/` — на два уровня вверх. */
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

/** Доставка одному агенту: провайдер скиллов и MCP-серверы проекта. */
export interface Delivery {
  /** Проект 1С, к которому привязана доставка. */
  readonly project: ContourProject
  /** Корни скиллов: пакет контура, вендоренные паки проекта и глобальная установка Юники. */
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
    skills: skillDirs(options.root, options.home, join(dataDir, 'skills')),
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

/**
 * Подключить доставку контура к хосту: аддитив к любому пресету, cwd-гейт.
 * @param ctx - контекст хост-плагина.
 */
export function applyDelivery(ctx: Context): void {
  const root = packageRoot()
  // Службы читаются через `ctx.get`: в тестах и SDK их может не быть,
  // а Cordis запрещает трогать незаявленную службу как свойство.
  const service = (key: keyof Services): Services[keyof Services] =>
    (ctx.get(key) as Services[keyof Services])
  const services: Services = {
    get loader() { return service('loader') as Services['loader'] },
    get shellEnv() { return service('shellEnv') as Services['shellEnv'] },
    get workingDirectory() { return service('workingDirectory') as Services['workingDirectory'] },
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

  /** Доставка ровно один раз на агента. */
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

  // Создание агента не ждёт MCP: ожидание — на первом шаге (pre-step),
  // иначе недоступная станция держит старт сессии десятки секунд.
  ctx.on('agent/created', ({ agent }) => {
    void ensure(agent).catch(() => undefined)
    return undefined
  }, { global: true })
  // `prepend` ставит доставку раньше публикатора каталога скиллов, иначе
  // первый запрос уйдёт без контурных скиллов и MCP.
  ctx.on('agent/pre-step', async ({ agent }, next) => {
    await ensure(agent)
    return next()
  }, { global: true, prepend: true })
}