/**
 * Раскладка DSH: канон `.dsh/1c`, промежуточная `.dsh`, состояние контура.
 *
 * Т6 доказал канон и переезд; Т10 снял legacy-раскладки ZCode после
 * перевода парка: таблица раскладок содержит только DSH, корень состояния
 * задаёт `DSH_1C_STATE_DIR`, скрипты контура живут в пакете
 * (`$DSH_1C_ROOT`), проект их не вендорит.
 *
 * spec: docs/plans/dsh-contour.md, «Готово» 9 — чтение legacy удалено
 * после перевода последнего проекта.
 * invariant: канон `.dsh/1c` перекрывает промежуточную `.dsh`; нечитаемый
 * вход канона останавливает поиск (fail-closed).
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
function projectOf(root: string, layout: 'dsh' | 'markers' = 'markers'): ContourProject {
  return { root, layout, contour: {} }
}

describe('таблица раскладок', () => {
  it('канон первым, затем промежуточная; legacy снят (Т10)', () => {
    expect(LAYOUT_DIRS).toEqual(['.dsh/1c', '.dsh'])
    expect(layoutCandidates('/p', 'contour')).toEqual([
      '/p/.dsh/1c/contour',
      '/p/.dsh/contour',
    ])
  })

  it('первый существующий файл побеждает, подпись называет раскладку', () => {
    const found = findLayoutFile(
      '/p',
      'contour',
      (path) => (path === '/p/.dsh/contour' ? 'test\n' : null),
    )
    expect(found).toMatchObject({ text: 'test\n', label: '.dsh/contour' })
  })

  it('файлов нет — null, а не выдуманный путь', () => {
    expect(findLayoutFile('/p', 'contour', () => null)).toBeNull()
  })
})

describe('признак контура: канон перекрывает промежуточную раскладку', () => {
  it('.dsh/1c/contour перекрывает .dsh/contour', () => {
    const fixture = createFixture({
      write: {
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

  it('проект только с .dsh/contour читается как test', () => {
    const fixture = createFixture({ write: { 'tree/.dsh/contour': 'test\n' } })
    try {
      expect(contourOf(resolveProject(fixture.tree))).toBe('test')
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })
})

describe('входы гейтов: канон .dsh/1c', () => {
  it('allowlist в .dsh/1c решает по себе и не читает .dsh при каноне', () => {
    // Только опасные инструменты: у остальных решение `allow` наступает
    // раньше allowlist'а, и тест доказывал бы пустоту.
    const fixture = createFixture({
      write: {
        'tree/.dsh/mcp-write-allow.txt': 'execute_code\n',
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
      // …и не подхватывает разрешение из промежуточной раскладки.
      expect(mcpPre(call('mcp__1c-db__execute_code'), project, stateOf(fixture)))
        .toMatchObject({ kind: 'deny', code: 'mcp-unsafe' })
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('без канона allowlist читается из промежуточной .dsh', () => {
    const fixture = createFixture({
      write: { 'tree/.dsh/mcp-write-allow.txt': 'execute_code\n' },
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

  it('пути гейта: канон раскладки, скрипты — в пакете контура', () => {
    expect(contourPaths()).toMatchObject({
      layoutDir: '.dsh/1c',
      escalationFile: '.dsh/1c/unica-gate-escalations.txt',
      initWorktreeScript: '$DSH_1C_ROOT/scripts/init_worktree.sh',
    })
  })

  it('эскалации Unica: канон .dsh/1c перекрывает промежуточную .dsh', () => {
    const fixture = createFixture({})
    try {
      const canonPath = 'src/cf/CommonModules/ОбщегоНазначения.bsl'
      const flatPath = 'src/cfe/Расширение/Общий.bsl'
      put(fixture.tree, '.dsh/unica-gate-escalations.txt', `${flatPath}\n`)
      expect(escalationEntries(fixture.tree, fixture.tree)).toMatchObject({
        found: true,
        matched: [flatPath],
        label: '.dsh/unica-gate-escalations.txt',
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
      put(fixture.tree, '.dsh/bsl-module-gate-escalations.txt', 'all:src\n')
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

  it('чекер стиля: канон .dsh/1c/skills, затем промежуточные адреса', async () => {
    const fixture = createFixture({})
    try {
      const stub = join(fixture.root, 'stub-python.sh')
      writeFileSync(stub, '#!/bin/sh\necho "$@"\nexit 1\n', { mode: 0o755 })
      const checker = createStyleChecker({ env: { PATH: '' }, python: stub })
      put(fixture.tree, '.dsh/skills/1c-bsl-code-style/scripts/bsl_style_check.py', 'flat')
      expect(await checker.run([], fixture.tree)).toContain(
        join(fixture.tree, '.dsh/skills/1c-bsl-code-style/scripts/bsl_style_check.py'),
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

describe('паритет раскладок DSH: один вход — один вердикт', () => {
  it('одинаковый набор входов в .dsh/1c и .dsh даёт одинаковые решения', () => {
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
          .replaceAll(/\/\.dsh(?:\/1c)?\//g, '/LAYOUT/'),
      )
    const canon = normalize(run('.dsh/1c'))
    expect(normalize(run('.dsh'))).toEqual(canon)
    expect(canon).toMatchObject({
      contour: 'test',
      gate: { kind: 'allow' },
      skips: [['directive-in-common-module', 'src/cf/Общий']],
      connection: 'File=TMPDIR/FIXTURE/tree/LAYOUT/testpilot/$TMP/tree/db',
    })
  })
})

describe('корень состояния', () => {
  it('DSH_1C_STATE_DIR переопределяет корень', () => {
    expect(defaultStateDir({ DSH_1C_STATE_DIR: '/dsh' })).toBe('/dsh')
  })

  it('без переменных — ~/.dsh/state/1c', () => {
    expect(defaultStateDir({})).toBe(join(homedir(), '.dsh', 'state', '1c'))
  })

  it('пустая переменная не считается заданной', () => {
    expect(defaultStateDir({ DSH_1C_STATE_DIR: '' })).toBe(join(homedir(), '.dsh', 'state', '1c'))
  })

  it('тильда раскрывается в домашний каталог', () => {
    expect(defaultStateDir({ DSH_1C_STATE_DIR: '~/state' })).toBe(join(homedir(), 'state'))
  })
})

describe('инвариант: литералы раскладок живут только там, где это решение', () => {
  /** Файлы, которым разрешено называть раскладку строкой. */
  const ALLOWED: Readonly<Record<string, string>> = {
    'src/paths.ts': 'таблица LAYOUT_DIRS и поиск конфига контура',
    'src/state.ts': 'путь состояния по умолчанию',
    'src/preset/config.ts': 'доставка пресета резолвит данные проекта сама',
    'src/style-checker.ts': 'кандидаты пути чекера стиля',
  }

  it('гейты не заводят своих путей раскладки', () => {
    const files = globSync('src/**/*.ts', { cwd: REPO_ROOT }).map((path) => path as string)
    expect(files.length).toBeGreaterThan(10)
    for (const rel of files) {
      if (rel in ALLOWED) continue
      const text = readFileSync(join(REPO_ROOT, rel), 'utf8')
      const hit = /['"]\.(dsh|zcode)(?:\/|['"])/.exec(text)
      expect(hit, `${rel}: путь раскладки строкой — брать из paths.ts`).toBeNull()
    }
  })

  it('в src/ нет второй копии таблицы раскладок', () => {
    for (const rel of globSync('src/**/*.ts', { cwd: REPO_ROOT }).map((path) => path as string)) {
      if (rel === 'src/paths.ts') continue
      const text = readFileSync(join(REPO_ROOT, rel), 'utf8')
      expect(text, `${rel}: своя копия списка раскладок`).not.toMatch(/\[\s*'\.dsh\/1c'\s*,\s*'\.dsh'\s*\]/)
    }
  })
})

