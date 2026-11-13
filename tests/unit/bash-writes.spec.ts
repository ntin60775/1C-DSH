/**
 * Разбор bash-записи в исходники: цель через sed и перенаправление.
 *
 * Ожидания взяты из поведения эталона и спеки bash-write: разбор по
 * операндам, fail-closed на неразбираемом вызове, правка копии вне дерева.
 *
 * target: спека bash-write — разбор sed по операндам и fail-closed без
 * упоминания пути исходников.
 * @module tests/unit/bash-writes.spec
 */

import { rmSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { bashWritesToSource } from '../../src/gates/unica-source.js'
import { BSL, createFixture } from '../helpers/fixture.js'

describe('bashWritesToSource', () => {
  const hitting = [
    `sed -i 's/a/b/' ${BSL}`,
    `sed -i -e 's/a/b/' -e 's/c/d/' ${BSL}`,
    `sed -i -f fix.sed ${BSL}`,
    `sed -ie 's/a/b/' ${BSL}`,
    `echo x | sed -i 's/a/b/' ${BSL}`,
    'echo x >> src/cf/CommonModules/Модуль.bsl',
    'echo x > tests/epf/Тест.epf',
    'f=src/cf/CommonModules/Модуль.bsl; sed -i \'s/a/b/\' "$f"',
    "sed -i 's/a/b/ /tmp/w.bsl # src/cf/",
  ]

  for (const command of hitting) {
    it(`блокирует: ${command}`, () => {
      const fixture = createFixture()
      expect(bashWritesToSource(command, fixture.tree)).not.toBeNull()
      rmSync(fixture.root, { recursive: true, force: true })
    })
  }

  const passing = [
    'echo x >> build/out/log.txt',
    'cp src/cfe/Расширение/Module.bsl /tmp/w.bsl && sed -i \'s/a/b/\' /tmp/w.bsl',
    "sed -i 's/src\\/cf\\///' /tmp/w.bsl",
    'f=build/ib/x.txt; sed -i \'s/a/b/\' "$f"',
    "sed -i 's/a/b/ /tmp/w.bsl",
    'sed -i \'s/a/b/\' src/docs/readme.md',
    'cat src/cf/CommonModules/Модуль.bsl',
    // spec: docs/plans/external-epf-loop.md, «Готово» п. 5 — bash-ветка
    // читает тот же SOURCE_PREFIXES: внешние epf/erf вне гейта.
    'sed -i \'s/a/b/\' src/epf/Обработка/epf.epf',
    'echo x > src/erf/Отчёт/erf.erf',
  ]

  for (const command of passing) {
    it(`пропускает: ${command}`, () => {
      const fixture = createFixture()
      expect(bashWritesToSource(command, fixture.tree)).toBeNull()
      rmSync(fixture.root, { recursive: true, force: true })
    })
  }

  it('правка AGENTS.md через sed не считается правкой исходника', () => {
    const fixture = createFixture()
    expect(bashWritesToSource("sed -i 's/a/b/' src/cf/AGENTS.md", fixture.tree)).toBeNull()
    rmSync(fixture.root, { recursive: true, force: true })
  })
})
