/**
 * Корпус и пресет: критерии Т5 проверяются на артефактах, не на словах.
 *
 * spec: Т5 — «15 скиллов контура, 4 команды», «`whenToUse` во всех 15
 * `SKILL.md`», «`grep -rl "\.zcode" skills commands templates` — пусто»,
 * «поставка скиллов в пресет».
 * @module tests/unit/preset-corpus.spec
 */

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { planDelivery } from '../../src/preset/index.js'
import type { ContourProject } from '../../src/contract.js'

const ROOT = fileURLToPath(new URL('../..', import.meta.url))

/** Все файлы дерева рекурсивно. */
function files(dir: string): string[] {
  const result: string[] = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) result.push(...files(path))
    else result.push(path)
  }
  return result
}

/** Поля YAML-фронтматтера: ключ верхнего уровня важен, значение — нет. */
function frontmatter(text: string): Map<string, string> {
  const fields = new Map<string, string>()
  const lines = text.split('\n')
  if (lines[0]?.trim() !== '---') return fields
  const end = lines.slice(1).findIndex((line) => line.trim() === '---')
  if (end < 0) return fields
  for (const line of lines.slice(1, end + 1)) {
    const match = /^([A-Za-z_][\w-]*)\s*:\s*(.*)$/.exec(line)
    if (match !== null && match[1] !== undefined) fields.set(match[1], match[2] ?? '')
  }
  return fields
}

const SKILLS = readdirSync(join(ROOT, 'skills'), { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .sort()

describe('скиллы контура', () => {
  it('ровно 15 SKILL.md, name совпадает с каталогом, whenToUse есть', () => {
    expect(SKILLS).toHaveLength(15)
    for (const skill of SKILLS) {
      const text = readFileSync(join(ROOT, 'skills', skill, 'SKILL.md'), 'utf8')
      const fields = frontmatter(text)
      expect(fields.get('name'), `${skill}: name`).toBe(skill)
      expect(fields.get('description')?.trim(), `${skill}: description`).not.toBe('')
      expect(fields.has('whenToUse'), `${skill}: whenToUse`).toBe(true)
      expect(fields.has('when_to_use'), `${skill}: legacy when_to_use`).toBe(false)
    }
  })
})

describe('команды контура', () => {
  const commands = readdirSync(join(ROOT, 'commands')).filter((name) => name.endsWith('.md')).sort()

  it('ровно 4 команды /1c-*', () => {
    expect(commands).toEqual(['1c-bootstrap.md', '1c-doctor.md', '1c-migrate.md', '1c-test.md'])
  })

  it('команда — user-invocable скилл, моделью не вызывается', () => {
    for (const file of commands) {
      const fields = frontmatter(readFileSync(join(ROOT, 'commands', file), 'utf8'))
      expect(fields.get('name'), file).toBe(file.replace(/\.md$/, ''))
      expect(fields.get('disable-model-invocation'), file).toBe('true')
      expect(fields.get('description')?.trim(), file).not.toBe('')
    }
  })

  it('OMP-миграции больше нет', () => {
    expect(commands).not.toContain('1c-migrate-from-omp.md')
  })
})

describe('корпус без ZCode', () => {
  it('ни одного .zcode в skills, commands, templates', () => {
    const hits: string[] = []
    for (const dir of ['skills', 'commands', 'templates']) {
      for (const file of files(join(ROOT, dir))) {
        if (!/\.(md|py|json|yaml|yml|template|ts)$/.test(file)) continue
        if (readFileSync(file, 'utf8').includes('.zcode')) hits.push(file.slice(ROOT.length + 1))
      }
    }
    expect(hits).toEqual([])
  })

  it('ZCode-путей состояния и слотов фронтматтера нет', () => {
    const hits: string[] = []
    for (const dir of ['skills', 'commands', 'templates']) {
      for (const file of files(join(ROOT, dir))) {
        if (!/\.(md|py|json|yaml|yml|template|ts)$/.test(file)) continue
        const text = readFileSync(file, 'utf8')
        for (const legacy of ['ZCODE_1C_STATE_DIR', 'when_to_use:', '~/.zcode']) {
          if (text.includes(legacy)) hits.push(`${file.slice(ROOT.length + 1)}: ${legacy}`)
        }
      }
    }
    expect(hits).toEqual([])
  })
})

describe('пресет в поставке', () => {
  const patch = readFileSync(join(ROOT, 'cordis.patch.yml'), 'utf8')

  it('строка пресета 1c и доставка контура на месте', () => {
    expect(patch).toMatch(/id:\s*preset-1c/)
    expect(patch).toMatch(/id:\s*1c\b/)
    expect(patch).toMatch(/name:\s*'dsh-1c-contour\/preset'/)
    for (const row of ["id: skill-filesystem", 'id: tool-skill', 'id: tool-bash', 'id: tool-fs']) {
      expect(patch, row).toContain(row)
    }
  })

  it('пакет несёт скиллы, команды, скрипты и workflows', () => {
    const manifest = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8')) as {
      files: string[]
      exports: Record<string, unknown>
    }
    for (const entry of ['skills/**', 'commands/**', 'scripts/**', 'workflows/**']) {
      expect(manifest.files, entry).toContain(entry)
    }
    expect(Object.keys(manifest.exports)).toContain('./preset')
  })

  it('доставка реального пресета видит 4 MCP-сервера и три корня скиллов', () => {
    const project: ContourProject = { root: ROOT, layout: 'markers', contour: {} }
    const delivery = planDelivery(project, { root: ROOT, home: '/home/tester' })
    expect(delivery.mcp.map((row) => row.config.serverName)).toEqual(['unica', '1c-testpilot', '1c-db', '1c-kd'])
    expect(delivery.skills).toEqual([
      join(ROOT, 'skills'),
      join(ROOT, 'commands'),
      '/home/tester/.local/share/unica/skills',
    ])
    expect(statSync(join(ROOT, 'skills')).isDirectory()).toBe(true)
    expect(statSync(join(ROOT, 'commands')).isDirectory()).toBe(true)
  })
})