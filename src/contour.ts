/**
 * Контур проекта: поверхность Unica, правила пересборки, allowlist эскалаций.
 *
 * Перенос `reference/gates/contour_common.py` и частей
 * `unica_source_gate.py`. Инварианты пересборки остаются ДАННЫМИ
 * (`contour.json` → `unica.rebuild_rules`), а не кодом.
 * @module dsh-1c-contour/contour
 */

import { existsSync, readFileSync, statSync } from 'node:fs'
import { basename, join } from 'node:path'

import type { ContourProject } from './contract.js'
import { normalizeSourcePath } from './paths.js'

/** Имя файла allowlist'а эскалаций внутри каталога раскладки. */
export const ESCALATION_FILE_NAME = 'unica-gate-escalations.txt'

/** Канонические пути раскладки проекта для сообщений гейта. */
export interface ContourPaths {
  /** Путь allowlist'а эскалаций в канонической раскладке. */
  readonly escalationFile: string
  /** Скрипт инициализации ворктри в канонической раскладке. */
  readonly initWorktreeScript: string
}

/**
 * Пути раскладки проекта: новая `.dsh` по умолчанию, legacy `.zcode` пока читается.
 * @param project - найденный проект контура либо `null` вне проекта.
 * @returns пути для сообщений гейта и поиска файлов.
 */
export function contourPaths(project: ContourProject | null): ContourPaths {
  const layout = project?.layout === 'zcode' ? '.zcode' : '.dsh'
  return {
    escalationFile: `${layout}/${ESCALATION_FILE_NAME}`,
    initWorktreeScript: `${layout}/1c/scripts/init_worktree.sh`,
  }
}

// ── поверхность Unica ────────────────────────────────────────────────────────

/** Конфиг поверхности юники: значения по умолчанию для 0.15. */
export interface UnicaTools {
  readonly server: string
  /** Сверочные инструменты: засчитываются как «сверился с источником». */
  readonly docs: readonly string[]
  /** Мутирующие исходники инструменты. */
  readonly mutations: readonly string[]
  /** Инструменты, в аргументах которых живёт операция раннера. */
  readonly op_tools: readonly string[]
  /** Операции раннера, меняющие базу: захватывают замок инфобазы. */
  readonly ib_mutating_ops: readonly string[]
  /** Операции раннера, требующие сверки с документацией (гейт doc). */
  readonly verify_ops: readonly string[]
  /** Правила инвариантов пересборки из `contour.json`. */
  readonly rebuild_rules: readonly RebuildRule[]
}

/** Правило пересборки: требование либо запрет флага операции. */
export interface RebuildRule {
  readonly tool?: string
  readonly op?: string
  /** Селектор наборов: `main`, `extensions`, `any`, имя или список имён. */
  readonly source_sets?: string | readonly string[]
  readonly require?: Readonly<Record<string, unknown>>
  readonly forbid?: Readonly<Record<string, unknown>>
  readonly message?: string
}

/**
 * Значения по умолчанию поверхности Unica 0.15.
 *
 * `upload`/`apply`/`reset` недоступны в 0.15, но остаются в списке операций
 * с замком: возврат проекта на 0.13/0.14 не должен терять защиту базы.
 * Правила пересборки по умолчанию пусты — до сверки на стенде гейт нейтрален.
 */
export const UNICA_TOOLS_DEFAULT: UnicaTools = {
  server: 'unica',
  docs: ['docs', 'search'],
  mutations: ['apply', 'run'],
  op_tools: ['run'],
  ib_mutating_ops: [
    'push', 'upload', 'apply', 'reset',
    'infobase.create', 'infobase.restore', 'infobase.dump', 'launch',
    'extensions.set',
  ],
  verify_ops: ['push', 'upload', 'apply', 'reset'],
  rebuild_rules: [],
}

/** Привести значение из `contour.json` к списку строк. */
function stringList(value: unknown): readonly string[] | null {
  if (!Array.isArray(value)) return null
  return value.filter((item): item is string => typeof item === 'string')
}

/**
 * Конфиг поверхности юники: дефолты 0.15 плюс override из `contour.json`.
 * @param project - проект контура либо `null`.
 * @returns действующая поверхность Unica.
 */
