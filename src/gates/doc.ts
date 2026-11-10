/**
 * `doc_gate` на DSH: сначала документация платформы, потом решение.
 *
 * Порт `reference/gates/doc_gate.py`. Исходники говорят, КАК сделано, но не
 * говорят, ЧТО допустимо платформой, поэтому мутация исходников требует
 * непустого документационного вызова в этой же сессии. Структурная мутация
 * сбрасывает флаг сверки, серия правок кода (`code.*`) — нет. Публикация
 * плана и исполнение прогона тратят сверку; preview и `dryRun` требуют её,
 * но не тратят.
 *
 * Состояние живёт в памяти сессии (эталон хранил его файлом на сессию):
 * `doc:verified`, `doc:pending`, `doc:tasks`.
 * @module dsh-1c-contour/gates/doc
 */

import type { ContourProject, ExecutedCall, GateCall, GateState, PostDecision, PreDecision } from '../contract.js'
import { unicaServerName, unicaTools, unicaVerb } from '../contour.js'
import { SOURCE_PREFIXES } from '../paths.js'

/** Имя правила в журнале решений. */
export const RULE = 'docs-before-design'

/** Приставка операций, правящих код: серия таких правок флаг не сбрасывает. */
const CODE_OP_RE = /^code\./i

/** Инструменты правки файла. */
const FILE_TOOLS = new Set(['write', 'edit', 'apply_patch'])

/** Ключи памяти сессии. */
const KEY_VERIFIED = 'doc:verified'
const KEY_PENDING = 'doc:pending'
const KEY_TASKS = 'doc:tasks'

/** Причина отказа — текст канона. */
export const BLOCK_REASON = [
  'Мутация 1С-исходников без сверки с документацией платформы (docs-before-design).',
  '',
  'Память — не источник: у памяти нет версии, а у платформы есть. Перед решением',
  'о механизме сверься с документацией (один вызов, недорого):',
  '',
  '  - unica.docs {source: platform-help | development-standard} — справка',
  '    платформы и стандарты разработки;',
  '  - unica.search — поиск по конфигурации как дополнение.',
  '',
  'Источник configuration-documentation в 0.15 отвечает unsupported_source:',
  'это граница поверхности, а не ответ. Ответ задачи (status: working) тоже',
  'не считается сверкой, пока не дочитан через unica.task.result. Поиск без',
  'совпадений сверкой не считается.',
  '',
  'Сверка исходниками (unica.view, grep по src/) отвечает на вопрос «как',
  'сделано здесь», но не «что допустимо платформой». После структурной',
  'мутации флаг сбрасывается: следующее структурное решение — снова свежая',
  'сверка.',
].join('\n')

/** Аргументы вызова как словарь. */
function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

/** Состояние сверки сессии. */
interface DocState {
  verified: boolean
  pending: boolean
  tasks: Record<string, number>
}

/**
 * Прочитать состояние сверки из памяти сессии.
 * @param state - состояние гейта.
 * @returns флаг сверки, ожидание ответа и принятые задачи.
 */
export function loadState(state: GateState): DocState {
  return {
    verified: state.memory.get<boolean>(KEY_VERIFIED) === true,
    pending: state.memory.get<boolean>(KEY_PENDING) === true,
    tasks: state.memory.get<Record<string, number>>(KEY_TASKS) ?? {},
  }
}

/** Записать состояние сверки в память сессии. */
function saveState(state: GateState, next: DocState): void {
  state.memory.set(KEY_VERIFIED, next.verified)
  state.memory.set(KEY_PENDING, next.pending)
  state.memory.set(KEY_TASKS, next.tasks)
}

/** Узлы ответа, где может лежать содержимое: корень, data, result. */
function* contentNodes(payload: unknown): Generator<Record<string, unknown>> {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return
  yield payload as Record<string, unknown>
  for (const key of ['data', 'result']) {
    const node = (payload as Record<string, unknown>)[key]
    if (typeof node === 'object' && node !== null && !Array.isArray(node)) {
      yield node as Record<string, unknown>
    }
  }
}

