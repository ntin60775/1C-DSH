/**
 * `bsl_style_gate` на DSH: стиль BSL изменённых строк по паку.
 *
 * Порт ZCode-гейта `bsl_style_gate`. Единственный исполняемый
 * авторитет формы BSL — пак `1c-bsl-code-style`; гейт прогоняет чекер и
 * блокирует правку, если нарушения стоят в изменённых строках. Чужой старый
 * код остаётся фоном: он не блокирует и уходит модели отдельной заметкой.
 *
 * Два канала правки:
 *   1. `write` / `edit` / `apply_patch` — прямые правки (эскалации
 *      `unica-source-gate`);
 *   2. `unica.apply` — штатный канал: план несёт тексты ops в тела модулей
 *      (`at` заканчивается `.Body`), предварительный снипет-чек даёт
 *      advisory ещё до записи, а после исполнения проверяются файлы, где
 *      нашёлся текст плана.
 *
 * Чекер — порт `GateState.styleChecker`: гейт не знает, движок пака это или
 * скрипт, и не запускает процессы сам.
 * @module dsh-1c-contour/gates/bsl-style
 */

import { readFileSync } from 'node:fs'
import { relative, sep } from 'node:path'

import type { ContourProject, ExecutedCall, GateCall, GateState, PostDecision } from '../contract.js'
import { unicaArgs, unicaServerName, unicaVerb } from '../contour.js'
import { bslFilesUnder, bslTargets, changedLines, planBslTexts, type Span, spanOfNewText } from './edits.js'

/** Имя правила в журнале решений. */
export const RULE = 'bsl-style-gate'

/** Время жизни незакрытого плана `unica.apply` — как у замка ИБ. */
export const BRIDGE_TTL_SECONDS = 20 * 60

/** Ключ памяти сессии с планами `unica.apply`. */
const KEY_PLANS = 'style:plans'

/** Строка отчёта чекера: `путь:строка: пункт`. */
const CHECKER_LINE_RE = /^(.+?):(\d+): (.+)$/

/** Сохранённый план `unica.apply`. */
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
 * Незакрытые планы сессии — свежие по TTL.
 * @param state - память сессии и часы.
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
 * Разделить вывод чекера на нарушения в правке и фон.
 *
 * Нераспознанная строка отчёта считается важной: молча терять вывод чекера
 * нельзя.
 * @param report - вывод чекера.
 * @param targets - проверенные файлы.
 * @param spansByTarget - диапазоны изменённых строк по файлам.
 * @returns нарушения правки и фон.
 */
export function splitReport(
  report: string,
  targets: readonly string[],
  spansByTarget: ReadonlyMap<string, Span[] | null>,
): { hot: string[]; cold: string[] } {
  const hot: string[] = []
  const cold: string[] = []
  for (const line of report.split('\n')) {
    const trimmed = line.trim()
    const match = CHECKER_LINE_RE.exec(trimmed)
    let assigned = false
    if (match !== null) {
      const path = match[1] as string
      const number = Number(match[2])
      for (const target of targets) {
        if (!(trimmed.startsWith(`${target}:`) || path.endsWith(`/${target}`))) continue
        const spans = spansByTarget.get(target) ?? null
        if (spans === null || spans.some(([from, to]) => from <= number && number <= to)) hot.push(line)
        else cold.push(line)
        assigned = true
        break
      }
    }
    if (!assigned) hot.push(line)
  }
  return { hot, cold }
}

/**
 * Найти файлы и диапазоны, где стоит текст сохранённых планов.
 *
 * Совпадение в нескольких файлах — все проверяются (объединение, безопасное
 * направление); ненайденные планы остаются незакрытыми.
 * @param root - корень дерева.
 * @param plans - планы сессии.
 * @returns цели, диапазоны и закрытые планы.
 */
export function locatePlans(
  root: string,
  plans: readonly Plan[],
): { targets: string[]; spansByTarget: Map<string, Span[]>; matched: Plan[] } {
  const contents = new Map<string, string>()
  for (const path of bslFilesUnder(root)) {
    try {
      contents.set(path, readFileSync(path, 'utf8'))
    } catch {
      continue
    }
  }
  const targets: string[] = []
  const spansByTarget = new Map<string, Span[]>()
  const matched: Plan[] = []
  for (const plan of plans) {
    let found = false
    for (const text of plan.texts) {
      if (text === '') continue
      for (const [path, content] of contents) {
        const span = spanOfNewText(content, text)
        if (span === null) continue
        const rel = relative(root, path).split(sep).join('/')
        if (!spansByTarget.has(rel)) {
          spansByTarget.set(rel, [])
          targets.push(rel)
        }
        spansByTarget.get(rel)?.push(span)
        found = true
      }
    }
    if (found) matched.push(plan)
  }
  return { targets, spansByTarget, matched }
}

/**
 * Вердикт по отчёту чекера — общий для обоих каналов.
 * @param report - вывод чекера.
 * @param targets - проверенные файлы.
 * @param spansByTarget - диапазоны изменённых строк.
 * @param action - действие для журнала.
 * @param origin - канал правки для сообщения.
 * @returns `accept` с заметкой либо `block` с отчётом.
 */
