/**
 * `unica_source_gate` на DSH: исходники 1С правятся только через Unica.
 *
 * Перенос `reference/gates/unica_source_gate.py` без упрощений:
 *
 * 1. `write` / `edit` / `apply_patch` по `src/{cf,cfe,epf,erf}` и `tests/epf`
 *    блокируются; единственный обход — allowlist эскалаций проекта.
 * 2. `bash` с `sed -i` или перенаправлением в исходник блокируется; sed
 *    разбирается по операндам, правка копии вне исходников проходит.
 * 3. Вызовы `mcp__*unica*__*`: неинициализированный ворктри, правила
 *    пересборки проекта, замок инфобазы.
 * 4. Правка allowlist'а эскалаций журналируется — бумажный след обхода.
 *
 * Имена инструментов другие, чем у ZCode (`Write`/`Edit`/`ApplyPatch` →
 * `write`/`edit`/`apply_patch`), и `apply_patch` приносит патч текстом:
 * пути извлекаются из строк `*** Add File:` / `*** Update File:` /
 * `*** Move to:` / `*** Delete File:`.
 * @module dsh-1c-contour/gates/unica-source
 */

import { basename } from 'node:path'

import type { ContourProject, ExecutedCall, GateCall, GateState, PostDecision, PreDecision } from '../contract.js'
import {
  contourPaths,
  escalationEdit,
  escalationEntries,
  isWorktree,
  missingWorktreeWorkspace,
  runnerOp,
  unicaArgs,
  unicaServerName,
  unicaTools,
  unicaVerb,
  violatedRebuildRule,
} from '../contour.js'
import { describeHolder, resolveInfobaseConnection } from '../lock.js'
import { isAgentsDoc, normalizeSourcePath, resolveProject, SOURCE_PREFIXES } from '../paths.js'

/** Имя правила в журнале решений. */
export const RULE = 'unica-source-gate'

/** Инструменты DSH, пишущие файл по пути. */
const FILE_TOOLS = new Set(['write', 'edit', 'apply_patch'])

/** Инструмент DSH, исполняющий команду оболочки. */
const BASH_TOOL = 'bash'

const SED_INPLACE_RE = /sed\s+(-[a-zA-Z]*i[a-zA-Z]*|--in-place)/
const REDIRECT_RE = />>?\s*([^\s;|&]+)/g
const SHELL_SEPARATORS = new Set(['&&', '||', ';', '|', '&', '>', '<', '(', ')'])
const PUNCTUATION = new Set('();<>|&'.split(''))

const ESCALATION_FORMAT_HINT = 'формат: одна строка = путь относительно корня дерева (как в поле «Файл:» выше);'
  + ' причина — #-комментарием строкой выше; файл кладётся в корень дерева, в котором лежит целевой файл'

/** Аргументы вызова как словарь; всё остальное — пустой словарь. */
function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

/**
 * Разбить команду оболочки на слова так же, как python `shlex` с
 * `punctuation_chars`: кавычки снимаются, серии пунктуации — отдельные слова.
 * @param command - командная строка.
 * @returns слова команды.
 * @throws при незакрытой кавычке — вызывающий уходит в fail-closed разбор.
 */
function tokenize(command: string): string[] {
  const tokens: string[] = []
  let current = ''
  let started = false
  let index = 0
  while (index < command.length) {
    const char = command[index] as string
    if (char === '\\') {
      const next = command[index + 1]
      current += next ?? '\\'
      index += next === undefined ? 1 : 2
      started = true
      continue
    }
    if (char === "'") {
      const end = command.indexOf("'", index + 1)
      if (end < 0) throw new Error('незакрытая кавычка')
      current += command.slice(index + 1, end)
      index = end + 1
      started = true
      continue
    }
    if (char === '"') {
      index += 1
      while (index < command.length && command[index] !== '"') {
        const inner = command[index] as string
        if (inner === '\\' && index + 1 < command.length && '"\\$`'.includes(command[index + 1] as string)) {
          current += command[index + 1]
          index += 2
          continue
        }
        current += inner
        index += 1
      }
      if (index >= command.length) throw new Error('незакрытая кавычка')
      index += 1
      started = true
      continue
    }
    if (/\s/.test(char)) {
      if (started) {
        tokens.push(current)
        current = ''
        started = false
      }
      index += 1
      continue
    }
    if (PUNCTUATION.has(char)) {
      if (started) {
        tokens.push(current)
        current = ''
        started = false
      }
      let run = ''
      while (index < command.length && PUNCTUATION.has(command[index] as string)) {
        run += command[index]
        index += 1
      }
      tokens.push(run)
      continue
    }
    current += char
    started = true
    index += 1
  }
  if (started) tokens.push(current)
  return tokens
}

