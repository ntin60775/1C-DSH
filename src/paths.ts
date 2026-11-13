/**
 * Пути контура: исходники 1С, корень проекта, каталоги раскладки.
 *
 * Перенос ZCode-эталона (`contour_common`): то же правило «первое
 * вхождение префикса в абсолютном пути», тот же обход вверх до маркера
 * проекта. Состав префиксов сужен спекой `external-epf-loop` (внешние
 * epf/erf выпали из гейта).
 * @module dsh-1c-contour/paths
 */

import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs'
import { basename, dirname, join, resolve } from 'node:path'

import type { ContourProject } from './contract.js'

/**
 * Каталоги исходников 1С, редактируемые только через Unica.
 *
 * `src/epf/` и `src/erf/` не входят: каркас внешних обработок и отчётов —
 * файлами по `epf-init`/`erf-init` (канонической операции создания на
 * поверхности юники нет), правка узлов — `unica.apply`; сужение —
 * решением оператора, спека `external-epf-loop`. Контроль собранных
 * внешних — roundtrip скриптов и разбор v8-контейнера.
 *
 * `tests/cfe/` не входит: юнит-стек демонтирован, каталога нет, а
 * переехавшие e2e-тесты — python, не исходники 1С. `tests/epf/` остаётся
 * блокируемым: сужение касалось только `src`.
 */
export const SOURCE_PREFIXES: readonly string[] = [
  'src/cf/',
  'src/cfe/',
  'tests/epf/',
]

/** Маркеры корня проекта 1С — имена файлов в каталоге. */
export const PROJECT_MARKERS: readonly string[] = [
  'v8project.yaml',
  'v8project.local.yaml',
  'v8project.test.local.yaml',
]

/**
 * Путь, разрешённый как у python `Path.resolve()`: символические ссылки сняты.
 *
 * Цели может ещё не быть (write нового файла), поэтому разрешается ближайший
 * существующий предок, а остаток пути приписывается как есть. Без этого одно
 * дерево в двух написаниях давало бы разные ключи замка.
 * @param path - путь для разрешения.
 * @returns абсолютный путь без символических ссылок в существующей части.
 */
export function resolvedPath(path: string): string {
  const absolute = resolve(path)
  try {
    return realpathSync.native(absolute)
  } catch {
    // Цели пока нет — разрешаем существующего предка.
  }
  let head = dirname(absolute)
  let tail = basename(absolute)
  for (;;) {
    try {
      return join(realpathSync.native(head), tail)
    } catch {
      const parent = dirname(head)
      if (parent === head) return absolute
      tail = join(basename(head), tail)
      head = parent
    }
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
 * Каталоги раскладки в порядке приоритета для проектного входа.
 *
 * Канон — `.dsh/1c/`: там лежат все входы контура. `.dsh/` без `1c` читается
 * как промежуточная раскладка ранних тикетов. Legacy-раскладки ZCode сняты
 * после перевода парка (Т10): проект читается только в DSH-раскладках.
 */
export const LAYOUT_DIRS: readonly string[] = ['.dsh/1c', '.dsh']

/**
 * Кандидаты пути проектного входа: канон и промежуточная раскладка.
 * @param root - корень дерева 1С.
 * @param relative - путь внутри каталога раскладки.
 * @returns пути в порядке приоритета.
 */
export function layoutCandidates(root: string, relative: string): string[] {
  return LAYOUT_DIRS.map((dir) => join(root, dir, relative))
}

/**
 * Найти проектный вход, читая кандидатов по приоритету.
 *
 * Отсутствие файла — переход к следующему кандидату. Существующий, но не
 * читаемый вход останавливает поиск: молча уступать следующей раскладке
 * нельзя, иначе сломанный канон подменяется другим файлом (для признака
 * контура это означало бы «test» вместо «prod» и разрешённые мутации
 * на проде).
 * @param root - корень дерева 1С.
 * @param relative - путь внутри каталога раскладки.
 * @param read - чтение файла как текста; `null` — файла нет либо он не читается.
 * @returns содержимое (либо `null` для нечитаемого), путь и подпись раскладки.
 */
export function findLayoutFile(
  root: string,
  relative: string,
  read: (path: string) => string | null,
): { text: string | null; path: string; label: string } | null {
  for (const dir of LAYOUT_DIRS) {
    const path = join(root, dir, relative)
    const text = read(path)
    if (text !== null) return { text, path, label: `${dir}/${relative}` }
    if (existsSync(path)) return { text: null, path, label: `${dir}/${relative}` }
  }
  return null
}

/**
 * Каталог раскладки проекта для путей и сообщений гейтов — канон `.dsh/1c`.
 * @returns каталог раскладки DSH.
 */
export function layoutDirOf(): string {
  return LAYOUT_DIRS[0] as string
}

/**
 * Каталог данных контура проекта: канон `.dsh/1c`.
 * @param root - корень дерева 1С.
 * @returns каталог, в котором лежит `contour.json`, и его раскладка.
 */
export function contourDir(root: string): { dir: string; layout: 'dsh' } | null {
  if (isFile(join(root, '.dsh', '1c', 'contour.json'))) {
    return { dir: join(root, '.dsh', '1c'), layout: 'dsh' }
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
 * парой «каталог раскладки + `src/`». Раскладка одна — DSH (`.dsh/1c`).
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
    if (isDir(join(current, '.dsh')) && isDir(join(current, 'src'))) {
      return { root: current, layout: 'markers', contour: {} }
    }
    const parent = dirname(current)
    if (parent === current) return null
    current = parent
  }
}