/** Осмысленность по секциям: `null` — форму распознать не удалось. */
function sectionsMeaningful(payload: unknown): boolean | null {
  for (const node of contentNodes(payload)) {
    const sections = node['sections']
    if (!Array.isArray(sections)) continue
    let sawSections = false
    for (const section of sections) {
      if (typeof section !== 'object' || section === null || Array.isArray(section)) continue
      sawSections = true
      const status = String((section as Record<string, unknown>)['status'] ?? '').toLowerCase()
      if (status !== 'ok') continue
      const hits = (section as Record<string, unknown>)['hits']
      if (Array.isArray(hits) && hits.length > 0) return true
    }
    return sawSections ? false : null
  }
  return null
}

/** `'task'` — принятая задача, `'error'` — отказ/пустота, `null` — ответ. */
function nonAnswer(payload: unknown): 'task' | 'error' | null {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return null
  for (const node of contentNodes(payload)) {
    const status = String(node['status'] ?? '').toLowerCase()
    if (['working', 'pending', 'accepted', 'queued'].includes(status)) return 'task'
    if (['unavailable', 'error', 'failed', 'cancelled'].includes(status)) return 'error'
    const code = String(node['code'] ?? '').toLowerCase()
    if (['unsupported_source', 'provider_unavailable', 'unavailable'].includes(code)) return 'error'
    if (node['ok'] === false) return 'error'
  }
  return null
}

/** Есть ли в ответе непустые хиты, элементы или текст документа. */
function hasContent(payload: unknown): boolean {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return false
  for (const node of contentNodes(payload)) {
    for (const key of ['items', 'hits', 'matches', 'documents', 'results']) {
      const value = node[key]
      if (Array.isArray(value) && value.length > 0) return true
    }
    const document = node['document']
    if (typeof document === 'object' && document !== null && !Array.isArray(document)) {
      if (String((document as Record<string, unknown>)['text'] ?? '').trim() !== '') return true
    }
  }
  return false
}

/** Сводка объёма говорит «совпадений нет», а списков содержимого нет. */
function zeroTotals(payload: unknown): boolean {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return false
  for (const node of contentNodes(payload)) {
    const totals = node['totals']
    if (typeof totals !== 'object' || totals === null || Array.isArray(totals)) continue
    const numbers = Object.values(totals as Record<string, unknown>)
      .filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
    if (numbers.length === 0 || !numbers.every((value) => value === 0)) continue
    const lists = ['items', 'hits', 'matches', 'documents', 'results']
    if (!lists.some((key) => Array.isArray(node[key]) && (node[key] as unknown[]).length > 0)) return true
  }
  return false
}

/**
 * Осмысленный ли результат сверки.
 *
 * Ответ вне формы «JSON со списком sections» судится по прежним маркерам
 * пустоты: пустые `hits`, статус задачи/ошибки, отказ провайдера.
 * @param text - текст ответа документационного вызова.
 * @returns истина, когда сверка содержательна.
 */
export function docsResultIsMeaningful(text: string): boolean {
  if (text.trim() === '') return false
  let payload: unknown = null
  try {
    payload = JSON.parse(text)
  } catch {
    // Не JSON — ответ судится по прежним текстовым маркерам пустоты.
    payload = null
  }
  if (typeof payload === 'object' && payload !== null && !Array.isArray(payload)) {
    if (nonAnswer(payload) !== null) return false
    const bySections = sectionsMeaningful(payload)
    if (bySections !== null) return bySections
    if (hasContent(payload)) return true
    if (zeroTotals(payload)) return false
    for (const node of contentNodes(payload)) {
      for (const key of ['items', 'hits', 'matches', 'documents', 'results']) {
        const value = node[key]
        if (Array.isArray(value) && value.length === 0) return false
      }
    }
  }
  if (/"hits"\s*:\s*\[\s*\]/.test(text)) return false
  if (/"status"\s*:\s*"(?:unavailable|working|error|failed)"/.test(text)) return false
  if (/"code"\s*:\s*"(?:unsupported_source|provider_unavailable)"/.test(text)) return false
  return true
}

