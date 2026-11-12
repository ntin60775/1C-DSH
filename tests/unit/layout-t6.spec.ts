/**
 * Т6: канон раскладки `.dsh/1c`, legacy-ветки и состояние контура.
 *
 * Здесь то, что доказывает пункты тикета: все входы гейтов читаются из
 * `.dsh/1c` первыми, legacy `.zcode/1c` и `.zcode` остаются ветками чтения,
 * проект на любой из двух раскладок получает один и тот же вердикт, а
 * корень состояния переопределяется `DSH_1C_STATE_DIR` с legacy
 * `ZCODE_1C_STATE_DIR`.
 *
 * invariant: канон `.dsh/1c` перекрывает `.dsh`, legacy `.zcode/1c` и `.zcode`.
 * @module tests/unit/layout-t6.spec
 */

import { globSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { dirname, join } from 'node:path'

import { describe, expect, it } from 'vitest'

import type { ContourProject, GateCall } from '../../src/contract.js'
import { contourPaths, escalationEntries } from '../../src/contour.js'
import { escalationSkips } from '../../src/gates/bsl-module.js'
import { contourOf, profileConnection } from '../../src/gates/mcp.js'
import { evaluatePre as mcpPre } from '../../src/gates/mcp.js'
import { LAYOUT_DIRS, findLayoutFile, layoutCandidates, resolveProject } from '../../src/paths.js'
import { createGateState, defaultStateDir } from '../../src/state.js'
import { createStyleChecker } from '../../src/style-checker.js'
import { createFixture } from '../helpers/fixture.js'

/** Корень репозитория: тест не зависит от каталога запуска. */
const REPO_ROOT = new URL('../..', import.meta.url).pathname

/** Состояние гейта на фикстуре. */
function stateOf(fixture: { stateDir: string }) {
  const store = new Map<string, unknown>()
  return createGateState({
    sessionId: 's1',
    stateDir: fixture.stateDir,
    memory: {
      get: <T>(key: string): T | undefined => store.get(key) as T | undefined,
      set: (key: string, value: unknown): void => {
        store.set(key, value)
      },
    },
  })
}

/** Записать файл, создав каталоги. */
function put(root: string, rel: string, text: string): void {
  const path = join(root, rel)
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, text, 'utf8')
}

/** Проект-заглушка без чтения диска. */
function projectOf(root: string, layout: 'dsh' | 'zcode' | 'markers' = 'markers'): ContourProject {
  return { root, layout, contour: {} }
}

describe('таблица раскладок', () => {
  it('канон первым, затем промежуточная и legacy', () => {
    expect(LAYOUT_DIRS).toEqual(['.dsh/1c', '.dsh', '.zcode/1c', '.zcode'])
    expect(layoutCandidates('/p', 'contour')).toEqual([
      '/p/.dsh/1c/contour',
      '/p/.dsh/contour',
      '/p/.zcode/1c/contour',
      '/p/.zcode/contour',
    ])
  })

  it('первый существующий файл побеждает, подпись называет раскладку', () => {
    const found = findLayoutFile(
      '/p',
      'contour',
      (path) => (path === '/p/.zcode/1c/contour' ? 'test\n' : null),
    )
    expect(found).toMatchObject({ text: 'test\n', label: '.zcode/1c/contour' })
  })

  it('файлов нет — null, а не выдуманный путь', () => {
    expect(findLayoutFile('/p', 'contour', () => null)).toBeNull()
  })
})

