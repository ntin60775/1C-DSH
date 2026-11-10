/**
 * `bsl_module_gate` на DSH: контракт носителя 1С-кода.
 *
 * Порт `reference/gates/bsl_module_gate.py`. Контекст исполнения модуля
 * задаёт ТИП НОСИТЕЛЯ, а не директивы; у вендорского легаси своя правда, наш
 * слой так не пишет:
 *
 *   1. директивы компиляции в `**\/CommonModules/*\/Ext/Module.bsl`;
 *   2. те же директивы в `**\/Ext/ManagerModule.bsl`;
 *   3. `Перем` на верхнем уровне модуля — разделяемое скрытое состояние;
 *   4. права в `**\/Roles/*\/Ext/Rights.xml` на перечисления, общие картинки
 *      и общие макеты.
 *
 * Блокируются только нарушения в изменённых строках; `Rights.xml`
 * проверяется целиком — запретный класс по парку не встречается.
 *
 * Эскалация — `.dsh/bsl-module-gate-escalations.txt` (legacy `.zcode`):
 * строка «`<rule-id>:<подстрока пути>`» либо «`<подстрока пути>`».
 * @module dsh-1c-contour/gates/bsl-module
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import type { ContourProject, ExecutedCall, GateCall, GateState, PostDecision } from '../contract.js'
import { unicaArgs, unicaServerName, unicaVerb } from '../contour.js'
import { normalizeSourcePath } from '../paths.js'
import { bslTargets, changedLines, freshBslFiles, planBslTexts, rawPathsOf, type Span, spanOfNewText } from './edits.js'

/** Имя правила в журнале решений. */
export const RULE = 'bsl-module-gate'

/** Файл эскалаций в каталоге раскладки. */
const ESCALATION_FILE = 'bsl-module-gate-escalations.txt'

/** Время жизни незакрытого плана `unica.apply` — как у замка ИБ. */
export const BRIDGE_TTL_SECONDS = 20 * 60

/** Запас на расхождение часов файловой системы при поиске свежих файлов. */
export const MTIME_SLACK_MS = 60 * 1000

/** Ключ памяти сессии с планами `unica.apply`. */
const KEY_PLANS = 'module:plans'

const DIRECTIVE_RE = /^&\s*(НаКлиенте|НаСервере|НаСервереБезКонтекста|НаКлиентеНаСервере|НаКлиентеНаСервереБезКонтекста)\s*$/i
const PEREM_RE = /^Перем\s+\S/i
const METHOD_START_RE = /^(Процедура|Функция)\s/i
const METHOD_END_RE = /^(КонецПроцедуры|КонецФункции)\s*$/i

const COMMON_MODULE_RE = /(^|\/)CommonModules\/[^/]+\/Ext\/Module\.bsl$/
const MANAGER_MODULE_RE = /\/Ext\/ManagerModule\.bsl$/
const RIGHTS_RE = /(^|\/)Roles\/[^/]+\/Ext\/Rights\.xml$/

const FORBIDDEN_RIGHT_PREFIXES = ['Enum.', 'CommonPicture.', 'CommonTemplate.']
const OBJECT_RE = /<object>\s*<name>([^<]+)<\/name>(.*?)<\/object>/gs
const RIGHT_TRUE_RE = /<right>\s*<name>[^<]+<\/name>\s*<value>\s*true\s*<\/value>/s

/** Нормы правил — текст для модели. */
export const RULE_NORMS: Readonly<Record<string, string>> = {
  'directive-in-common-module':
    'директивы компиляции в общем модуле не используются — доступность задают '
    + 'флаги (Клиент/Сервер/ВызовСервера); код из модуля формы переносится '
    + 'телами, без директив',
  'directive-in-manager-module':
    'директивы в модуле менеджера не используются — контекст исполнения задаёт '
    + 'тип носителя (сервер)',
  'module-level-variable':
    'переменные модуля на верхнем уровне — разделяемое скрытое состояние; '
    + 'состояние передаётся параметрами',
  'role-forbidden-rights':
    'права ролей на перечисления, общие картинки и общие макеты не назначаются никогда',
}