export function unicaTools(project: ContourProject | null): UnicaTools {
  const section = project?.contour['unica']
  if (typeof section !== 'object' || section === null || Array.isArray(section)) {
    return UNICA_TOOLS_DEFAULT
  }
  const merged: Record<string, unknown> = { ...UNICA_TOOLS_DEFAULT }
  for (const [key, value] of Object.entries(section as Record<string, unknown>)) {
    if (!(key in merged)) continue
    const list = stringList(value)
    if (key === 'rebuild_rules' && Array.isArray(value)) merged[key] = value
    else if (list !== null) merged[key] = list
    else if (typeof value === 'string') merged[key] = value
  }
  return merged as unknown as UnicaTools
}

/**
 * Имя MCP-сервера юники в проекте.
 * @param project - проект контура либо `null`.
 * @returns имя сервера, по умолчанию `unica`.
 */
export function unicaServerName(project: ContourProject | null): string {
  const server = unicaTools(project).server
  return typeof server === 'string' && server !== '' ? server : 'unica'
}

/**
 * Короткое имя инструмента MCP: `mcp__<server>__<tool>` → `<tool>`.
 * @param toolName - полное имя инструмента.
 * @param server - имя сервера.
 * @returns короткое имя либо пустая строка.
 */
export function mcpShortName(toolName: string, server: string): string {
  const prefix = `mcp__${server}__`
  return toolName.startsWith(prefix) ? toolName.slice(prefix.length) : ''
}

/**
 * Глагол юники из полного имени инструмента.
 *
 * Живые формы: `mcp__unica__run`, `mcp__plugin_unica_unica__unica.run`.
 * Отрезаем всё до последнего `__`, затем необязательный префикс
 * `unica.`/`unica_`; хост заменяет точки на подчёркивания, поэтому
 * возвращаем точки обратно.
 * @param toolName - полное имя инструмента DSH.
 * @param server - имя MCP-сервера юники.
 * @returns глагол (`run`, `apply`, `task.get`) либо пустая строка.
 */
export function unicaVerb(toolName: string, server: string): string {
  if (!toolName.startsWith('mcp__')) return ''
  const rest = toolName.slice('mcp__'.length)
  const index = rest.lastIndexOf('__')
  if (index <= 0) return ''
  const middle = rest.slice(0, index)
  let short = rest.slice(index + 2)
  if (short === '') return ''
  const parts = middle.split('_')
  if (!parts.includes(server) && !parts.includes('unica')) return ''
  for (const prefix of ['unica.', 'unica_']) {
    if (short.startsWith(prefix)) {
      short = short.slice(prefix.length)
      break
    }
  }
  return short.replaceAll('_', '.')
}

/**
 * Канонические аргументы вызова юники.
 *
 * Две живые формы входа: контракт 0.15 держит глагол наверху, параметры — в
 * `args`; клиент MCP может завернуть всё целиком. Канонический вид — слияние
 * с приоритетом внутреннего `args`; сам контейнер в результат не входит.
 * @param toolInput - аргументы вызова.
 * @returns канонические аргументы.
 */
export function unicaArgs(toolInput: unknown): Record<string, unknown> {
  if (typeof toolInput !== 'object' || toolInput === null || Array.isArray(toolInput)) return {}
  const args = toolInput as Record<string, unknown>
  const inner = args['args']
  if (typeof inner !== 'object' || inner === null || Array.isArray(inner)) return args
  const merged: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(args)) {
    if (key !== 'args') merged[key] = value
  }
  for (const [key, value] of Object.entries(inner as Record<string, unknown>)) merged[key] = value
  return merged
}

/**
 * Операция раннера из аргументов: 0.15 `op`, переходно `operation`.
 * @param args - канонические аргументы вызова.
 * @returns имя операции либо пустая строка.
 */
export function runnerOp(args: Readonly<Record<string, unknown>>): string {
  const op = args['op'] ?? args['operation']
  return typeof op === 'string' ? op : ''
}

// ── v8project.yaml: наборы EXTENSION ─────────────────────────────────────────

/**
 * Source-set'ы типа EXTENSION из `v8project.yaml` — построчный парсер, как в доне.
 * @param tree - дерево, в котором лежит `v8project.yaml`.
 * @returns имена наборов типа EXTENSION.
 */
