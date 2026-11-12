#!/usr/bin/env node
/**
 * Заглушка движка стиля для паритет-корпуса: контракт `check <files>`.
 *
 * Гейт получает её через `1C_STYLE_ENGINE`, поэтому проверяется поведение
 * гейта, а не чекера. Node-заглушка держит набор тестов свободным от python.
 *
 * Нарушение — строка, содержащая «Плохо»: печатается `<файл>:<строка>: [...]`.
 */
import { readFileSync } from 'node:fs'

let args = process.argv.slice(2)
if (args[0] === 'check') args = args.slice(1)
let bad = 0
for (const path of args) {
  let lines
  try {
    lines = readFileSync(path, 'utf8').split('\n')
  } catch {
    continue
  }
  for (const [index, line] of lines.entries()) {
    if (line.includes('Плохо')) {
      console.log(`${path}:${index + 1}: [BSL-STYLE] Плохой стиль`)
      bad += 1
    }
  }
}
process.exit(bad > 0 ? 1 : 0)
