/**
 * Доставка пресета `1c`: монтирование скиллов и MCP в области агента.
 *
 * spec: Т5 — «с пресетом — 15 скиллов, 4 команды, MCP; без пресета — 0/0/0;
 * вне 1С-проекта скиллы Юники и mcp__unica__* не видны»; «DSH_1C_ROOT
 * в окружении команд».
 * @module tests/unit/preset-mount.spec
 */

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { apply } from '../../src/preset/index.js'

/** Монтирование, перехваченное фальшивой областью агента. */
interface Mount {
  readonly spec: string
  readonly config: Record<string, unknown>
}

/** Стенд: фальшивые ctx/agent/services, настоящий apply. */
function harness(options: { preset: string; cwd: string }) {
  const mounts: Mount[] = []
  const registered: unknown[] = []
  const listeners = new Map<string, ((...args: never[]) => unknown)[]>()
  const services: Record<string, unknown> = {
    loader: { import: async (spec: string) => ({ spec }) },
    workingDirectory: { ensure: async () => options.cwd },
    shellEnv: { register: (contributor: unknown) => { registered.push(contributor) } },
    agentPresets: { presetForSession: () => options.preset },
  }
  const ctx = {
    on(name: string, listener: (...args: never[]) => unknown) {
      const list = listeners.get(name) ?? []
      list.push(listener)
      listeners.set(name, list)
    },
    get(key: string) { return services[key] },
    logger: { warn() {} },
  }
  apply(ctx as never)
  const emit = async (name: string, ...args: unknown[]): Promise<void> => {
    for (const listener of listeners.get(name) ?? []) {
      await (listener as (...values: unknown[]) => Promise<unknown>)(...args, async () => ({ kind: 'continue' }))
    }
  }
  const agent = (id: string) => ({
    id,
    session: { header: {} },
    ctx: {
      plugin: async (plugin: unknown, config: unknown) => {
        mounts.push({ spec: (plugin as { spec: string }).spec, config: config as Record<string, unknown> })
      },
    },
  })
  return { mounts, registered, emit, agent, services }
}

/** Дождаться асинхронной доставки, запущенной без await (событие выбора). */
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

describe('доставка пресета', () => {
  it('без пресета 1c не монтирует ничего', async () => {
    const tree = projectTree()
    try {
      const stand = harness({ preset: 'standard', cwd: tree.root })
      await stand.emit('agent/created', { agent: stand.agent('s1') })
      expect(stand.mounts).toEqual([])
      expect(stand.registered).toHaveLength(1)
    } finally {
      tree.cleanup()
    }
  })

  it('в 1С-проекте монтирует провайдер скиллов и четыре MCP-сервера', async () => {
    const tree = projectTree()
    try {
      const stand = harness({ preset: '1c', cwd: tree.root })
      await stand.emit('agent/created', { agent: stand.agent('s1') })
      await settle(() => stand.mounts.length === 5)
      expect(stand.mounts.map((mount) => mount.spec)).toEqual([
        '@deepseek-ai/dsh-skill-filesystem',
        '@deepseek-ai/dsh-mcp-client',
        '@deepseek-ai/dsh-mcp-client',
        '@deepseek-ai/dsh-mcp-client',
        '@deepseek-ai/dsh-mcp-client',
      ])
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

  it('повторное событие не дублирует доставку', async () => {
    const tree = projectTree()
    try {
      const stand = harness({ preset: '1c', cwd: tree.root })
      const agent = stand.agent('s1')
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
      const stand = harness({ preset: '1c', cwd: tree.root })
      await stand.emit('agent/created', { agent: stand.agent('s1') })
      expect(stand.mounts).toEqual([])
    } finally {
      tree.cleanup()
    }
  })

  it('выбор пресета в пустой сессии догоняет доставку до первого шага', async () => {
    const tree = projectTree()
    try {
      const stand = harness({ preset: 'standard', cwd: tree.root })
      const agent = stand.agent('s1')
      await stand.emit('agent/created', { agent })
      expect(stand.mounts).toHaveLength(0)
      ;(stand.services['agentPresets'] as { presetForSession: () => string }).presetForSession = () => '1c'
      await stand.emit('agent-preset/selected', 's1', '1c')
      await settle(() => stand.mounts.length === 5)
      expect(stand.mounts).toHaveLength(5)
    } finally {
      tree.cleanup()
    }
  })
})
