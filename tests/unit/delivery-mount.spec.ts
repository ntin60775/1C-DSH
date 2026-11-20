/**
 * Доставка контура хост-плагином: монтирование скиллов и MCP в области агента.
 *
 * Правило одно: каталог сессии резолвится в проект 1С → доставка поверх
 * ЛЮБОГО пресета (standard, ролевой, без пресета). Вне 1С-проекта — ничего.
 * Отдельного пресета контура нет; выбор пресета доставку не включает и не
 * выключает.
 * @module tests/unit/delivery-mount.spec
 */

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { applyDelivery } from '../../src/delivery/index.js'

/** Монтирование, перехваченное фальшивой областью агента. */
interface Mount {
  readonly spec: string
  readonly config: Record<string, unknown>
}

/** Стенд: фальшивые ctx/agent/services, настоящий applyDelivery. */
function harness(options: { cwd: string; services?: Record<string, unknown> }) {
  const mounts: Mount[] = []
  const registered: unknown[] = []
  const listeners = new Map<string, ((...args: never[]) => unknown)[]>()
  const services: Record<string, unknown> = {
    loader: { import: async (spec: string) => ({ spec }) },
    workingDirectory: { ensure: async () => options.cwd },
    shellEnv: { register: (contributor: unknown) => { registered.push(contributor) } },
    ...options.services,
  }
  const injections: ((inner: unknown) => void)[] = []
  const ctx = {
    on(name: string, listener: (...args: never[]) => unknown) {
      const list = listeners.get(name) ?? []
      list.push(listener)
      listeners.set(name, list)
    },
    get(key: string) { return services[key] },
    inject(deps: string[], callback: (inner: unknown) => void) {
      if (deps.every((dep) => services[dep] !== undefined)) callback(ctx)
      else injections.push(callback)
    },
    logger: { warn() {} },
  }
  applyDelivery(ctx as never)
  const fireInjections = () => { for (const callback of injections) callback(ctx) }
  const emit = async (name: string, ...args: unknown[]): Promise<void> => {
    for (const listener of listeners.get(name) ?? []) {
      await (listener as (...values: unknown[]) => Promise<unknown>)(...args, async () => ({ kind: 'continue' }))
    }
  }
  const agent = (id: string, agentPreset?: string) => ({
    id,
    session: { header: agentPreset === undefined ? {} : { agentPreset } },
    ctx: {
      plugin: async (plugin: unknown, config: unknown) => {
        mounts.push({ spec: (plugin as { spec: string }).spec, config: config as Record<string, unknown> })
      },
    },
  })
  return { mounts, registered, emit, agent, services, fireInjections }
}

/** Дождаться асинхронной доставки, запущенной без await (событие создания). */
async function settle(done: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (done()) return
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
}

/** Каталог 1С-проекта с маркером. */
function projectTree(): { root: string; cleanup: () => void } {
  const root = mkdtempSync(join(tmpdir(), 'dsh-1c-mount-'))
  writeFileSync(join(root, 'v8project.yaml'), 'workPath: src\nsource-set:\n  - name: main\n    type: CONFIGURATION\n    path: src/cf\n')
  mkdirSync(join(root, 'src', 'cf'), { recursive: true })
  return { root, cleanup: () => rmSync(root, { recursive: true, force: true }) }
}

/** Каталог вне 1С-проекта. */
function bareTree(): { root: string; cleanup: () => void } {
  const root = mkdtempSync(join(tmpdir(), 'dsh-1c-bare-'))
  return { root, cleanup: () => rmSync(root, { recursive: true, force: true }) }
}

/** Состав монтирования: провайдер скиллов и четыре MCP-сервера. */
const DELIVERY_SPECS = [
  '@deepseek-ai/dsh-skill-filesystem',
  '@deepseek-ai/dsh-mcp-client',
  '@deepseek-ai/dsh-mcp-client',
  '@deepseek-ai/dsh-mcp-client',
  '@deepseek-ai/dsh-mcp-client',
]

