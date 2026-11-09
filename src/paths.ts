/**
 * Пути контура: исходники 1С, корень проекта, каталоги раскладки.
 *
 * Перенос python-эталона (`reference/gates/contour_common.py`) без изменения
 * поведения: те же префиксы, то же правило «первое вхождение префикса в
 * абсолютном пути», тот же обход вверх до маркера проекта.
 * @module dsh-1c-contour/paths
 */

import { readFileSync, realpathSync, statSync } from 'node:fs'
import { basename, dirname, join, resolve } from 'node:path'

import type { ContourProject } from './contract.js'

/**
 * Каталоги исходников 1С, редактируемые только через Unica.
 *
 * `tests/cfe/` не входит: юнит-стек демонтирован, каталога нет, а
 * переехавшие e2e-тесты — python, не исходники 1С.
 */
export const SOURCE_PREFIXES: readonly string[] = [
  'src/cf/',
  'src/cfe/',
  'src/epf/',
  'src/erf/',
  'tests/epf/',
]

/** Маркеры корня проекта 1С — имена файлов в каталоге. */
export const PROJECT_MARKERS: readonly string[] = [
  'v8project.yaml',
  'v8project.local.yaml',
  'v8project.test.local.yaml',
]

/** Путь, разрешённый как у python `Path.resolve()`: символические ссылки сняты. */
export function resolvedPath(path: string): string {
  const absolute = resolve(path)
  try {
    return realpathSync.native(absolute)
  } catch {
    // Цели может ещё не быть (write нового файла) — снимаем ссылки с каталога.
  }
  try {
    return join(realpathSync.native(dirname(absolute)), basename(absolute))
  } catch {
    return absolute
  }
}

/**
 * Разобранный путь исходника 1С.
 * @param rel - путь относительно корня дерева (`src/cf/...`).
 * @param root - каталог, в котором лежит `src/` или `tests/`.
 */
export interface SourceTarget {
  readonly rel: string
  readonly root: string
}

/**
 * Найти исходник 1С в пути.
 * @param filePath - путь из аргументов инструмента, абсолютный или от cwd.
 * @param cwd - каталог сессии для относительных путей.
 * @returns путь и корень дерева либо `null`, когда путь не исходник.
 */
export function normalizeSourcePath(filePath: string, cwd: string): SourceTarget | null {
  const absolute = resolvedPath(filePath.startsWith('/') ? filePath : join(cwd, filePath))
  for (const prefix of SOURCE_PREFIXES) {
    const index = absolute.indexOf(`/${prefix}`)
    if (index >= 0) return { rel: absolute.slice(index + 1), root: absolute.slice(0, index) }
  }
  return null
}

/**
 * Документация внутри `src/` — не исходник 1С.
 * @param rel - путь относительно корня дерева.
 * @returns истина для `AGENTS.md` на любом уровне.
 */
export function isAgentsDoc(rel: string): boolean {
  return rel === 'AGENTS.md' || rel.endsWith('/AGENTS.md')
}

/** Проверка «путь существует и это файл». */
function isFile(path: string): boolean {
  try {
    return statSync(path).isFile()
  } catch {
    return false
  }
}

/** Проверка «путь существует и это каталог». */
function isDir(path: string): boolean {
  try {
    return statSync(path).isDirectory()
  } catch {
    return false
  }
}

/**
 * Каталог данных контура проекта: `.dsh/1c` первым, legacy `.zcode/1c` вторым.
 * @param root - корень дерева 1С.
 * @returns каталог, в котором лежит `contour.json`, и его раскладка.
 */
export function contourDir(root: string): { dir: string; layout: 'dsh' | 'zcode' } | null {
  if (isFile(join(root, '.dsh', '1c', 'contour.json'))) {
    return { dir: join(root, '.dsh', '1c'), layout: 'dsh' }
  }
  if (isFile(join(root, '.zcode', '1c', 'contour.json'))) {
    return { dir: join(root, '.zcode', '1c'), layout: 'zcode' }
  }
  return null
}

/** Чтение JSON-объекта; битый или отсутствующий файл — пустой объект. */
export function readJsonObject(path: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, 'utf8'))
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {}
  } catch {
    return {}
  }
}

/**
 * Определить проект контура от каталога сессии.
 *
 * Идём вверх до первого каталога с конфигом контура, маркером v8project или
 * парой «каталог раскладки + `src/`». Конфиг в `.dsh/1c` перекрывает legacy
 * `.zcode/1c` того же дерева: раскладка одна, но обе читаются на переезде.
 * @param start - каталог сессии.
 * @returns проект либо `null`, когда сессия вне проекта 1С.
 */
export function resolveProject(start: string): ContourProject | null {
  let current = resolvedPath(start)
  for (;;) {
    const contour = contourDir(current)
    if (contour !== null) {
      return { root: current, layout: contour.layout, contour: readJsonObject(join(contour.dir, 'contour.json')) }
    }
    if (PROJECT_MARKERS.some((marker) => isFile(join(current, marker)))) {
      return { root: current, layout: 'markers', contour: {} }
    }
    if ((isDir(join(current, '.dsh')) || isDir(join(current, '.zcode'))) && isDir(join(current, 'src'))) {
      return { root: current, layout: 'markers', contour: {} }
    }
    const parent = dirname(current)
    if (parent === current) return null
    current = parent
  }
}