export function verdict(
  report: string,
  targets: readonly string[],
  spansByTarget: ReadonlyMap<string, Span[] | null>,
  action: string,
  origin: string,
  state: GateState,
): PostDecision {
  const { hot, cold } = splitReport(report, targets, spansByTarget)
  if (hot.length === 0 && cold.length > 0) {
    state.audit(RULE, action, `нарушения вне изменённых строк (${cold.length}) — не блокируют`, 'advisory')
    return {
      kind: 'accept',
      notice: `Стайл-чек: ${cold.length} нарушений в файле вне твоей правки `
        + '(старый код — не блокируют, чинить при случае):\n' + cold.join('\n'),
    }
  }
  const blocks = hot.length > 0 ? hot : report.split('\n')
  const header = [
    'БЛОК: правка нарушает стиль BSL (пак 1c-bsl-code-style — приоритетный',
    `авторитет формы; канал: ${origin}).`,
    'Исправь перечисленные строки и повтори правку.',
  ]
  const ids = [...new Set(blocks
    .map((line) => CHECKER_LINE_RE.exec(line.trim()))
    .filter((match): match is RegExpExecArray => match !== null)
    .map((match) => `${(match[3] as string).split(']')[0] ?? ''}]`))]
    .sort()
  if (ids.length > 0) {
    header.push(`Машиннофиксируемые пункты: ${ids.join(', ')} — чекер чинит их с --fix при праве на запись.`)
  }
  if (cold.length > 0) header.push(`Вне твоей правки (не блокируют, но есть): ${cold.length} наруш.`)
  state.audit(RULE, action, `нарушения стиля в изменённых строках (${blocks.length})`)
  return { kind: 'block', feedback: [...header, '', ...blocks].join('\n') }
}

/**
 * Фаза плана: запомнить тексты и дать advisory-чек до записи.
 * @param call - вызов `unica.apply` с планом.
 * @param project - проект контура.
 * @param state - память и журнал.
 * @returns `accept` с заметкой, если снипет-чек нашёл нарушения.
 */
async function handlePlan(call: GateCall, project: ContourProject, state: GateState): Promise<PostDecision> {
  const args = unicaArgs(call.args)
  const label = `unica.apply план ${String(args['at'] ?? '')}`.trim()
  const texts = planBslTexts(args)
  if (texts.length === 0) {
    state.audit(RULE, label, 'ops вне тел BSL-модулей — стиль не проверяется', 'advisory')
    return { kind: 'accept' }
  }
  const plans = loadPlans(state)
  plans.push({ savedAt: state.nowSeconds, expiresAt: state.nowSeconds + BRIDGE_TTL_SECONDS, texts })
  savePlans(state, plans)
  state.audit(RULE, label, `план сохранён (${texts.length} BSL-ops, TTL ${BRIDGE_TTL_SECONDS / 60} мин)`, 'advisory')
  const checker = state.styleChecker
  if (checker === undefined) return { kind: 'accept' }
  const warn = await checker.snippet(texts, project.root)
  if (warn === null || warn === '') return { kind: 'accept' }
  return {
    kind: 'accept',
    notice: [
      'Предварительный стайл-чек текста правки unica.apply (до записи, не блокирует:',
      'текст без контекста модуля; после исполнения проверка повторится по факту):',
      '',
      warn,
    ].join('\n'),
  }
}

/**
 * Фаза исполнения: проверить файлы, где нашёлся текст планов.
 * @param project - проект контура.
 * @param state - память и журнал.
 * @returns решение по отчёту чекера.
 */
async function handleExecute(project: ContourProject, state: GateState): Promise<PostDecision> {
  const plans = loadPlans(state)
  if (plans.length === 0) return { kind: 'accept' }
  const { targets, spansByTarget, matched } = locatePlans(project.root, plans)
  if (targets.length === 0) {
    state.audit(RULE, 'unica.apply исполнение', 'тексты сохранённых планов не найдены в .bsl — пропуск (планы оставлены)', 'advisory')
    return { kind: 'accept' }
  }
  const checker = state.styleChecker
  if (checker === undefined) return { kind: 'accept' }
  const report = await checker.run(targets, project.root)
  if (report === null) return { kind: 'accept' }
  savePlans(state, plans.filter((plan) => !matched.includes(plan)))
  if (report === '') return { kind: 'accept' }
  return verdict(report, targets, spansByTarget, `unica.apply: ${targets.join(', ')}`, 'unica.apply', state)
}

/**
 * Решение после вызова.
 * @param executed - вызов и его результат.
 * @param project - проект контура.
 * @param state - память, журнал и порт чекера.
 * @returns `accept` (возможно с заметкой) либо `block`.
 */
export async function evaluatePost(
  executed: ExecutedCall,
  project: ContourProject | null,
  state: GateState,
): Promise<PostDecision> {
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
  if (targets.length === 0) return { kind: 'accept' }
  const checker = state.styleChecker
  if (checker === undefined) return { kind: 'accept' }
  const report = await checker.run(targets, project.root)
  if (report === null || report === '') return { kind: 'accept' }
  const spansByTarget = new Map<string, Span[] | null>()
  for (const target of targets) spansByTarget.set(target, changedLines(call, target, project.root))
  return verdict(report, targets, spansByTarget, targets.join(', '), 'прямая правка write/edit/apply_patch', state)
}