/**
 * Разбить команду на сегменты по шелл-разделителям.
 * @param tokens - слова команды.
 * @returns сегменты без разделителей.
 */
function segments(tokens: readonly string[]): string[][] {
  const result: string[][] = []
  let segment: string[] = []
  for (const token of tokens) {
    if (SHELL_SEPARATORS.has(token)) {
      if (segment.length > 0) result.push(segment)
      segment = []
      continue
    }
    segment.push(token)
  }
  if (segment.length > 0) result.push(segment)
  return result
}

/**
 * Операнды `sed`: цели и неразбираемые слова.
 *
 * Скрипт задан флагом `-e`/`-f`/`--expression`/`--file` — все свободные слова
 * цели; иначе первое свободное слово — скрипт, остальные — цели.
 * @param segment - слова одного сегмента, `segment[0]` — исполняемый файл.
 * @returns цели и слова с подстановкой.
 */
function sedOperands(segment: readonly string[]): { targets: string[]; unresolved: string[] } {
  const targets: string[] = []
  const unresolved: string[] = []
  let expressionSeen = false
  let scriptSeen = false
  let index = 1
  while (index < segment.length) {
    const token = segment[index] as string
    if (SHELL_SEPARATORS.has(token)) break
    if (token.startsWith('--')) {
      const base = token.includes('=') ? token.slice(0, token.indexOf('=')) : token
      if (base === '--expression' || base === '--file') {
        expressionSeen = true
        index += token.includes('=') ? 1 : 2
        continue
      }
      index += 1
      continue
    }
    if (token.startsWith('-') && token.length > 1) {
      const letters = token.slice(1)
      if (letters.includes('e') || letters.includes('f')) {
        expressionSeen = true
        index += token === '-e' || token === '-f' ? 2 : 1
        continue
      }
      if (token === '-l' || token === '--line-length') {
        index += 2
        continue
      }
      index += 1
      continue
    }
    if (expressionSeen || scriptSeen) {
      if (token.includes('$') || token.includes('`')) unresolved.push(token)
      else targets.push(token)
    } else {
      scriptSeen = true
    }
    index += 1
  }
  return { targets, unresolved }
}

/**
 * Цель записи в исходники через bash либо `null`.
 *
 * Неразбираемый вызов (незакрытая кавычка, цель-переменная) — fail-closed по
 * правилу «sed -i и упоминание пути исходников в строке».
 * @param command - командная строка.
 * @param cwd - каталог сессии.
 * @returns слово-цель либо `null`.
 */
export function bashWritesToSource(command: string, cwd: string): string | null {
  const legacyHit = (): string | null => {
    if (!SED_INPLACE_RE.test(command)) return null
    for (const prefix of SOURCE_PREFIXES) {
      if (command.includes(prefix)) return prefix
    }
    return null
  }
  let tokens: string[]
  try {
    tokens = tokenize(command)
  } catch {
    return legacyHit()
  }
  const prefixInCommand = SOURCE_PREFIXES.some((prefix) => command.includes(prefix))
  for (const segment of segments(tokens)) {
    if (basename(segment[0] as string) !== 'sed') continue
    const { targets, unresolved } = sedOperands(segment)
    for (const token of unresolved) {
      if (prefixInCommand) return token
    }
    for (const token of targets) {
      const normalized = normalizeSourcePath(token, cwd)
      if (normalized !== null && !isAgentsDoc(normalized.rel)) return token
    }
  }
  for (const match of command.matchAll(REDIRECT_RE)) {
    const target = match[1]
    if (target === undefined) continue
    const normalized = normalizeSourcePath(target, cwd)
    if (normalized !== null && !isAgentsDoc(normalized.rel)) return target
  }
  return null
}

/**
 * Подсказка, как выразить правку файла через поверхность Unica 0.15.
 * @param rel - путь файла относительно корня дерева.
 * @returns подсказка инструмента и операции.
 */