describe('доставка контура', () => {
  it('в 1С-проекте монтирует провайдер скиллов и четыре MCP-сервера', async () => {
    const tree = projectTree()
    try {
      const stand = harness({ cwd: tree.root })
      await stand.emit('agent/created', { agent: stand.agent('s1', 'standard') })
      await settle(() => stand.mounts.length === 5)
      expect(stand.mounts.map((mount) => mount.spec)).toEqual(DELIVERY_SPECS)
      const skills = stand.mounts[0]?.config
      expect(skills?.['includeDefaultRoots']).toBe(false)
      expect(skills?.['providerName']).toBe('1c-contour')
      expect((skills?.['customSkillDirs'] as string[])[0]).toMatch(/\/skills$/)
      expect(stand.mounts[1]?.config['serverName']).toBe('unica')
      expect(stand.mounts[1]?.config['cwd']).toBe(tree.root)
      expect(stand.mounts[2]?.config['serverName']).toBe('1c-testpilot')
      expect(stand.registered).toHaveLength(1)
    } finally {
      tree.cleanup()
    }
  })

  it('пресет не важен: standard, ролевой и отсутствие пресета дают доставку', async () => {
    const tree = projectTree()
    try {
      for (const [index, preset] of ['standard', '1c-executor', undefined].entries()) {
        const stand = harness({ cwd: tree.root })
        await stand.emit('agent/created', { agent: stand.agent(`s${index}`, preset) })
        await settle(() => stand.mounts.length === 5)
        expect(stand.mounts.map((mount) => mount.spec)).toEqual(DELIVERY_SPECS)
      }
    } finally {
      tree.cleanup()
    }
  })

  it('повторное событие не дублирует доставку', async () => {
    const tree = projectTree()
    try {
      const stand = harness({ cwd: tree.root })
      const agent = stand.agent('s1', 'standard')
      await stand.emit('agent/created', { agent })
      await stand.emit('agent/pre-step', { agent })
      await stand.emit('agent/created', { agent })
      await settle(() => stand.mounts.length === 5)
      expect(stand.mounts).toHaveLength(5)
    } finally {
      tree.cleanup()
    }
  })

  it('вне 1С-проекта не монтирует ни скиллов, ни MCP', async () => {
    const tree = bareTree()
    try {
      const stand = harness({ cwd: tree.root })
      await stand.emit('agent/created', { agent: stand.agent('s1', 'standard') })
      await stand.emit('agent/pre-step', { agent: stand.agent('s1', 'standard') })
      expect(stand.mounts).toEqual([])
    } finally {
      tree.cleanup()
    }
  })

  it('pre-step догоняет доставку до первого шага', async () => {
    const tree = projectTree()
    try {
      const stand = harness({ cwd: tree.root })
      await stand.emit('agent/pre-step', { agent: stand.agent('s1', 'standard') })
      expect(stand.mounts).toHaveLength(5)
    } finally {
      tree.cleanup()
    }
  })

  it('DSH_1C_ROOT регистрируется, когда shellEnv поднимается позже', async () => {
    const tree = projectTree()
    try {
      const stand = harness({ cwd: tree.root, services: { shellEnv: undefined } })
      expect(stand.registered).toHaveLength(0)
      stand.services['shellEnv'] = { register: (contributor: unknown) => { stand.registered.push(contributor) } }
      stand.fireInjections()
      expect(stand.registered).toHaveLength(1)
    } finally {
      tree.cleanup()
    }
  })

  it('без сервиса каталога доставка молча не монтируется', async () => {
    const tree = projectTree()
    try {
      const stand = harness({ cwd: tree.root, services: { workingDirectory: undefined } })
      await stand.emit('agent/created', { agent: stand.agent('s1', 'standard') })
      expect(stand.mounts).toEqual([])
    } finally {
      tree.cleanup()
    }
  })
})