export function extensionSourceSets(tree: string): ReadonlySet<string> {
  const found = new Set<string>()
  let raw: string
  try {
    raw = readFileSync(join(tree, 'v8project.yaml'), 'utf8')
  } catch {
    return found
  }
  let inSourceSet = false
  let current: string | null = null
  for (const line of raw.split('\n')) {
    const first = line.slice(0, 1)
    if (first !== ' ' && first !== '\t') {
      inSourceSet = line.startsWith('source-set:')
      current = null
      continue
    }
    if (!inSourceSet) continue
    const name = /^\s*-\s*name:\s*(.+?)\s*$/.exec(line)
    if (name?.[1] !== undefined) {
      current = name[1].trim().replace(/^['"]|['"]$/g, '')
      continue
    }
    const type = /^\s*type:\s*(.+?)\s*$/.exec(line)
    if (type?.[1] !== undefined && current !== null) {
      if (type[1].trim().replace(/^['"]|['"]$/g, '').toUpperCase() === 'EXTENSION') found.add(current)
      current = null
    }
  }
  return found
}

/**
 * Подходит ли правило пересборки под набор источников вызова.
 *
 * Селекторы: `null`/`any` — все; `extensions` — EXTENSION-наборы проекта;
 * `main` — основной набор; имя или список имён. Вызов без `sourceSet`
 * считается `main`: правила для main обязаны защищать и такой вызов.
 * @param rule - правило пересборки.
 * @param sourceSet - набор из аргументов вызова.
 * @param extensions - EXTENSION-наборы дерева.
 * @returns истина, когда правило применимо.
 */
export function ruleMatchesSource(rule: RebuildRule, sourceSet: string, extensions: ReadonlySet<string>): boolean {
  const effective = sourceSet === '' ? 'main' : sourceSet
  const selector = rule.source_sets
  if (selector === undefined || selector === 'any') return true
  if (selector === 'extensions') return extensions.has(effective)
  if (selector === 'main') return effective === 'main'
  if (Array.isArray(selector)) return selector.includes(effective)
  return effective === selector
}

/**
 * Сравнить значение аргумента с ожидаемым значением правила.
 *
 * Схема правила может нести не только скаляры: эталон сравнивает структуры
 * python-равенством, поэтому объекты и списки сверяются по содержимому, а не
 * по ссылке (иначе `require` всегда срабатывал бы, а `forbid` — никогда).
 * @param actual - значение из аргументов вызова.
 * @param expected - значение из правила.
 * @returns истина, когда значения равны по содержимому.
 */
function sameValue(actual: unknown, expected: unknown): boolean {
  if (actual === expected) return true
  if (typeof actual !== 'object' || actual === null) return false
  return JSON.stringify(actual) === JSON.stringify(expected)
}

/**
 * Нарушено ли правило пересборки.
 * @param rule - правило из `contour.json`.
 * @param short - глагол юники.
 * @param args - канонические аргументы вызова.
 * @param callCwd - дерево, из которого вызвана операция.
 * @returns сообщение нарушения либо `null`.
 */
export function violatedRebuildRule(
  rule: RebuildRule,
  short: string,
  args: Readonly<Record<string, unknown>>,
  callCwd: string,
): string | null {
  if ((rule.tool ?? '') !== short) return null
  if (rule.op !== undefined && runnerOp(args) !== rule.op) return null
  const sourceSet = args['sourceSet']
  const extensions = extensionSourceSets(callCwd)
  if (!ruleMatchesSource(rule, typeof sourceSet === 'string' ? sourceSet : '', extensions)) return null
  for (const [key, value] of Object.entries(rule.require ?? {})) {
    if (!sameValue(args[key], value)) return rule.message ?? `правило пересборки: требуется ${key}=${JSON.stringify(value)}`
  }
  for (const [key, value] of Object.entries(rule.forbid ?? {})) {
    if (sameValue(args[key], value)) return rule.message ?? `правило пересборки: ${key}=${JSON.stringify(value)} запрещено`
  }
  return null
}

// ── ворктри ──────────────────────────────────────────────────────────────────

/**
 * Каталог — git-ворктри: `.git` является файлом, а не каталогом.
 * @param directory - проверяемый каталог.
 * @returns истина для рабочего дерева `git worktree`.
 */
export function isWorktree(directory: string): boolean {
  try {
    return statSync(join(directory, '.git')).isFile()
  } catch {
    return false
  }
}

/**
 * Отсутствующие элементы инициализации воркспейса в ворктри.
 * @param directory - каталог ворктри.
 * @returns имена отсутствующих элементов.
 */
export function missingWorktreeWorkspace(directory: string): readonly string[] {
  const missing: string[] = []
  const hasFile = (path: string): boolean => {
    try {
      return statSync(path).isFile()
    } catch {
      return false
    }
  }
  const hasDir = (path: string): boolean => {
    try {
      return statSync(path).isDirectory()
    } catch {
      return false
    }
  }
  if (!hasFile(join(directory, 'v8project.local.yaml'))) missing.push('v8project.local.yaml')
  if (!hasDir(join(directory, 'build', 'tools'))) missing.push('build/tools/')
  return missing
}

// ── allowlist эскалаций ──────────────────────────────────────────────────────

/** Разбор allowlist'а эскалаций для блокирующего сообщения. */
export interface EscalationReport {
  /** Файл найден хоть в одном из деревьев. */
  readonly found: boolean
  /** Строки, совпавшие с целью (путь в относительной форме). */
  readonly matched: readonly string[]
  /** Почему остальные строки не подошли. */
  readonly report: readonly string[]
  /** Дерево, из которого прочитан файл. */
  readonly treeRoot: string
  /** Метка файла для сообщений: раскладка, в которой он найден. */
  readonly label: string
}

/**
 * Прочитать allowlist эскалаций для дерева цели.
 *
 * Порядок поиска: дерево цели, затем каталог сессии (ворктри против
 * основного дерева). Внутри дерева — `.dsh` первым, legacy `.zcode` вторым.
 * Неподошедшие строки не выбрасываются молча: они попадают в разбор.
 * @param targetRoot - дерево целевого файла.
 * @param sessionCwd - каталог сессии.
 * @returns найденные совпадения и разбор.
 */
export function escalationEntries(targetRoot: string, sessionCwd: string): EscalationReport {
  for (const root of [targetRoot, sessionCwd]) {
    for (const layout of ['.dsh', '.zcode']) {
      const path = join(root, layout, ESCALATION_FILE_NAME)
      let raw: string
      try {
        raw = readFileSync(path, 'utf8')
      } catch {
        // Файла нет — смотрим следующую раскладку. Файл есть, но не читается —
        // это несовпадение, а не отсутствие: молча уступать legacy-файлу нельзя.
        if (!existsSync(path)) continue
        return {
          found: true,
          matched: [],
          report: [`файл эскалаций не прочитан: ${path}`],
          treeRoot: root,
          label: `${layout}/${ESCALATION_FILE_NAME}`,
        }
      }
      const matched: string[] = []
      const report: string[] = []
      for (const line of raw.split('\n')) {
        const trimmed = line.trim()
        if (trimmed === '' || trimmed.startsWith('#')) continue
        if (trimmed.includes(' — ') || trimmed.includes(' – ')) {
          report.push(`"${trimmed}" — путь и причина одной строкой; причина перенеси в #-комментарий строкой выше`)
          continue
        }
        if (trimmed.includes(' #')) {
          report.push(`"${trimmed}" — причина в конце строки; перенеси в #-комментарий строкой выше`)
          continue
        }
        const normalized = normalizeSourcePath(trimmed, root)
        if (normalized === null) {
          report.push(`"${trimmed}" — строка не резолвится в исходник дерева`)
        } else if (trimmed !== normalized.rel) {
          report.push(`"${trimmed}" — не относительный путь от корня дерева; укажи относительную форму, как в поле «Файл:» выше`)
        } else {
          matched.push(trimmed)
        }
      }
      return { found: true, matched, report, treeRoot: root, label: `${layout}/${ESCALATION_FILE_NAME}` }
    }
  }
  return { found: false, matched: [], report: [], treeRoot: targetRoot, label: ESCALATION_FILE_NAME }
}

/**
 * Путь allowlist'а в аргументах инструмента — правка файла журналируется.
 * @param filePath - путь из аргументов.
 * @param cwd - каталог сессии.
 * @returns имя файла, если правится allowlist, иначе `null`.
 */
export function escalationEdit(filePath: string, cwd: string): string | null {
  const absolute = filePath.startsWith('/') ? filePath : join(cwd, filePath)
  return absolute.endsWith(`/${ESCALATION_FILE_NAME}`) || basename(absolute) === ESCALATION_FILE_NAME
    ? ESCALATION_FILE_NAME
    : null
}
