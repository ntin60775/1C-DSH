/**
 * Замок инфобазы: ключ подключения, файл замка, учётные данные.
 *
 * invariant: пароль базы не попадает в файл состояния; файл замка доступен
 * только владельцу.
 * invariant: одно дерево в двух написаниях (симлинк) даёт один ключ.
 * @module tests/unit/lock.spec
 */

import { spawnSync } from 'node:child_process'
import { readFileSync, rmSync, statSync, symlinkSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { evaluatePre } from '../../src/gates/unica-source.js'
import { describeHolder, lockKey, lockPath, normalizedConnection, standLockKey, standLockPath, standLockStatus, withoutCredentials } from '../../src/lock.js'
import type { LockRecord } from '../../src/contract.js'
import { seedStandLock } from '../helpers/locks.js'
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

/**
 * invariant: stand-замок, записанный python-стартером (scripts/ib_lock.py),
 * читается TS-гейтом: формат другого рантайма — не пересказ собственного.
 * Посев — литерал формата стартера (tests/helpers/locks.ts).
 */
describe('stand-замок python-стартера', () => {
  it('читается гейтом: pid-владелец жив, описание без undefined', () => {
    const fixture = createFixture()
    seedStandLock({
      tree: fixture.tree,
      connection: WITH_CREDENTIALS,
      pid: process.pid,
      stateDir: fixture.stateDir,
    })
    const file = standLockPath(fixture.stateDir, WITH_CREDENTIALS, fixture.tree)
    const record = JSON.parse(readFileSync(file, 'utf8')) as Record<string, unknown>
    // Формат стартера: без session_id и expires_ts, учёток нет.
    expect(record['kind']).toBe('stand')
    expect(record['pid']).toBe(process.pid)
    expect(record).not.toHaveProperty('session_id')
    expect(record).not.toHaveProperty('expires_ts')
    expect(JSON.stringify(record)).not.toContain('secret')

    const holder = standLockStatus(fixture.stateDir, WITH_CREDENTIALS, fixture.tree)
    expect(holder).not.toBeNull()
    expect(holder?.pid).toBe(process.pid)
    const text = describeHolder(holder as LockRecord)
    expect(text).not.toContain('undefined')
    expect(text).toContain(String(process.pid))
    rmSync(fixture.root, { recursive: true, force: true })
  })
})

/**
 * invariant: ключи замков заморожены литералом, посчитанным python-писателем
 * (scripts/ib_lock.py). TS-сторона сверяет тот же литерал; дрейф любой
 * стороны краснит её тест. Находка панели Т8 (P2): мутация lockKey не
 * ловилась, пока посев и чтение считали ключ одним кодом.
 */
describe('литералы ключей (межъязыковой охранник)', () => {
  it('ключ ИБ совпадает с литералом python-писателя', () => {
    expect(lockKey('Srvr=host;Ref=erp;', '/tree/a')).toBe('57db9fe786460ef38252e441c37967ddf24df939')
  })

  it('ключ stand совпадает с литералом python-писателя и не зависит от учёток', () => {
    expect(standLockKey('Srvr=host;Ref=erp;Usr=u;Pwd=p;', '/tree/a')).toBe('48b716fbf05cbc86e9c66e66017f0bfe9f9c8c2c')
    expect(standLockKey('Srvr=host;Ref=erp;', '/tree/a')).toBe('48b716fbf05cbc86e9c66e66017f0bfe9f9c8c2c')
  })

  it('мёртвый владелец не держит базу (standOwnerAlive)', () => {
    const fixture = createFixture()
    // Короткоживущий процесс: pid мёртв к моменту проверки.
    const dead = spawnSync('sleep', ['0'])
    expect(dead.status).toBe(0)
    const pid = dead.pid as number
    seedStandLock({ tree: fixture.tree, connection: WITH_CREDENTIALS, pid, stateDir: fixture.stateDir })
    expect(standLockStatus(fixture.stateDir, WITH_CREDENTIALS, fixture.tree)).toBeNull()
    rmSync(fixture.root, { recursive: true, force: true })
  })
})
