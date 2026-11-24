/**
 * Юнит-тесты распознавания запуска Конфигуратора (`no_designer`).
 *
 * invariant: агент не дергает Конфигуратор — распознавание ловит обёртки и
 * сегменты, но не трогает чтение бинарника, grep по файлам и ENTERPRISE.
 * @module tests/unit/no-designer.spec
 */

import { describe, expect, it } from 'vitest'

import { designerHit, unicaRunHit } from '../../src/gates/no-designer.js'

describe('designerHit', () => {
  it('ловит запуск в обёртках и сегментах', () => {
    const deny: readonly string[] = [
      '1cv8 DESIGNER /F ib',
      '/opt/1cv8/x86_64/8.3.27.1859/1cv8 DESIGNER /F ib',
      'env -u WAYLAND_DISPLAY GDK_BACKEND=x11 1cv8 DESIGNER /F ib',
      'env -i FOO=bar 1cv8 DESIGNER',
      'timeout 120 1cv8 DESIGNER',
      'timeout -k 5 10s 1cv8 DESIGNER',
      'nohup 1cv8 DESIGNER &',
      'distrobox enter 1c -- 1cv8 DESIGNER /F ib',
      "bash -c '1cv8 DESIGNER /F ib'",
      'cd /tmp && 1cv8 DESIGNER',
      'echo start; 1cv8 DESIGNER',
      '1cv8 DESIGNER | tee log',
      'v8-runner make ПФ --output out',
      'v8-runner artifacts ПФ --output out',
      'v8-runner launch designer',
      'v8-runner launch configurator',
      '/x/v8-runner launch --client designer',
    ]
    for (const command of deny) {
      expect(designerHit(command), command).not.toBeNull()
    }
  })

  it('не трогает чтение, grep и ENTERPRISE', () => {
    const allow: readonly string[] = [
      '1cv8 ENTERPRISE /F ib /Execute x.epf',
      '1cv8c ENTERPRISE /F ib',
      'ibcmd config import src --out=x.epf',
      'grep -n DESIGNER v8project.yaml',
      'strings /opt/1cv8/x86_64/8.3.27.1859/1cv8 | grep DESIGNER',
      "echo '1cv8 DESIGNER'",
      'cat build/tools/platform-bin/1cv8',
      'v8-runner push main',
      'v8-runner launch thin',
      'v8-runner check main',
      'git grep DESIGNER',
    ]
    for (const command of allow) {
      expect(designerHit(command), command).toBeNull()
    }
  })

  it('make и launch различаются классом', () => {
    expect(designerHit('v8-runner make x --output o')).toBe('designer-make')
    expect(designerHit('1cv8 DESIGNER')).toBe('designer-launch')
    expect(designerHit('v8-runner launch designer')).toBe('designer-launch')
  })
})

describe('unicaRunHit', () => {
  const run = (args: unknown): ReturnType<typeof unicaRunHit> =>
    unicaRunHit({ tool: 'mcp__unica__unica_run_0123456789ab', args, cwd: '/x' })

  it('make блокируется, остальные операции живут', () => {
    expect(run({ op: 'make', args: {}, dryRun: false })).toBe('designer-make')
    expect(run({ op: 'launch', args: { client: 'designer' } })).toBe('designer-launch')
    expect(run({ op: 'launch', args: { client: 'configurator' } })).toBe('designer-launch')
    expect(run({ op: 'launch', args: { client: 'thin' } })).toBeNull()
    expect(run({ op: 'push', args: { sourceSet: 'main' } })).toBeNull()
    expect(run({ op: 'infobase.create', args: {} })).toBeNull()
    expect(run({})).toBeNull()
  })

  it('чужой сервер не распознаётся как run юники', () => {
    expect(unicaRunHit({ tool: 'mcp__1c-db__execute_code', args: { op: 'make' }, cwd: '/x' })).toBeNull()
  })
})