/**
 * Идентификатор задачи из ответа.
 * @param payload - разобранный ответ.
 * @returns taskId либо пустая строка.
 */
export function taskIdFrom(payload: unknown): string {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return ''
  for (const node of contentNodes(payload)) {
    const value = node['taskId']
    if (value !== undefined && value !== null && String(value) !== '') return String(value)
  }
  return ''
}

/**
 * Ответ — принятая задача, а не готовый результат.
 * @param payload - разобранный ответ.
 * @returns истина для статуса «в работе».
 */
export function docsTaskPending(payload: unknown): boolean {
  return nonAnswer(payload) === 'task'
}

/**
 * `unica.apply` — публикация готового плана.
 *
 * Публикация (executionToken без ops) тратит сверку; preview (at+ops) требует,
 * но не тратит: иначе исполнение блокировалось бы собственным планом.
 * @param args - канонические аргументы вызова.
 * @returns истина для публикации.
 */
export function applyIsPublication(call: GateCall): boolean {
  const outer = record(call.args)
  const inner = record(outer['args'])
  const args = Object.keys(inner).length > 0 ? inner : outer
  const hasToken = Boolean(args['executionToken'])
  const ops = args['ops']
  return hasToken && !(Array.isArray(ops) && ops.length > 0)
}

/**
 * Операция раннера: на верхнем уровне вызова, с фолбэком на вложенные args.
 * @param call - вызов инструмента.
 * @returns имя операции либо пустая строка.
 */
export function runnerOpOf(call: GateCall): string {
  const args = record(call.args)
  const inner = record(args['args'])
  const value = args['op'] ?? args['operation'] ?? inner['op'] ?? inner['operation']
  return typeof value === 'string' ? value : ''
}

/**
 * `unica.run` — исполнение, если `dryRun` явно не `true` (fail-closed).
 * @param call - вызов инструмента.
 * @returns истина для исполнения.
 */
export function runIsExecution(call: GateCall): boolean {
  return record(call.args)['dryRun'] !== true
}

/**
 * Структурная ли мутация: флаг сверки тратится только на структурное решение.
 * @param short - глагол юники.
 * @param args - канонические аргументы вызова.
 * @param verifyOps - операции, требующие сверки.
 * @returns истина, когда мутация структурная.
 */
export function isStructuralMutation(
  short: string,
  call: GateCall,
  verifyOps: readonly string[],
): boolean {
  const outer = record(call.args)
  const inner = record(outer['args'])
  const args = Object.keys(inner).length > 0 ? inner : outer
  if (short === 'apply') {
    const ops = args['ops']
    if (Array.isArray(ops) && ops.length > 0) {
      const names = ops.map((entry) => {
        if (typeof entry === 'object' && entry !== null && !Array.isArray(entry)) {
          const record_ = entry as Record<string, unknown>
          return String(record_['op'] ?? record_['name'] ?? '')
        }
        return String(entry)
      })
      return !names.every((name) => CODE_OP_RE.test(name))
    }
    return true
  }
  if (short === 'run') {
    return verifyOps.includes(runnerOpOf(call))
  }
  return true
}

/**
 * Убрать старые задачи: они не должны жить дольше часа.
 * @param state - состояние сверки.
 * @param nowSeconds - текущее время.
 */
function pruneTasks(state: DocState, nowSeconds: number): void {
  const cutoff = nowSeconds - 3600
  for (const [key, ts] of Object.entries(state.tasks)) {
    if (!Number.isFinite(ts) || ts < cutoff) delete state.tasks[key]
  }
}

/**
 * Решение до вызова инструмента.
 * @param call - вызов инструмента.
 * @param project - проект контура.
 * @param state - журнал и память сессии.
 * @returns разрешение либо отказ без сверки.
 */
