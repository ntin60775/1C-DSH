/**
 * Доставка пресета `1c`: проектные входы → каталоги скиллов и MCP-записи.
 *
 * spec: Т5 — «MCP-записи: unica, 1c-testpilot, 1c-db (+ инстансы), 1c-kd;
 * `unica` получает `UNICA_RUNTIME_CACHE_DIR` и привязку к проекту»;
 * «вне 1С-проекта скиллы Юники и `mcp__unica__*` не видны».
 * @module tests/unit/preset.spec
 */

import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import type { ContourProject } from '../../src/contract.js'
import {
  actualDbUrl,
  DEFAULT_KD_URL,
  kdUrl,
  mcpRows,
  parseDbInstances,
  PRESET_ID,
  readProject,
  skillDirs,
} from '../../src/preset/config.js'
import { planDelivery, presetOf } from '../../src/preset/index.js'

/** Временный каталог с файлами; возвращает корень и уборщик. */
function tempTree(files: Record<string, string>): { root: string; cleanup: () => void } {
  const root = mkdtempSync(join(tmpdir(), 'dsh-1c-preset-'))
  for (const [name, text] of Object.entries(files)) {
    const path = join(root, name)
    mkdirSync(join(path, '..'), { recursive: true })
    writeFileSync(path, text)
  }
  return { root, cleanup: () => rmSync(root, { recursive: true, force: true }) }
}

/** Проект на новой раскладке без чтения с диска. */
function project(root: string): ContourProject {
  return { root, layout: 'dsh', contour: {} }
}

describe('parseDbInstances', () => {
  it('читает инстансы и подставляет имя каналом', () => {
    const text = [
      'instances:',
      '  base2:',
      '    connection: File=build/ib/base2',
      '    channel: second',
      '  base3:',
      '    connection: File=build/ib/base3',
    ].join('\n')
    expect(parseDbInstances(text)).toEqual([
      { name: 'base2', channel: 'second' },
      { name: 'base3', channel: 'base3' },
    ])
  })

  it('отбивает имя и канал вне набора matcher’а гейта', () => {
    expect(() => parseDbInstances('instances:\n  Base2:\n    channel: x\n')).toThrow(/вне \[a-z0-9_-\]/)
    expect(() => parseDbInstances('instances:\n  base2:\n    channel: "a b"\n')).toThrow(/канал/)
  })

  it('отбивает табуляцию: YAML не разобран бы молча', () => {
    expect(() => parseDbInstances('instances:\n\tbase2:\n')).toThrow(/табуляция/)
  })

  it('моно-режим: блок пуст или отсутствует', () => {
    expect(parseDbInstances('')).toEqual([])
    expect(parseDbInstances('# без блока\nother: 1\n')).toEqual([])
  })
})

describe('actualDbUrl и kdUrl', () => {
  it('умолчания совпадают с прокси тулкита и станцией КД', () => {
    expect(actualDbUrl({}, null)).toBe('http://127.0.0.1:6003/mcp')
    expect(kdUrl({})).toBe('http://127.0.0.1:8060/mcp')
  })

  it('локальный оверлей применяется только при совпавшем desired_url', () => {
    const contour = { '1c_db': { url: 'http://127.0.0.1:6100/mcp' } }
    expect(actualDbUrl(contour, { '1c_db': { url: 'http://127.0.0.1:6101/mcp', desired_url: 'http://127.0.0.1:6100/mcp' } }))
      .toBe('http://127.0.0.1:6101/mcp')
    expect(actualDbUrl(contour, { '1c_db': { url: 'http://127.0.0.1:6101/mcp', desired_url: 'http://127.0.0.1:6200/mcp' } }))
      .toBe('http://127.0.0.1:6100/mcp')
  })

  it('адрес станции КД берётся из contour.json', () => {
    expect(kdUrl({ '1c_kd': { url: 'http://127.0.0.1:9060/mcp' } })).toBe('http://127.0.0.1:9060/mcp')
  })
})