export function suggestUnicaTool(rel: string): string {
  const lower = rel.toLowerCase()
  if (lower.endsWith('.bsl')) return 'unica.apply (ops code.insert / code.replace)'
  if (lower.endsWith('configuration.xml')) return 'unica.apply / unica.run (контракт — словарь unica.run {})'
  if (lower.includes('/forms/')) return 'unica.apply (форма; сверка — unica.view)'
  return 'unica.apply / unica.run (контракт узла — словарь unica.run {}; сверка — unica.view)'
}

/**
 * Пути из текста патча `apply_patch`.
 *
 * Разбор повторяет парсер DSH (`packages/fs/fs-patch`): заголовок — это
 * строка, обрезанная по Unicode White_Space с обоих концов, поэтому ведущий
 * пробел перед `*** Add File:` парсер не отменяет. `Move to` живёт внутри
 * блока обновления и обрезается только справа — так же, как в парсере.
 * @param patch - текст патча в формате `*** Add File:` / `*** Update File:`.
 * @returns пути в порядке появления.
 */
export function patchPaths(patch: string): string[] {
  const paths: string[] = []
  for (const rawLine of patch.split('\n')) {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
    const header = line.trim()
    const head = /^\*\*\* (?:Add File|Update File|Delete File): (.+)$/.exec(header)
    if (head?.[1] !== undefined) {
      paths.push(head[1])
      continue
    }
    const move = /^\*\*\* Move to: (.+)$/.exec(line.trimEnd())
    if (move?.[1] !== undefined) paths.push(move[1])
  }
  return paths
}

/**
 * Файловые цели вызова.
 * @param call - вызов инструмента.
 * @returns пути, которые инструмент собирается править.
 */
function touchedPaths(call: GateCall): readonly string[] {
  const args = asRecord(call.args)
  if (call.tool === 'apply_patch') {
    const patch = args['input'] ?? args['patch']
    return typeof patch === 'string' ? patchPaths(patch) : []
  }
  const path = args['file_path'] ?? args['path']
  return typeof path === 'string' && path !== '' ? [path] : []
}

/**
 * Разбор эскалаций для блокирующего сообщения.
 * @param report - разбор allowlist'а.
 * @returns строки разбора.
 */
function escalationBlockLines(report: {
  readonly found: boolean
  readonly matched: readonly string[]
  readonly report: readonly string[]
  readonly treeRoot: string
  readonly label: string
}): string[] {
  if (!report.found) {
    return [
      `Разбор эскалаций: ${report.label} в дереве ${report.treeRoot} не найден —`,
      '  без файла разовая прямая правка не проходит.',
    ]
  }
  if (report.matched.length === 0 && report.report.length === 0) {
    return ['Разбор эскалаций: файл найден, путей: 0 (только комментарии).']
  }
  const lines = [
    `Разбор эскалаций: файл найден, путей: ${report.matched.length}; для этого файла не подошли:`,
  ]
  for (const line of report.matched) lines.push(`  - "${line}" (эскалация другого файла)`)
  for (const entry of report.report) lines.push(`  - ${entry}`)
  return lines
}

/**
 * Блокирующее сообщение о прямой правке исходника.
 * @param rel - путь файла относительно корня дерева.
 * @param suggestion - подсказка поверхности Unica.
 * @param escalation - разбор allowlist'а.
 * @param escalationFile - путь allowlist'а в раскладке проекта.
 * @returns текст причины.
 */
function blockMessage(
  rel: string,
  suggestion: string,
  escalation: ReturnType<typeof escalationEntries>,
  escalationFile: string,
): string {
  const lines = [
    'Прямое редактирование исходников 1С заблокировано (unica-source-gate).',
    '',
    `Файл: ${rel}`,
    `Как выразить: ${suggestion}`,
    '',
    'Поверхность Unica 0.15 (глаголы, не инструменты-на-файл):',
    '  unica.view / unica.search  — чтение/поиск (страницы по cursor)',
    '  unica.resolve {paths}      — пути/строки → логические адреса',
    '  unica.apply                — правка кода и структуры (ops из view.can)',
    '  unica.run {op, args, dryRun} — сборка/загрузка; unica.check — валидация',
    '  словарь unica.run {} — источник контракта; длинное свойство —',
    '  <узел>.Props.<имя>',
    '',
    'Разовая прямая правка (когда unica.* не выражает изменение) —',
    `впиши путь в ${escalationFile} и повтори.`,
    `  ${ESCALATION_FORMAT_HINT}`,
    '',
    ...escalationBlockLines(escalation),
    '',
    'Все вызовы Unica требуют cwd корня проекта.',
  ]
  return lines.join('\n')
}

