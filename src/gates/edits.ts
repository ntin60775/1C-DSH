/**
 * Механика правок для гейтов после вызова: диапазоны строк, цели, планы.
 *
 * Общая часть `bsl_style_gate.py` и `bsl_module_gate.py`: разбор патча,
 * поиск диапазона вставленного текста в файле и тексты планов `unica.apply`.
 * «Изменённые строки» — единственная граница ответственности агента: чужой
 * старый код остаётся фоном и не блокирует.
 * @module dsh-1c-contour/gates/edits
 */

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

import type { GateCall } from '../contract.js'
import { normalizeSourcePath, BSL_SCAN_PREFIXES } from '../paths.js'

/** Заголовок правки файла в патче `apply_patch`. */
export const PATCH_FILE_RE = /^\*\*\* (?:Update|Add) File: (.+)$/

/** Заголовок hunk'а: смещение и число строк новой версии, 1-based. */
export const PATCH_HUNK_RE = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/

/** Строка новой версии файла, ограниченная диапазоном правки. */
export type Span = readonly [number, number]

/**
 * Диапазон строк, который занимает вставленный текст.
 * @param newText - текст файла после правки.
 * @param needle - вставленный текст.
 * @returns диапазон 1-based включительно либо `null`, если текст не найден.
 */
export function spanOfNewText(newText: string, needle: string): Span | null {
  if (needle === '') return null
  const index = newText.indexOf(needle)
  if (index < 0) return null
  let start = 1
  for (let position = 0; position < index; position += 1) {
    if (newText.charCodeAt(position) === 10) start += 1
  }
  const lines = needle.split('\n').length - 1
  return [start, start + lines]
}

/**
 * Диапазоны новых строк по заголовкам hunk'ов, по файлам патча.
 * @param patch - текст патча.
 * @returns диапазоны по путям файлов.
 */
export function patchFileSpans(patch: string): Map<string, Span[]> {
  const spans = new Map<string, Span[]>()
  let current: string | null = null
  for (const line of patch.split(/\r?\n/)) {
    const file = PATCH_FILE_RE.exec(line)
    if (file?.[1] !== undefined) {
      current = file[1].trim()
      continue
    }
    const hunk = PATCH_HUNK_RE.exec(line)
    if (hunk === null || current === null) continue
    const start = Number(hunk[1])
    const count = hunk[2] === undefined ? 1 : Number(hunk[2])
    if (!Number.isFinite(count) || count <= 0) continue
    const list = spans.get(current) ?? []
    list.push([start, start + count - 1])
    spans.set(current, list)
  }
  return spans
}

/**
 * Диапазоны изменённых строк файла после правки.
 *
 * `write` пишет файл целиком — ответственность агента на всём файле.
 * `edit` даёт диапазон вставленного текста. Патч — диапазон hunk'а.
 * @param call - вызов инструмента.
 * @param relPath - путь файла относительно корня дерева.
 * @param root - корень дерева.
 * @returns диапазоны либо `null` — «весь файл».
 */
export function changedLines(call: GateCall, relPath: string, root: string): Span[] | null {
  const args = typeof call.args === 'object' && call.args !== null && !Array.isArray(call.args)
    ? call.args as Record<string, unknown>
    : {}
  if (call.tool === 'write') return null
  let newText: string
  try {
    newText = readFileSync(join(root, relPath), 'utf8')
  } catch {
    return null
  }
  if (call.tool === 'edit') {
    const inserted = typeof args['new_string'] === 'string' ? args['new_string'] : ''
    const span = spanOfNewText(newText, inserted)
    return span === null ? null : [span]
  }
  const patch = args['patch'] ?? args['input']
  if (typeof patch !== 'string') return null
  for (const [path, spans] of patchFileSpans(patch)) {
    if (path === relPath || path.endsWith(`/${relPath}`) || relPath.endsWith(`/${path}`)) return spans
  }
  return null
}

/**
 * Пути файлов, затронутых вызовом: прямая цель либо заголовки патча.
 * @param call - вызов инструмента.
 * @returns сырые пути из аргументов, в порядке появления.
 */