describe('readProject и mcpRows', () => {
  it('собирает четыре базовые записи и инстанс мультистенда', () => {
    const tree = tempTree({
      '.dsh/1c/contour.json': JSON.stringify({ '1c_db': { url: 'http://127.0.0.1:6100/mcp' }, '1c_kd': { url: 'http://127.0.0.1:9060/mcp' } }),
      '.dsh/1c/contour.local.json': JSON.stringify({ '1c_db': { url: 'http://127.0.0.1:6101/mcp', desired_url: 'http://127.0.0.1:6100/mcp' } }),
      '.dsh/1c/db-instances.yaml': 'instances:\n  base2:\n    channel: second\n',
    })
    try {
      const reading = readProject(join(tree.root, '.dsh', '1c'), (path) => readFileSync(path, 'utf8'))
      expect(reading.problems).toEqual([])
      const rows = mcpRows(project(tree.root), reading, { packageRoot: '/pkg', home: '/home/tester' })
      expect(rows.map((row) => row.config.serverName)).toEqual(['unica', '1c-testpilot', '1c-db', '1c-kd', '1c-db-base2'])
      expect(rows[0]?.config).toMatchObject({
        transport: 'stdio',
        cwd: tree.root,
        command: 'python3',
        args: ['/pkg/scripts/unica_mcp_proxy.py'],
        env: {
          UNICA_RUNTIME_CACHE_DIR: '/home/tester/.local/share/unica/runtimes',
          UNICA_LAUNCHER: '/home/tester/.local/bin/unica',
        },
      })
      expect(rows[1]?.config.env?.['TC1C_PROFILES_FILE']).toBe(join(tree.root, '.dsh', '1c', 'testpilot', 'profiles.yaml'))
      expect(rows[2]?.config.url).toBe('http://127.0.0.1:6101/mcp')
      expect(rows[3]?.config.url).toBe('http://127.0.0.1:9060/mcp')
      expect(rows[4]?.config.url).toBe('http://127.0.0.1:6101/mcp?channel=second')
    } finally {
      tree.cleanup()
    }
  })

  it('битый db-instances не роняет доставку, проблема названа', () => {
    const tree = tempTree({ '.dsh/1c/db-instances.yaml': 'instances:\n  Bad Name:\n' })
    try {
      const reading = readProject(join(tree.root, '.dsh', '1c'), (path) => {
        try {
          return readFileSync(path, 'utf8')
        } catch {
          return null
        }
      })
      expect(reading.instances).toEqual([])
      expect(reading.problems[0]).toMatch(/db-instances\.yaml/)
    } finally {
      tree.cleanup()
    }
  })
})

describe('раскладка и скиллы', () => {
  it('корни скиллов: пакет контура, команды, глобальные скиллы Юники', () => {
    expect(skillDirs('/pkg', '/home/tester')).toEqual([
      '/pkg/skills',
      '/pkg/commands',
      '/home/tester/.local/share/unica/skills',
    ])
  })

  it('planDelivery видит legacy-раскладку, пока проект не переехал', () => {
    const tree = tempTree({ '.zcode/1c/contour.json': JSON.stringify({ '1c_db': { url: 'http://127.0.0.1:6100/mcp' } }) })
    try {
      const legacy: ContourProject = { root: tree.root, layout: 'zcode', contour: {} }
      const delivery = planDelivery(legacy, { root: '/pkg', home: '/home/tester' })
      expect(delivery.mcp[0]?.config.serverName).toBe('unica')
      expect(delivery.mcp[2]?.config.url).toBe('http://127.0.0.1:6100/mcp')
      expect(delivery.problems).toEqual([])
    } finally {
      tree.cleanup()
    }
  })
})

describe('presetOf', () => {
  const agent = (preset: string | undefined) => ({
    session: { header: preset === undefined ? {} : { agentPreset: preset } },
  }) as never

  it('читает заголовок сессии', () => {
    expect(presetOf(agent(PRESET_ID), {})).toBe('1c')
    expect(presetOf(agent('standard'), {})).toBe('standard')
  })

  it('проекция реестра перекрывает заголовок', () => {
    expect(presetOf(agent('standard'), { agentPresets: { presetForSession: () => '1c' } })).toBe('1c')
  })

  it('без пресета — null, доставки нет', () => {
    expect(presetOf(agent(undefined), {})).toBeNull()
  })
})