/** Нарушение: файл, строка, правило и попадание в правку. */
interface Entry {
  readonly file: string
  readonly line: number
  readonly rule: string
  readonly hot: boolean
}

/** План `unica.apply`. */
interface Plan {
  readonly savedAt: number
  readonly expiresAt: number
  readonly texts: readonly string[]
}

/** Аргументы вызова как словарь. */
function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

/**
 * Правило, которое нарушает строка носителя.
 * @param rel - путь файла.
 * @param kind - вид нарушения для верхнеуровневой переменной.
 * @returns идентификатор правила.
 */
export function ruleIdFor(rel: string, kind: 'directive' | 'module-level-variable'): string {
  if (kind === 'module-level-variable') return 'module-level-variable'
  if (COMMON_MODULE_RE.test(rel)) return 'directive-in-common-module'
  return 'directive-in-manager-module'
}

/**
 * Нарушения носителя в тексте модуля.
 * @param rel - путь файла.
 * @param text - текст модуля.
 * @param spans - диапазоны изменённых строк; `null` — весь файл.
 * @returns найденные нарушения.
 */
export function checkBsl(rel: string, text: string, spans: readonly Span[] | null): Entry[] {
  if (!COMMON_MODULE_RE.test(rel) && !MANAGER_MODULE_RE.test(rel)) return []
  const found: Entry[] = []
  let depth = 0
  const lines = text.split('\n')
  for (let index = 0; index < lines.length; index += 1) {
    const number = index + 1
    const stripped = (lines[index] as string).trim()
    if (stripped.startsWith('//')) continue
    const inSpan = spans === null || spans.some(([from, to]) => from <= number && number <= to)
    if (DIRECTIVE_RE.test(stripped)) {
      found.push({ file: rel, line: number, rule: ruleIdFor(rel, 'directive'), hot: inSpan })
    } else if (depth === 0 && PEREM_RE.test(stripped)) {
      found.push({ file: rel, line: number, rule: 'module-level-variable', hot: inSpan })
    }
    if (METHOD_START_RE.test(stripped)) depth += 1
    else if (METHOD_END_RE.test(stripped)) depth = Math.max(0, depth - 1)
  }
  return found
}

/**
 * Права ролей на метаданные, на которые права не назначаются никогда.
 * @param text - текст `Rights.xml`.
 * @returns имена объектов.
 */
export function checkRights(text: string): string[] {
  const found: string[] = []
  OBJECT_RE.lastIndex = 0
  for (const match of text.matchAll(OBJECT_RE)) {
    const name = match[1] as string
    if (!FORBIDDEN_RIGHT_PREFIXES.some((prefix) => name.startsWith(prefix))) continue
    if (RIGHT_TRUE_RE.test(match[2] as string)) found.push(name)
  }
  return found
}

/**
 * Эскалации носителя: пары «правило, подстрока пути».
 * @param project - проект контура.
 * @returns список исключений; пусто — файла нет.
 */
export function escalationSkips(project: ContourProject | null): [string | null, string][] {
  if (project === null) return []
  for (const layout of ['.dsh', '.zcode']) {
    let text: string
    try {
      text = readFileSync(join(project.root, layout, ESCALATION_FILE), 'utf8')
    } catch {
      continue
    }
    const out: [string | null, string][] = []
    for (const raw of text.split('\n')) {
      const line = raw.trim()
      if (line === '' || line.startsWith('#')) continue
      const separator = line.indexOf(':')
      if (separator >= 0) out.push([line.slice(0, separator).trim(), line.slice(separator + 1).trim()])
      else out.push([null, line])
    }
    return out
  }
  return []
}

/**
 * Подпадает ли нарушение под эскалацию.
 * @param skips - список исключений.
 * @param rel - путь файла.
 * @param rule - правило нарушения.
 * @returns истина, когда нарушение исключено.
 */