export function evaluatePre(
  call: GateCall,
  project: ContourProject | null,
  state: GateState,
): PreDecision {
  if (typeof call.args !== 'object' || call.args === null || Array.isArray(call.args)) {
    return { kind: 'allow', code: 'none' }
  }
  if (project === null) return { kind: 'allow', code: 'none' }

  const tools = unicaTools(project)
  const short = unicaVerb(call.tool, unicaServerName(project))
  const doc = loadState(state)
  const verify: readonly string[] = tools.verify_ops

  if (short !== '') {
    const docsTools = tools.docs
    const mutationTools = tools.mutations

    if (docsTools.includes(short)) {
      doc.pending = true
      saveState(state, doc)
      return { kind: 'allow', code: 'none' }
    }

    let isMutation = mutationTools.includes(short)
    if (isMutation && tools.op_tools.includes(short)) {
      isMutation = verify.includes(runnerOpOf(call))
    }
    if (!isMutation) return { kind: 'allow', code: 'none' }

    if (doc.verified) {
      if (short === 'apply' && !applyIsPublication(call)) {
        return { kind: 'allow', code: 'none' }
      }
      if (short === 'run' && !runIsExecution(call)) {
        return { kind: 'allow', code: 'none' }
      }
      if (isStructuralMutation(short, call, verify)) {
        doc.verified = false
        saveState(state, doc)
      }
      return { kind: 'allow', code: 'none' }
    }
    state.audit(RULE, `mcp__unica__${short}`, 'мутация метаданных/кода без сверки с документацией платформы')
    return { kind: 'deny', code: 'docs-unverified', reason: BLOCK_REASON }
  }

  if (!FILE_TOOLS.has(call.tool)) return { kind: 'allow', code: 'none' }
  const args = record(call.args)
  const path = String(args['file_path'] ?? args['path'] ?? '')
  if (path === '') return { kind: 'allow', code: 'none' }
  const lower = path.toLowerCase()
  const isSource = (lower.endsWith('.bsl') || lower.endsWith('.xml'))
    && SOURCE_PREFIXES.some((prefix) => path.includes(prefix))
  if (!isSource) return { kind: 'allow', code: 'none' }
  if (doc.verified) return { kind: 'allow', code: 'none' }
  state.audit(RULE, path, 'правка исходников 1С без сверки с документацией платформы')
  return { kind: 'deny', code: 'docs-unverified', reason: BLOCK_REASON }
}

/**
 * Решение после вызова: результат сверки проставляет флаг.
 *
 * Задача (`status: working`) сверкой не считается: `taskId` запоминается, а
 * флаг ставит ответ `task.result`. Успешная сверка липкая — снимает её
 * только структурная мутация.
 * @param executed - вызов и его результат.
 * @param project - проект контура.
 * @param state - журнал и память сессии.
 * @returns `accept` (гейт только ведёт состояние).
 */
export function evaluatePost(
  executed: ExecutedCall,
  project: ContourProject | null,
  state: GateState,
): PostDecision {
  if (project === null) return { kind: 'accept' }
  const tools = unicaTools(project)
  const short = unicaVerb(executed.call.tool, unicaServerName(project))
  const doc = loadState(state)
  pruneTasks(doc, state.nowSeconds)

  const payloadOf = (text: string): unknown => {
    try {
      return JSON.parse(text) as unknown
    } catch {
      return null
    }
  }

  if (short === 'task.result' || short === 'task.get') {
    const payload = payloadOf(executed.resultText)
    let taskId = taskIdFrom(payload)
    if (taskId === '') {
      const args = record(executed.call.args)
      taskId = String(args['taskId'] ?? '')
    }
    if (taskId !== '' && taskId in doc.tasks) {
      if (!executed.isError && docsResultIsMeaningful(executed.resultText)) {
        doc.verified = true
        delete doc.tasks[taskId]
      }
      saveState(state, doc)
    }
    return { kind: 'accept' }
  }

  if (!tools.docs.includes(short)) return { kind: 'accept' }
  doc.pending = false
  if (executed.isError) {
    saveState(state, doc)
    return { kind: 'accept' }
  }
  const payload = payloadOf(executed.resultText)
  if (docsTaskPending(payload)) {
    const taskId = taskIdFrom(payload)
    if (taskId !== '') doc.tasks[taskId] = state.nowSeconds
  } else {
    doc.verified = doc.verified || docsResultIsMeaningful(executed.resultText)
  }
  saveState(state, doc)
  return { kind: 'accept' }
}