/** Вызов юники, разобранный для гейта. */
interface UnicaCall {
  readonly short: string
  readonly args: Readonly<Record<string, unknown>>
  readonly callCwd: string
}

/**
 * Разобрать вызов юники; `null` — инструмент не юника.
 * @param call - вызов инструмента.
 * @param project - проект контура.
 * @returns глагол, аргументы и каталог вызова.
 */
function unicaCall(call: GateCall, project: ContourProject | null): UnicaCall | null {
  const short = unicaVerb(call.tool, unicaServerName(project))
  if (short === '') return null
  const args = unicaArgs(call.args)
  const rawCwd = args['cwd']
  const callCwd = typeof rawCwd === 'string' && rawCwd !== '' ? rawCwd : call.cwd
  return { short, args, callCwd }
}

/**
 * Решение до вызова инструмента.
 * @param call - вызов инструмента.
 * @param project - проект контура от каталога сессии либо `null`.
 * @param state - журнал, замки, часы.
 * @returns `allow`, `deny` с причиной либо `ask`.
 */
export function evaluatePre(
  call: GateCall,
  project: ContourProject | null,
  state: GateState,
): PreDecision {
  if (typeof call.args !== 'object' || call.args === null || Array.isArray(call.args)) {
    return { kind: 'allow', code: 'none' }
  }
  // Вне проекта 1С контура нет: гейт молчит. Форма пути (`src/cf/…`) сама по
  // себе не признак исходника — плагин стоит на машину, а не на проект, и в
  // чужом репозитории такой путь может быть чем угодно. Отличие от эталона,
  // который жил внутри проекта и блокировал по форме пути.
  // Исключение — вызов Unica с `cwd` внутри проекта: такой вызов относится к
  // проекту цели (инициализация ворктри, замок базы), даже если сессия открыта
  // вне проекта.
  let effective = project
  if (effective === null) {
    const callCwd = asRecord(call.args)['cwd']
    const target = call.tool.startsWith('mcp__') && typeof callCwd === 'string' && callCwd !== ''
      ? resolveProject(callCwd)
      : null
    if (target === null) return { kind: 'allow', code: 'none' }
    effective = target
  }
  const paths = contourPaths(effective)

  const unica = unicaCall(call, effective)
  if (unica !== null) {
    const tools = unicaTools(effective)

    if (isWorktree(unica.callCwd)) {
      const missing = missingWorktreeWorkspace(unica.callCwd)
      if (missing.length > 0) {
        state.audit(RULE, `unica в ворктри ${unica.callCwd}`, `воркспейс не инициализирован: ${missing.join(', ')}`)
        return {
          kind: 'deny',
          code: 'worktree-uninit',
          reason: [
            'Ворктри не инициализирован для Unica (worktree-env).',
            '',
            `Отсутствует: ${missing.join(', ')}`,
            '',
            'Инициализируй воркспейс скриптом:',
            `  ${paths.initWorktreeScript} ${unica.callCwd}`,
            '',
            'Скрипт копирует из основного дерева:',
            '  - v8project.local.yaml (креды ИБ, платформа)',
            '  - build/tools/ (артефакты инструментов)',
            '',
            'Симлинки НЕ использовать — только реальные копии.',
          ].join('\n'),
        }
      }
    }

    for (const rule of tools.rebuild_rules) {
      const violation = violatedRebuildRule(rule, unica.short, unica.args, unica.callCwd)
      if (violation !== null) {
        const sourceSet = unica.args['sourceSet']
        state.audit(RULE, `unica ${runnerOp(unica.args)} ${typeof sourceSet === 'string' ? sourceSet : ''}`.trim(), violation)
        return { kind: 'deny', code: 'rebuild-rule', reason: violation }
      }
    }

    const op = runnerOp(unica.args)
    if (op !== '' && tools.ib_mutating_ops.includes(op)) {
      const connection = resolveInfobaseConnection(unica.callCwd)
      if (connection !== null) {
        const acquired = state.acquireBaseLock({
          tree: unica.callCwd,
          connection,
          operation: op,
          sessionId: state.sessionId,
        })
        if (acquired.status === 'busy') {
          state.audit(RULE, `unica ${op}`, `база занята деревом ${acquired.holder.tree}`)
          return {
            kind: 'deny',
            code: 'ib-lock',
            reason: [
              'База занята другим деревом (worktree-env).',
              '',
              `База:   ${connection}`,
              `Держит: ${describeHolder(acquired.holder)}`,
              '',
              'Серверная база одна на все деревья проекта, поэтому с ней',
              'одновременно работает только одно дерево. Дождись освобождения',
              '(TTL замка — 20 минут) или закончи операцию в том дереве.',
            ].join('\n'),
          }
        }
      }
    }
    return { kind: 'allow', code: 'none' }
  }

  if (call.tool === BASH_TOOL) {
    const command = asRecord(call.args)['command']
    if (typeof command !== 'string' || command === '') return { kind: 'allow', code: 'none' }
    const hit = bashWritesToSource(command, call.cwd)
    if (hit !== null) {
      state.audit(RULE, `bash -> ${hit}`, 'запись в исходники 1С через bash')
      return {
        kind: 'deny',
        code: 'source-bash',
        reason: [
          'Запись в исходники 1С через bash заблокирована (unica-source-gate).',
          `Цель: ${hit}`,
          'Используй Unica MCP (маршрутизация — в навыке 1c-contour).',
        ].join('\n'),
      }
    }
    return { kind: 'allow', code: 'none' }
  }

  if (!FILE_TOOLS.has(call.tool)) return { kind: 'allow', code: 'none' }

  let granted = false
  for (const filePath of touchedPaths(call)) {
    const escalationName = escalationEdit(filePath, call.cwd)
    if (escalationName !== null) {
      state.audit(
        RULE,
        `${call.tool} ${paths.escalationFile}`,
        'изменён allowlist эскалаций — последующие прямые правки исходников будут им разрешены',
        'confirmed',
      )
      continue
    }
    const normalized = normalizeSourcePath(filePath, call.cwd)
    if (normalized === null) continue
    if (isAgentsDoc(normalized.rel)) continue
    const escalation = escalationEntries(normalized.root, call.cwd)
    if (escalation.matched.includes(normalized.rel)) {
      state.audit(
        RULE,
        `${call.tool} ${normalized.rel} (дерево ${normalized.root})`,
        `эскалация: разрешена прямой правкой (${escalation.label})`,
        'confirmed',
      )
      granted = true
      continue
    }
    const suggestion = suggestUnicaTool(normalized.rel)
    state.audit(RULE, `${call.tool} ${normalized.rel}`, `требуется ${suggestion}`)
    return {
      kind: 'deny',
      code: 'source-edit',
      reason: blockMessage(normalized.rel, suggestion, escalation, paths.escalationFile),
    }
  }
  return granted ? { kind: 'allow', code: 'escalation-granted' } : { kind: 'allow', code: 'none' }
}