export function isEscalated(skips: readonly [string | null, string][], rel: string, rule: string): boolean {
  return skips.some(([escRule, needle]) => (escRule === null || escRule === rule) && rel.includes(needle))
}

/**
 * Вердикт по нарушениям носителя.
 * @param entries - найденные нарушения.
 * @param project - проект контура.
 * @param action - действие для журнала.
 * @param origin - канал правки для сообщения.
 * @param state - журнал.
 * @returns `accept` с заметкой либо `block`.
 */
export function verdict(
  entries: readonly Entry[],
  project: ContourProject,
  action: string,
  origin: string,
  state: GateState,
): PostDecision {
  if (entries.length === 0) return { kind: 'accept' }
  const skips = escalationSkips(project)
  const hot = entries.filter((entry) => entry.hot && !isEscalated(skips, entry.file, entry.rule))
  const background = entries.filter((entry) => !(entry.hot && !isEscalated(skips, entry.file, entry.rule)))
  if (hot.length === 0) {
    state.audit(RULE, action, `нарушения носителя вне правки/под эскалациями (${background.length})`, 'advisory')
    return {
      kind: 'accept',
      notice: `Гейт носителя: ${background.length} нарушений вне твоей правки `
        + '(старый код или эскалация — не блокируют, чинить при случае):\n'
        + background.map((entry) => `${entry.file}:${entry.line}: [${entry.rule}] ${RULE_NORMS[entry.rule] ?? ''}`).join('\n'),
    }
  }
  const lines = hot.map((entry) => `${entry.file}:${entry.line}: [${entry.rule}] ${RULE_NORMS[entry.rule] ?? ''}`)
  const rules = [...new Set(hot.map((entry) => entry.rule))].sort()
  const header = [
    `БЛОК: правка нарушает контракт носителя (гейт bsl-module-gate, канал: ${origin}).`,
    `Норма контура: ${rules.map((rule) => RULE_NORMS[rule] ?? '').join('; ')}.`,
    'Исправь перечисленные строки и повтори правку.',
    `Осознанное исключение — .dsh/${ESCALATION_FILE}: строка «<rule-id>:<подстрока пути>» или «<подстрока пути>».`,
  ]
  state.audit(RULE, action, `нарушения носителя в изменённых строках (${hot.length})`)
  return { kind: 'block', feedback: [...header, '', ...lines].join('\n') }
}

/**
 * Прямые правки: файл читается с диска, проверяются изменённые строки.
 * @param call - вызов инструмента.
 * @param project - проект контура.
 * @param state - журнал.
 * @returns решение по нарушениям носителя.
 */
function handleDirect(call: GateCall, project: ContourProject, state: GateState): PostDecision {
  const entries: Entry[] = []
  for (const raw of rawPathsOf(call)) {
    const target = normalizeSourcePath(raw, call.cwd)
    if (target === null) continue
    const normalized = target.rel
    let text: string
    try {
      text = readFileSync(join(project.root, normalized), 'utf8')
    } catch {
      continue
    }
    if (RIGHTS_RE.test(normalized)) {
      for (const _name of checkRights(text)) {
        entries.push({ file: normalized, line: 0, rule: 'role-forbidden-rights', hot: true })
      }
      continue
    }
    const spans = changedLines(call, normalized, project.root)
    entries.push(...checkBsl(normalized, text, spans))
  }
  const action = [...new Set(entries.map((entry) => entry.file))].sort().join(', ') || '-'
  return verdict(entries, project, action, 'прямая правка write/edit/apply_patch', state)
}

/**
 * Незакрытые планы сессии — свежие по TTL.
 * @param state - память сессии.
 * @returns планы в порядке сохранения.
 */
export function loadPlans(state: GateState): Plan[] {
  const plans = state.memory.get<Plan[]>(KEY_PLANS) ?? []
  return plans.filter((plan) => plan.expiresAt > state.nowSeconds)
}

/**
 * Сохранить планы; пустой список очищает память.
 * @param state - память сессии.
 * @param plans - планы к сохранению.
 */