describe('признак контура: канон перекрывает обе legacy-раскладки', () => {
  it('.dsh/1c/contour перекрывает .dsh, .zcode/1c и .zcode', () => {
    const fixture = createFixture({
      write: {
        'tree/.zcode/contour': 'prod\n',
        'tree/.zcode/1c/contour': 'prod\n',
        'tree/.dsh/contour': 'prod\n',
        'tree/.dsh/1c/contour': 'test\n',
      },
    })
    try {
      expect(contourOf(resolveProject(fixture.tree))).toBe('test')
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('проект только с .zcode/1c/contour читается как test', () => {
    const fixture = createFixture({ write: { 'tree/.zcode/1c/contour': 'test\n' } })
    try {
      expect(contourOf(resolveProject(fixture.tree))).toBe('test')
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })
})

describe('входы гейтов: канон .dsh/1c', () => {
  it('allowlist в .dsh/1c решает по себе и не читает legacy при каноне', () => {
    // Только опасные инструменты: у остальных решение `allow` наступает
    // раньше allowlist'а, и тест доказывал бы пустоту.
    const fixture = createFixture({
      write: {
        'tree/.zcode/1c/mcp-write-allow.txt': 'execute_code\n',
        'tree/.dsh/1c/mcp-write-allow.txt': 'restart_1c_session\n',
      },
    })
    try {
      const call = (tool: string): GateCall => ({ tool, args: { query: 'x' }, cwd: fixture.tree })
      const project = resolveProject(fixture.tree)
      expect(project).not.toBeNull()
      // Канон разрешает свой инструмент…
      expect(mcpPre(call('mcp__1c-db__restart_1c_session'), project, stateOf(fixture)))
        .toMatchObject({ kind: 'allow' })
      // …и не подхватывает разрешение из legacy: файл канона перекрывает legacy целиком.
      expect(mcpPre(call('mcp__1c-db__execute_code'), project, stateOf(fixture)))
        .toMatchObject({ kind: 'deny', code: 'mcp-unsafe' })
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('без канона allowlist читается из legacy .zcode/1c', () => {
    const fixture = createFixture({
      write: { 'tree/.zcode/1c/mcp-write-allow.txt': 'execute_code\n' },
    })
    try {
      const call: GateCall = { tool: 'mcp__1c-db__execute_code', args: {}, cwd: fixture.tree }
      expect(mcpPre(call, resolveProject(fixture.tree), stateOf(fixture))).toMatchObject({ kind: 'allow' })
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('сообщение отказа указывает канонический allowlist', () => {
    const fixture = createFixture({})
    try {
      const call: GateCall = { tool: 'mcp__1c-db__execute_code', args: {}, cwd: fixture.tree }
      const decision = mcpPre(call, resolveProject(fixture.tree), stateOf(fixture))
      expect(decision).toMatchObject({ kind: 'deny' })
      expect(decision.kind === 'deny' ? decision.reason : '').toContain('.dsh/1c/mcp-write-allow.txt')
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('путь эскалаций в сообщениях гейта — канонический', () => {
    const project = projectOf('/p')
    expect(contourPaths(project)).toMatchObject({
      layoutDir: '.dsh/1c',
      escalationFile: '.dsh/1c/unica-gate-escalations.txt',
      initWorktreeScript: '.dsh/1c/scripts/init_worktree.sh',
    })
    expect(contourPaths(projectOf('/p', 'zcode'))).toMatchObject({
      layoutDir: '.zcode/1c',
      escalationFile: '.zcode/1c/unica-gate-escalations.txt',
    })
  })

  it('эскалации Unica: канон .dsh/1c перекрывает legacy .zcode', () => {
    const fixture = createFixture({})
    try {
      const canonPath = 'src/cf/CommonModules/ОбщегоНазначения.bsl'
      const legacyPath = 'src/cfe/Расширение/Общий.bsl'
      put(fixture.tree, '.zcode/unica-gate-escalations.txt', `${legacyPath}\n`)
      expect(escalationEntries(fixture.tree, fixture.tree)).toMatchObject({
        found: true,
        matched: [legacyPath],
        label: '.zcode/unica-gate-escalations.txt',
      })
      put(fixture.tree, '.dsh/1c/unica-gate-escalations.txt', `${canonPath}\n`)
      expect(escalationEntries(fixture.tree, fixture.tree)).toMatchObject({
        found: true,
        matched: [canonPath],
        label: '.dsh/1c/unica-gate-escalations.txt',
      })
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('эскалации носителя читаются из .dsh/1c', () => {
    const fixture = createFixture({})
    try {
      put(fixture.tree, '.dsh/1c/bsl-module-gate-escalations.txt', 'directive-in-common-module:src/cf/Общий\n')
      expect(escalationSkips(projectOf(fixture.tree))).toEqual([
        ['directive-in-common-module', 'src/cf/Общий'],
      ])
      put(fixture.tree, '.zcode/1c/bsl-module-gate-escalations.txt', 'all:src\n')
      expect(escalationSkips(projectOf(fixture.tree))).toEqual([
        ['directive-in-common-module', 'src/cf/Общий'],
      ])
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('профили testpilot читаются из .dsh/1c/testpilot', () => {
    const fixture = createFixture({
      write: {
        'tree/.dsh/1c/testpilot/profiles.yaml': 'profiles:\n  file:\n    base: $TMP/tree/db\n',
      },
    })
    try {
      expect(profileConnection(resolveProject(fixture.tree), 'file')).toBe(`File=${fixture.tree}/db`)
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('чекер стиля: канон .dsh/1c/skills, затем legacy-адреса', async () => {
    const fixture = createFixture({})
    try {
      const stub = join(fixture.root, 'stub-python.sh')
      writeFileSync(stub, '#!/bin/sh\necho "$@"\nexit 1\n', { mode: 0o755 })
      const checker = createStyleChecker({ env: { PATH: '' }, python: stub })
      put(fixture.tree, '.zcode/skills/1c-bsl-code-style/scripts/bsl_style_check.py', 'legacy')
      expect(await checker.run([], fixture.tree)).toContain(
        join(fixture.tree, '.zcode/skills/1c-bsl-code-style/scripts/bsl_style_check.py'),
      )
      put(fixture.tree, '.dsh/1c/skills/1c-bsl-code-style/scripts/bsl_style_check.py', 'canon')
      expect(await checker.run([], fixture.tree)).toContain(
        join(fixture.tree, '.dsh/1c/skills/1c-bsl-code-style/scripts/bsl_style_check.py'),
      )
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })
})

describe('паритет раскладок: один вход — один вердикт', () => {
  it('одинаковый набор входов в .dsh/1c и .zcode/1c даёт одинаковые решения', () => {
    const inputs: Record<string, string> = {
      'contour': 'test\n',
      'mcp-write-allow.txt': 'execute_code\n',
      'bsl-module-gate-escalations.txt': 'directive-in-common-module:src/cf/Общий\n',
      'testpilot/profiles.yaml': 'profiles:\n  file:\n    base: $TMP/tree/db\n',
    }
    const run = (layout: string) => {
      const fixture = createFixture({})
      try {
        for (const [rel, text] of Object.entries(inputs)) put(fixture.tree, `${layout}/${rel}`, text)
        const project = resolveProject(fixture.tree)
        const call: GateCall = { tool: 'mcp__1c-db__execute_code', args: {}, cwd: fixture.tree }
        return {
          contour: contourOf(project),
          gate: mcpPre(call, project, stateOf(fixture)),
          skips: escalationSkips(project),
          connection: profileConnection(project, 'file'),
        }
      } finally {
        rmSync(fixture.root, { recursive: true, force: true })
      }
    }
    /** Пути фикстуры различаются только именем временного каталога и раскладкой. */
    const normalize = (value: unknown): unknown =>
      JSON.parse(
        JSON.stringify(value)
          .replaceAll(tmpdir(), 'TMPDIR')
          .replaceAll(/dsh-1c-parity-[A-Za-z0-9]+/g, 'FIXTURE')
          .replaceAll(/\/\.(?:dsh|zcode)\/1c\//g, '/LAYOUT/'),
      )
    const canon = normalize(run('.dsh/1c'))
    expect(normalize(run('.zcode/1c'))).toEqual(canon)
    expect(canon).toMatchObject({
      contour: 'test',
      gate: { kind: 'allow' },
      skips: [['directive-in-common-module', 'src/cf/Общий']],
      connection: 'File=TMPDIR/FIXTURE/tree/LAYOUT/testpilot/$TMP/tree/db',
    })
  })
})

describe('корень состояния', () => {
  it('DSH_1C_STATE_DIR переопределяет и побеждает legacy', () => {
    expect(defaultStateDir({ DSH_1C_STATE_DIR: '/dsh', ZCODE_1C_STATE_DIR: '/zcode' })).toBe('/dsh')
    expect(defaultStateDir({ DSH_1C_STATE_DIR: '/dsh' })).toBe('/dsh')
  })

  it('ZCODE_1C_STATE_DIR читается как legacy', () => {
    expect(defaultStateDir({ ZCODE_1C_STATE_DIR: '/zcode' })).toBe('/zcode')
  })

  it('без переменных — ~/.dsh/state/1c', () => {
    expect(defaultStateDir({})).toBe(join(homedir(), '.dsh', 'state', '1c'))
  })

  it('пустая переменная не считается заданной', () => {
    expect(defaultStateDir({ DSH_1C_STATE_DIR: '', ZCODE_1C_STATE_DIR: '/zcode' })).toBe('/zcode')
  })
})

describe('инвариант: литералы раскладок живут только там, где это решение', () => {
  /** Файлы, которым разрешено называть раскладку строкой. */
  const ALLOWED: Readonly<Record<string, string>> = {
    'src/paths.ts': 'таблица LAYOUT_DIRS и поиск конфига контура',
    'src/state.ts': 'путь состояния по умолчанию',
    'src/preset/config.ts': 'доставка пресета резолвит данные проекта сама',
    'src/style-checker.ts': 'кандидаты пути чекера стиля',
    'src/gates/kd-session-start.ts': 'legacy-конфиг хоста для url станции КД',
  }

  it('гейты не заводят своих путей раскладки', () => {
    const files = globSync('src/**/*.ts', { cwd: REPO_ROOT }).map((path) => path as string)
    expect(files.length).toBeGreaterThan(10)
    for (const rel of files) {
      if (rel in ALLOWED) continue
      const text = readFileSync(join(REPO_ROOT, rel), 'utf8')
      const hit = /['"]\.(dsh|zcode)(?:\/|['"])/ .exec(text)
      expect(hit, `${rel}: путь раскладки строкой — брать из paths.ts`).toBeNull()
    }
  })

  it('в src/ нет второй копии пары «.dsh, .zcode»', () => {
    for (const rel of globSync('src/**/*.ts', { cwd: REPO_ROOT }).map((path) => path as string)) {
      if (rel === 'src/paths.ts') continue
      const text = readFileSync(join(REPO_ROOT, rel), 'utf8')
      expect(text, `${rel}: своя копия списка раскладок`).not.toMatch(/\[\s*'\.dsh[^\]]*'\.zcode'\s*\]/)
    }
  })
})

describe('нечитаемый вход: сломанный канон не подменяется legacy', () => {
  it('каталог на месте contour.json — канон виноват, legacy не подхватывается', () => {
    const fixture = createFixture({ write: { 'tree/.zcode/1c/contour': 'test\n' } })
    try {
      // Каталог вместо файла: existsSync — да, чтение — нет.
      mkdirSync(join(fixture.tree, '.dsh', '1c', 'contour'), { recursive: true })
      expect(contourOf(resolveProject(fixture.tree))).toBe('prod')
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('нечитаемый allowlist не разрешает и не уступает legacy', () => {
    const fixture = createFixture({ write: { 'tree/.zcode/1c/mcp-write-allow.txt': 'execute_code\n' } })
    try {
      mkdirSync(join(fixture.tree, '.dsh', '1c', 'mcp-write-allow.txt'), { recursive: true })
      const call: GateCall = { tool: 'mcp__1c-db__execute_code', args: {}, cwd: fixture.tree }
      expect(mcpPre(call, resolveProject(fixture.tree), stateOf(fixture)))
        .toMatchObject({ kind: 'deny', code: 'mcp-unsafe' })
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('нечитаемые эскалации носителя не уступают legacy', () => {
    const fixture = createFixture({ write: { 'tree/.zcode/1c/bsl-module-gate-escalations.txt': 'all:src\n' } })
    try {
      mkdirSync(join(fixture.tree, '.dsh', '1c', 'bsl-module-gate-escalations.txt'), { recursive: true })
      expect(escalationSkips(projectOf(fixture.tree))).toEqual([])
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('эскалации нечитаемы — гейт это называет, а не уступает legacy', () => {
    const fixture = createFixture({})
    try {
      mkdirSync(join(fixture.tree, '.dsh', '1c', 'unica-gate-escalations.txt'), { recursive: true })
      put(fixture.tree, '.zcode/unica-gate-escalations.txt', 'src/cf/CommonModules/ОбщегоНазначения.bsl\n')
      const report = escalationEntries(fixture.tree, fixture.tree)
      expect(report.matched).toEqual([])
      expect(report.report.join(' ')).toContain('не прочитан')
      expect(report.label).toBe('.dsh/1c/unica-gate-escalations.txt')
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })
})