/**
 * Решение после вызова: синхронная операция юники закончилась — замок своего
 * дерева отпускается.
 *
 * Замок отпускает только успешный вызов: эталон вешает освобождение на
 * `PostToolUse`, который у ZCode приходит лишь после успеха, а упавшая
 * операция держит базу до TTL. Отказ политики и ошибка в хосте доходят сюда
 * тем же событием, поэтому исход проверяется явно.
 * @param executed - вызов и его финальный исход.
 * @param project - проект контура.
 * @param state - журнал и замки.
 * @returns `accept` либо `block` с исправляющей обратной связью.
 */
export function evaluatePost(
  executed: ExecutedCall,
  project: ContourProject | null,
  state: GateState,
): PostDecision {
  if (executed.isError || executed.denied) return { kind: 'accept' }
  const call = executed.call
  if (typeof call.args !== 'object' || call.args === null || Array.isArray(call.args)) {
    return { kind: 'accept' }
  }
  const unica = unicaCall(call, project)
  if (unica === null) return { kind: 'accept' }
  if (unica.short.startsWith('task.')) return { kind: 'accept' }
  const tools = unicaTools(project)
  const op = runnerOp(unica.args)
  if (op === '' || !tools.ib_mutating_ops.includes(op)) return { kind: 'accept' }
  const connection = resolveInfobaseConnection(unica.callCwd)
  if (connection !== null) {
    state.releaseBaseLock({ tree: unica.callCwd, connection, sessionId: state.sessionId })
  }
  return { kind: 'accept' }
}