export function savePlans(state: GateState, plans: readonly Plan[]): void {
  state.memory.set(KEY_PLANS, [...plans])
}

/**
 * Фаза плана: запомнить тексты правок тел модулей.
 * @param call - вызов `unica.apply` с планом.
 * @param project - проект контура.
 * @param state - память и журнал.
 * @returns `accept` (правила зависят от носителя и проверяются после записи).
 */
function handlePlan(call: GateCall, project: ContourProject, state: GateState): PostDecision {
  const args = unicaArgs(call.args)
  const label = `unica.apply план ${String(args['at'] ?? '')}`.trim()
  const texts = planBslTexts(args)
  if (texts.length === 0) {
    state.audit(RULE, label, 'ops вне тел BSL-модулей — носитель не проверяется', 'advisory')
    return { kind: 'accept' }
  }
  const plans = loadPlans(state)
  plans.push({ savedAt: state.nowSeconds, expiresAt: state.nowSeconds + BRIDGE_TTL_SECONDS, texts })
  savePlans(state, plans)
  state.audit(RULE, label, `план сохранён (${texts.length} BSL-ops)`, 'advisory')
  return { kind: 'accept' }
}

/**
 * Фаза исполнения: проверить свежие файлы, где нашёлся текст планов.
 * @param project - проект контура.
 * @param state - память и журнал.
 * @returns решение по нарушениям носителя.
 */
function handleExecute(project: ContourProject, state: GateState): PostDecision {
  const plans = loadPlans(state)
  if (plans.length === 0) return { kind: 'accept' }
  const oldest = Math.min(...plans.map((plan) => plan.savedAt))
  const fresh = freshBslFiles(project.root, oldest * 1000, MTIME_SLACK_MS)
  const entries: Entry[] = []
  const matched: Plan[] = []
  for (const plan of plans) {
    let found = false
    for (const text of plan.texts) {
      if (text === '') continue
      for (const rel of fresh) {
        let body: string
        try {
          body = readFileSync(join(project.root, rel), 'utf8')
        } catch {
          // Файл исчез после обхода — проверять нечего.
          continue
        }
        const span = spanOfNewText(body, text)
        if (span === null) continue
        found = true
        entries.push(...checkBsl(rel, body, [span]))
      }
    }
    if (found) matched.push(plan)
  }
  if (entries.length === 0 && matched.length === 0) {
    state.audit(RULE, 'unica.apply исполнение', 'тексты планов не найдены среди свежих файлов — планы оставлены', 'advisory')
    return { kind: 'accept' }
  }
  savePlans(state, plans.filter((plan) => !matched.includes(plan)))
  return verdict(entries, project, 'unica.apply', 'unica.apply', state)
}

/**
 * Решение после вызова.
 * @param executed - вызов и его результат.
 * @param project - проект контура.
 * @param state - память и журнал.
 * @returns `accept` (возможно с заметкой) либо `block`.
 */
export function evaluatePost(
  executed: ExecutedCall,
  project: ContourProject | null,
  state: GateState,
): PostDecision {
  if (project === null) return { kind: 'accept' }
  if (executed.denied) return { kind: 'accept' }
  const call = executed.call

  const short = unicaVerb(call.tool, unicaServerName(project))
  if (short !== '') {
    if (short !== 'apply') return { kind: 'accept' }
    const args = unicaArgs(call.args)
    if ('executionToken' in args) return handleExecute(project, state)
    if (Array.isArray(args['ops'])) return handlePlan(call, project, state)
    return { kind: 'accept' }
  }

  if (!['write', 'edit', 'apply_patch'].includes(call.tool)) return { kind: 'accept' }
  const targets = bslTargets(call, call.cwd)
  const rightsTargets = rawPathsOf(call)
    .map((raw) => normalizeSourcePath(raw, call.cwd)?.rel ?? null)
    .filter((rel): rel is string => rel !== null && RIGHTS_RE.test(rel))
  if (targets.length === 0 && rightsTargets.length === 0) return { kind: 'accept' }
  return handleDirect(call, project, state)
}
