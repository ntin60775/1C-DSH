/**
 * Имена инструментов Юники в живом DSH: клиент MCP обрезает имя под контракт
 * function-name — точка становится подчёркиванием, к имени дописывается
 * `_<12 hex>`. Гейты обязаны читать глагол и в этой форме.
 *
 * invariant: `unicaVerb` возвращает глагол контракта 0.15 независимо от формы
 * имени, иначе гейт сверки не узнаёт мутацию и пропускает правку.
 * @module tests/unit/unica-name.spec
 */

import { describe, expect, it } from 'vitest'

import { mcpShortName, unicaVerb } from '../../src/contour.js'

describe('unicaVerb в живых формах имени', () => {
  it('читает нормализованное имя с хвостом-хешем', () => {
    expect(unicaVerb('mcp__unica__unica_apply_8ba7af26408e', 'unica')).toBe('apply')
    expect(unicaVerb('mcp__unica__unica_check_6db7d2a7495f', 'unica')).toBe('check')
    expect(unicaVerb('mcp__unica__unica_task_get_011884f98058', 'unica')).toBe('task.get')
    expect(unicaVerb('mcp__unica__unica_task_result_67327c71a8ed', 'unica')).toBe('task.result')
  })

  it('читает формы без хеша и с чужим сервером в середине', () => {
    expect(unicaVerb('mcp__unica__unica.run', 'unica')).toBe('run')
    expect(unicaVerb('mcp__plugin_unica_unica__unica.docs', 'unica')).toBe('docs')
    expect(unicaVerb('mcp__unica__view', 'unica')).toBe('view')
  })

  it('не считает юникой чужие серверы', () => {
    expect(unicaVerb('mcp__1c-db__execute_query', 'unica')).toBe('')
    expect(unicaVerb('mcp__unica__', 'unica')).toBe('')
    expect(unicaVerb('write', 'unica')).toBe('')
  })

  it('короткое имя сервера остаётся сырым: гейту MCP нужен полный хвост', () => {
    expect(mcpShortName('mcp__1c-db__execute_query', '1c-db')).toBe('execute_query')
  })
})
