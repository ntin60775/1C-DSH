/**
 * Разбор отчёта стиль-чекера на горячие и фоновые нарушения.
 *
 * invariant: режим «нарушения вне изменённых строк не блокируют» достижим с
 * реальным чекером пака `1c-bsl-code-style`: его итоговая строка
 * (`bsl_style_violations=N fixed=M`, `bsl_style_check_ok`) — метаданные, а не
 * нарушение. Прежде она попадала в `hot` как нераспознанная, и фон блокировал
 * правку в любом файле с нарушениями.
 * spec: docs/dsh-gates.md, гейт `bsl_style_gate` (post, блок только по
 * изменённым строкам).
 * @module tests/unit/bsl-style-split.spec
 */

import { describe, expect, it } from 'vitest'

import { splitReport } from '../../src/gates/bsl-style.js'

/** Отчёт реального `bsl_style_check.py` при единственном нарушении. */
const REAL_COLD_REPORT = [
  'src/cf/CommonModules/М/Ext/Module.bsl:14: [operator-case] неканонический регистр операторов: НЕ→Не',
  'bsl_style_violations=1',
].join('\n')

/** Отчёт реального чекера после `--fix`. */
const REAL_FIXED_REPORT = [
  'src/cf/CommonModules/М/Ext/Module.bsl:14: [tab-rhythm] пустая строка без табов внутри тела метода',
  'bsl_style_violations=1 fixed=1',
].join('\n')

describe('splitReport: мета-строки чекера', () => {
  it('нарушение вне правки с итоговой строкой остаётся фоном, hot пуст', () => {
    const result = splitReport(
      REAL_COLD_REPORT,
      ['src/cf/CommonModules/М/Ext/Module.bsl'],
      new Map([['src/cf/CommonModules/М/Ext/Module.bsl', [[3, 5] as [number, number]]]]),
    )
    expect(result.hot).toEqual([])
    expect(result.cold).toHaveLength(1)
  })

  it('итог --fix не делает правку горячей', () => {
    const result = splitReport(
      REAL_FIXED_REPORT,
      ['src/cf/CommonModules/М/Ext/Module.bsl'],
      new Map([['src/cf/CommonModules/М/Ext/Module.bsl', [[3, 5] as [number, number]]]]),
    )
    expect(result.hot).toEqual([])
    expect(result.cold).toHaveLength(1)
  })

  it('нарушение в изменённой строке горячее и при итоговой строке', () => {
    const result = splitReport(
      REAL_COLD_REPORT,
      ['src/cf/CommonModules/М/Ext/Module.bsl'],
      new Map([['src/cf/CommonModules/М/Ext/Module.bsl', [[14, 14] as [number, number]]]]),
    )
    expect(result.hot).toHaveLength(1)
    expect(result.cold).toEqual([])
  })

  it('чистый прогон без нарушений не даёт ни hot, ни cold', () => {
    const result = splitReport('bsl_style_check_ok', ['M.bsl'], new Map([['M.bsl', null]]))
    expect(result).toEqual({ hot: [], cold: [] })
  })

  it('неизвестная строка отчёта по-прежнему горячая', () => {
    const result = splitReport(
      'Traceback: чекер упал\nbsl_style_violations=0',
      ['M.bsl'],
      new Map([['M.bsl', null]]),
    )
    expect(result.hot).toEqual(['Traceback: чекер упал'])
  })
})