describe('нечитаемый вход: сломанный канон не подменяется другой раскладкой', () => {
  it('каталог на месте contour.json — канон виноват, .dsh не подхватывается', () => {
    const fixture = createFixture({ write: { 'tree/.dsh/contour': 'test\n' } })
    try {
      // Каталог вместо файла: existsSync — да, чтение — нет.
      mkdirSync(join(fixture.tree, '.dsh', '1c', 'contour'), { recursive: true })
      expect(contourOf(resolveProject(fixture.tree))).toBe('prod')
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('нечитаемый allowlist не разрешает и не уступает .dsh', () => {
    const fixture = createFixture({ write: { 'tree/.dsh/mcp-write-allow.txt': 'execute_code\n' } })
    try {
      mkdirSync(join(fixture.tree, '.dsh', '1c', 'mcp-write-allow.txt'), { recursive: true })
      const call: GateCall = { tool: 'mcp__1c-db__execute_code', args: {}, cwd: fixture.tree }
      expect(mcpPre(call, resolveProject(fixture.tree), stateOf(fixture)))
        .toMatchObject({ kind: 'deny', code: 'mcp-unsafe' })
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('нечитаемые эскалации носителя не уступают .dsh', () => {
    const fixture = createFixture({ write: { 'tree/.dsh/bsl-module-gate-escalations.txt': 'all:src\n' } })
    try {
      mkdirSync(join(fixture.tree, '.dsh', '1c', 'bsl-module-gate-escalations.txt'), { recursive: true })
      expect(escalationSkips(projectOf(fixture.tree))).toEqual([])
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('эскалации нечитаемы — гейт это называет, а не уступает .dsh', () => {
    const fixture = createFixture({})
    try {
      mkdirSync(join(fixture.tree, '.dsh', '1c', 'unica-gate-escalations.txt'), { recursive: true })
      put(fixture.tree, '.dsh/unica-gate-escalations.txt', 'src/cf/CommonModules/ОбщегоНазначения.bsl\n')
      const report = escalationEntries(fixture.tree, fixture.tree)
      expect(report.matched).toEqual([])
      expect(report.report.join(' ')).toContain('не прочитан')
      expect(report.label).toBe('.dsh/1c/unica-gate-escalations.txt')
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })
})
