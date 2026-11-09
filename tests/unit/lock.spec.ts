/**
 * Замок инфобазы: ключ подключения, файл замка, учётные данные.
 *
 * invariant: пароль базы не попадает в файл состояния; файл замка доступен
 * только владельцу.
 * invariant: одно дерево в двух написаниях (симлинк) даёт один ключ.
 * @module tests/unit/lock.spec
 */

import { readFileSync, rmSync, statSync, symlinkSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { evaluatePre } from '../../src/gates/unica-source.js'
import { lockKey, lockPath, normalizedConnection, withoutCredentials } from '../../src/lock.js'
import { resolveProject } from '../../src/paths.js'
import { createGateState } from '../../src/state.js'
import { createFixture } from '../helpers/fixture.js'

/** Строка подключения с учётными данными, как в боевом local-слое. */
const WITH_CREDENTIALS = 'Srvr="host";Ref="erp";Usr="bot";Pwd="secret";'

describe('withoutCredentials', () => {
  it('убирает Usr и Pwd, остальное сохраняет', () => {
    const clean = withoutCredentials(WITH_CREDENTIALS)
    expect(clean).not.toContain('secret')
    expect(clean).not.toContain('Usr')
    expect(clean).toContain('Ref="erp"')
  })

  it('не трогает сегменты, начинающиеся иначе', () => {
    const clean = withoutCredentials('Srvr="host";UsrX=1;PwdFile=a;Ref="erp"')
    expect(clean).toBe('Srvr="host";UsrX=1;PwdFile=a;Ref="erp"')
  })
})

describe('ключ замка', () => {
  it('файловая база разворачивается от дерева', () => {
    const fixture = createFixture()
    expect(normalizedConnection('File=build/ib', fixture.tree))
      .toBe(`file=${join(fixture.tree, 'build', 'ib')}`)
    rmSync(fixture.root, { recursive: true, force: true })
  })

  it('дерево через симлинк даёт тот же ключ', () => {
    const fixture = createFixture()
    const link = join(fixture.root, 'tree-link')
    symlinkSync(fixture.tree, link)
    expect(lockKey('File=build/ib', link)).toBe(lockKey('File=build/ib', fixture.tree))
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

describe('файл замка', () => {
  it('не содержит пароля и доступен только владельцу', () => {
    const fixture = createFixture({
      contour: {},
      write: { 'tree/v8project.local.yaml': `infobases:\n  origin:\n    connection: ${WITH_CREDENTIALS}\n` },
    })
    const state = createGateState({ sessionId: 's1', stateDir: fixture.stateDir })
    const decision = evaluatePre(
      {
        tool: 'mcp__unica__run',
        args: { args: { op: 'push', cwd: fixture.tree } },
        cwd: fixture.tree,
      },
      resolveProject(fixture.tree),
      state,
    )
    expect(decision).toMatchObject({ kind: 'allow' })
    const file = lockPath(fixture.stateDir, WITH_CREDENTIALS, fixture.tree)
    const raw = readFileSync(file, 'utf8')
    expect(raw).not.toContain('secret')
    expect(raw).toContain('Ref=\\"erp\\"')
    expect(statSync(file).mode & 0o777).toBe(0o600)
    rmSync(fixture.root, { recursive: true, force: true })
  })
})
