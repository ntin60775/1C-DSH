/**
 * Раскладка и пути контура: приоритет `.dsh/1c`, маркеры проекта, исходники.
 * @module tests/unit/paths.spec
 */

import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { contourDir, isAgentsDoc, normalizeSourcePath, resolveProject } from '../../src/paths.js'
import { createFixture } from '../helpers/fixture.js'

describe('resolveProject', () => {
  it('находит конфиг в legacy .zcode/1c', () => {
    const fixture = createFixture({ contour: { unica: { server: 'unica' } } })
    expect(resolveProject(fixture.tree)).toMatchObject({ root: fixture.tree, layout: 'zcode' })
    expect(resolveProject(fixture.tree)?.contour).toEqual({ unica: { server: 'unica' } })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('находит конфиг в .dsh/1c', () => {
    const fixture = createFixture({ contour: { unica: { server: 'unica' } }, layout: 'dsh' })
    expect(resolveProject(fixture.tree)).toMatchObject({ root: fixture.tree, layout: 'dsh' })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('новая раскладка перекрывает legacy в том же дереве', () => {
    const fixture = createFixture({ contour: { legacy: true } })
    const dir = join(fixture.tree, '.dsh', '1c')
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, 'contour.json'), JSON.stringify({ fresh: true }), 'utf8')
    expect(resolveProject(fixture.tree)).toMatchObject({ layout: 'dsh', contour: { fresh: true } })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('без конфига корень находится по маркеру v8project.yaml', () => {
    const fixture = createFixture()
    expect(resolveProject(fixture.tree)).toMatchObject({ root: fixture.tree, layout: 'markers', contour: {} })
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('из подкаталога поднимается до корня дерева', () => {
    const fixture = createFixture({ contour: {} })
    const nested = join(fixture.tree, 'src', 'cf', 'CommonModules')
    expect(resolveProject(nested)?.root).toBe(fixture.tree)
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('вне проекта 1С возвращает null', () => {
    const fixture = createFixture()
    expect(resolveProject(join(fixture.root, 'other-tree'))).toBeNull()
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('contourDir', () => {
  it('без каталога конфига возвращает null', () => {
    const fixture = createFixture()
    expect(contourDir(fixture.tree)).toBeNull()
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('normalizeSourcePath', () => {
  const cases = [
    ['src/cf/CommonModules/Модуль.bsl', 'src/cf/CommonModules/Модуль.bsl'],
    ['src/cfe/Расширение/Module.bsl', 'src/cfe/Расширение/Module.bsl'],
    ['src/epf/Обработка.epf', 'src/epf/Обработка.epf'],
    ['src/erf/Отчёт.erf', 'src/erf/Отчёт.erf'],
    ['tests/epf/Тест.epf', 'tests/epf/Тест.epf'],
  ] as const

  for (const [relative, expected] of cases) {
    it(`узнаёт исходник ${relative}`, () => {
      const fixture = createFixture()
      const target = normalizeSourcePath(relative, fixture.tree)
      expect(target).toMatchObject({ rel: expected, root: fixture.tree })
      rmSync(fixture.root, { recursive: true, force: true })
    })
  }

  it('файл вне исходников не трогает', () => {
    const fixture = createFixture()
    expect(normalizeSourcePath('src/docs/readme.md', fixture.tree)).toBeNull()
    expect(normalizeSourcePath('docs/readme.md', fixture.tree)).toBeNull()
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('абсолютный путь разрешается независимо от cwd', () => {
    const fixture = createFixture()
    const absolute = join(fixture.tree, 'src', 'cf', 'A.bsl')
    expect(normalizeSourcePath(absolute, '/tmp')).toMatchObject({ rel: 'src/cf/A.bsl', root: fixture.tree })
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('isAgentsDoc', () => {
  it('AGENTS.md на любом уровне — документация', () => {
    expect(isAgentsDoc('AGENTS.md')).toBe(true)
    expect(isAgentsDoc('src/cf/AGENTS.md')).toBe(true)
    expect(isAgentsDoc('src/cf/Module.bsl')).toBe(false)
  })
})
