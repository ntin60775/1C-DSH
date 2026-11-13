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
function harness(options: { preset: string; cwd: string; config?: Record<string, unknown> }) {
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
  apply(ctx as never, options.config as never)
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

/**
 * Стековые id: доставка — конфигурируемый набор пресетов (тикет 05
 * team-profiles). Конфиг строки пресета задаёт набор; дефолт `1c` цел.
 */
describe('набор id доставки из конфига пресета', () => {
  /** Набор трёх стековых id — как в строке `contour-preset` стек-бандла. */
  const STACK_IDS = ['1c-architect', '1c-orchestrator', '1c-executor'] as const

  it('каждый стековый id получает скиллы и MCP проекта', async () => {
    const tree = projectTree()
    try {
      for (const preset of STACK_IDS) {
        const stand = harness({ preset, cwd: tree.root, config: { presetIds: [...STACK_IDS] } })
        await stand.emit('agent/created', { agent: stand.agent(`s-${preset}`) })
        await settle(() => stand.mounts.length === 5)
        expect(stand.mounts.map((mount) => mount.spec)).toEqual([
          '@deepseek-ai/dsh-skill-filesystem',
          '@deepseek-ai/dsh-mcp-client',
          '@deepseek-ai/dsh-mcp-client',
          '@deepseek-ai/dsh-mcp-client',
          '@deepseek-ai/dsh-mcp-client',
        ])
        expect(stand.mounts[0]?.config['providerName']).toBe('1c-contour')
      }
    } finally {
      tree.cleanup()
    }
  })

  it('чужой роли (team-architect) набор id доставку не даёт', async () => {
    const tree = projectTree()
    try {
      const stand = harness({ preset: 'architect', cwd: tree.root, config: { presetIds: [...STACK_IDS] } })
      await stand.emit('agent/created', { agent: stand.agent('s-team') })
      expect(stand.mounts).toEqual([])
    } finally {
      tree.cleanup()
    }
  })

  it('без конфига дефолт 1c цел', async () => {
    const tree = projectTree()
    try {
      const stand = harness({ preset: '1c', cwd: tree.root })
      await stand.emit('agent/created', { agent: stand.agent('s1c') })
      await settle(() => stand.mounts.length === 5)
      expect(stand.mounts).toHaveLength(5)
    } finally {
      tree.cleanup()
    }
  })

  it('выбор стекового пресета в пустой сессии догоняет доставку', async () => {
    const tree = projectTree()
    try {
      const stand = harness({ preset: 'standard', cwd: tree.root, config: { presetIds: [...STACK_IDS] } })
      const agent = stand.agent('s1')
      await stand.emit('agent/created', { agent })
      expect(stand.mounts).toHaveLength(0)
      ;(stand.services['agentPresets'] as { presetForSession: () => string }).presetForSession = () => '1c-executor'
      await stand.emit('agent-preset/selected', 's1', '1c-executor')
      await settle(() => stand.mounts.length === 5)
      expect(stand.mounts).toHaveLength(5)
    } finally {
      tree.cleanup()
    }
  })

  it('невалидный набор откатывается к дефолту 1c', async () => {
    const tree = projectTree()
    try {
      for (const presetIds of [[], ['']] as const) {
        const stand = harness({ preset: '1c', cwd: tree.root, config: { presetIds: [...presetIds] } })
        await stand.emit('agent/created', { agent: stand.agent('s1c') })
        await settle(() => stand.mounts.length === 5)
        expect(stand.mounts).toHaveLength(5)
      }
    } finally {
      tree.cleanup()
    }
  })
})