export function rawPathsOf(call: GateCall): string[] {
  const args = typeof call.args === 'object' && call.args !== null && !Array.isArray(call.args)
    ? call.args as Record<string, unknown>
    : {}
  const direct = args['file_path'] ?? args['path']
  const raws: string[] = []
  if (typeof direct === 'string' && direct !== '') raws.push(direct)
  if (call.tool === 'apply_patch' || raws.length === 0) {
    const patch = args['patch'] ?? args['input']
    if (typeof patch === 'string') {
      for (const line of patch.split(/\r?\n/)) {
        const file = PATCH_FILE_RE.exec(line)
        if (file?.[1] !== undefined) raws.push(file[1].trim())
      }
    }
  }
  return raws
}

/**
 * Изменённые `.bsl`-исходники вызова.
 * @param call - вызов инструмента.
 * @param cwd - каталог сессии.
 * @returns пути относительно корней деревьев.
 */
export function bslTargets(call: GateCall, cwd: string): string[] {
  const targets: string[] = []
  for (const raw of rawPathsOf(call)) {
    const normalized = normalizeSourcePath(raw, cwd)
    if (normalized !== null && normalized.rel.toLowerCase().endsWith('.bsl')) targets.push(normalized.rel)
  }
  return targets
}

/**
 * Тексты ops плана `unica.apply`, адресованные в тела BSL-модулей.
 * @param args - канонические аргументы вызова.
 * @returns тексты правок; пусто, если правка не в тело модуля.
 */
export function planBslTexts(args: Readonly<Record<string, unknown>>): string[] {
  const texts: string[] = []
  const ops = args['ops']
  if (!Array.isArray(ops)) return texts
  for (const op of ops) {
    if (typeof op !== 'object' || op === null || Array.isArray(op)) continue
    const opArgs = (op as Record<string, unknown>)['args']
    if (typeof opArgs !== 'object' || opArgs === null || Array.isArray(opArgs)) continue
    const at = (opArgs as Record<string, unknown>)['at']
    const text = (opArgs as Record<string, unknown>)['text']
    if (typeof at === 'string' && at.endsWith('.Body') && typeof text === 'string' && text.trim() !== '') {
      texts.push(text)
    }
  }
  return texts
}

/** Рекурсивный обход каталога: файлы с путём относительно него. */
function walkFiles(base: string): string[] {
  const found: string[] = []
  let entries: string[]
  try {
    entries = readdirSync(base)
  } catch {
    // Каталога исходников нет (или нет прав) — обход пуст, гейт молчит.
    return found
  }
  for (const entry of entries) {
    const path = join(base, entry)
    let stat
    try {
      stat = statSync(path)
    } catch {
      // Файл исчез между чтением каталога и stat — пропускаем.
      continue
    }
    if (stat.isDirectory()) {
      for (const nested of walkFiles(path)) found.push(join(entry, nested))
      continue
    }
    if (stat.isFile()) found.push(entry)
  }
  return found
}

/**
 * Все `.bsl` в каталогах сканирования BSL (`BSL_SCAN_PREFIXES`: охранные
 * каталоги плюс внешние epf/erf — их узлы правятся через `unica.apply`).
 * @param root - корень дерева.
 * @returns абсолютные пути.
 */
export function bslFilesUnder(root: string): string[] {
  const files: string[] = []
  for (const prefix of BSL_SCAN_PREFIXES) {
    const base = join(root, prefix.replace(/\/$/, ''))
    for (const relative of walkFiles(base)) {
      if (!relative.toLowerCase().endsWith('.bsl')) continue
      files.push(join(base, relative))
    }
  }
  return files
}

/**
 * Свежие `.bsl` в каталогах сканирования BSL (`BSL_SCAN_PREFIXES`).
 *
 * Свежесть — кандидат на проверку после `unica.apply`, а не доказательство:
 * гейт носителя всё равно ищет в этих файлах текст плана.
 * @param root - корень дерева.
 * @param sinceMs - время плана в миллисекундах.
 * @param slackMs - запас на расхождение часов файловой системы.
 * @returns относительные пути от корня дерева.
 */
export function freshBslFiles(root: string, sinceMs: number, slackMs: number): string[] {
  const fresh: string[] = []
  const cutoff = sinceMs - slackMs
  for (const prefix of BSL_SCAN_PREFIXES) {
    const base = join(root, prefix.replace(/\/$/, ''))
    for (const relative of walkFiles(base)) {
      if (!relative.toLowerCase().endsWith('.bsl')) continue
      try {
        if (statSync(join(base, relative)).mtimeMs >= cutoff) fresh.push(`${prefix}${relative}`)
      } catch {
        // Файл исчез между обходом и stat — кандидатом не считается.
        continue
      }
    }
  }
  return fresh
}
