/**
 * `no_designer` гейт: агенту запрещён Конфигуратор 1С.
 *
 * Прямой запуск `1cv8 DESIGNER` (включая обёртки env/timeout/distrobox и
 * `bash -c`), `v8-runner make`/`artifacts` и `unica.run {op: make}` —
 * отказ до вызова: make раннер исполняет только пакетным
 * Конфигуратором (пин `make: ibcmd` не читается), сборка epf/erf идёт
 * ibcmd-скриптами `epf-load.sh`/`epf-dump.sh`, .cfe — `config save`.
 * `v8-runner launch designer` и `unica.run {op: launch}` с клиентом
 * designer/configurator — тоже отказ.
 *
 * Именованные инфраструктурные исключения (решение оператора): batch-проверка
 * `unica.check` внутри раннера и внутренние проверки станции КД — не агент
 * дергает Конфигуратор, гейт их не блокирует. Эскалаций у запрета нет.
 * @module dsh-1c-contour/gates/no-designer
 */

import type { ContourProject, GateCall, GateState, PreDecision } from '../contract.js'
import { unicaVerb } from '../contour.js'

/** Имя правила в журнале решений. */
export const RULE = 'no-designer'

/** Класс причины запуска Конфигуратора. */
export type DesignerHit = 'designer-launch' | 'designer-make'

/** Бинарник Конфигуратора. */
const DESIGNER_BIN_RE = /^1cv8(\.exe)?$/i

/** Бинарник раннера. */
const RUNNER_BIN_RE = /^v8-runner(\.exe)?$/i

/** Обёртки запуска, не меняющие исполнителя. */
const WRAPPERS = new Set(['nohup', 'nice', 'ionice', 'stdbuf', 'sudo', 'doas', 'pkexec', 'exec'])

/** Клиенты раннера, открывающие Конфигуратор. */
const DESIGNER_CLIENTS = new Set(['designer', 'configurator'])

/**
 * Токены сегмента команды: кавычки снимаются, содержимое сохраняется.
 * @param segment - сегмент командной строки.
 * @returns токены.
 */
function tokensOf(segment: string): string[] {
  const out: string[] = []
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g
  let match: RegExpExecArray | null
  while ((match = re.exec(segment)) !== null) {
    const token = match[1] ?? match[2] ?? match[3] ?? ''
    if (token !== '') out.push(token)
  }
  return out
}

/**
 * Снять обёртки запуска: env, timeout, nice, sudo, distrobox enter.
 * @param tokens - токены сегмента.
 * @returns токены фактического запуска.
 */
function stripWrappers(tokens: readonly string[]): string[] {
  let rest = [...tokens]
  for (;;) {
    const first = rest[0] ?? ''
    const head = first.toLowerCase()
    if (head === 'env') {
      rest = rest.slice(1)
      while (rest.length > 0 && ((rest[0] ?? '').startsWith('-') || (rest[0] ?? '').includes('='))) {
        const flag = rest[0] ?? ''
        rest = rest.slice(flag === '-u' || flag === '--unset' ? 2 : 1)
      }
      continue
    }
    if (head === 'timeout') {
      rest = rest.slice(1)
      while (rest.length > 0 && (rest[0] ?? '').startsWith('-')) {
        const flag = rest[0] ?? ''
        rest = rest.slice(flag === '-k' || flag === '--kill-after' ? 2 : 1)
      }
      if (rest.length > 0 && /^[\d.]+[smhd]?$/.test(rest[0] ?? '')) rest = rest.slice(1)
      continue
    }
    if (WRAPPERS.has(head)) {
      rest = rest.slice(1)
      while (rest.length > 0 && (rest[0] ?? '').startsWith('-')) {
        const flag = rest[0] ?? ''
        rest = rest.slice(flag === '-n' || flag === '-u' ? 2 : 1)
      }
      continue
    }
    if (head === 'distrobox' && rest[1] === 'enter') {
      const separator = rest.indexOf('--')
      rest = separator >= 0 ? rest.slice(separator + 1) : rest.slice(3)
      continue
    }
    break
  }
  return rest
}

/**
 * Запуск Конфигуратора в одном сегменте команды.
 * @param segment - сегмент командной строки.
 * @returns класс причины либо `null`.
 */
function segmentHit(segment: string): DesignerHit | null {
  const tokens = stripWrappers(tokensOf(segment))
  const exe = tokens[0] ?? ''
  if ((exe === 'bash' || exe === 'sh') && tokens[1] === '-c' && tokens[2] !== undefined) {
    return designerHit(tokens[2])
  }
  const base = exe.split('/').pop() ?? ''
  if (DESIGNER_BIN_RE.test(base)) {
    return tokens.slice(1).some((token) => token.toUpperCase() === 'DESIGNER') ? 'designer-launch' : null
  }
  if (RUNNER_BIN_RE.test(base)) {
    const command = tokens[1] ?? ''
    if (command === 'make' || command === 'artifacts') return 'designer-make'
    if (command === 'launch' && tokens.slice(2).some((token) => DESIGNER_CLIENTS.has(token.toLowerCase()))) {
      return 'designer-launch'
    }
  }
  return null
}

/**
 * Запуск Конфигуратора или make в командной строке bash.
 *
 * Эвристика над сегментами (`;`, `&&`, `||`, `|`, перевод строки): исполнитель
 * сегмента после снятия обёрток — `1cv8` с режимом DESIGNER либо раннер с
 * make/launch designer. Чтение бинарника (`strings … | grep DESIGNER`) и
 * `grep DESIGNER` по файлам не запускают Конфигуратор и не блокируются.
 * @param command - командная строка.
 * @returns класс причины либо `null`.
 */
export function designerHit(command: string): DesignerHit | null {
  for (const segment of command.split(/&&|\|\||[;|\n]/)) {
    const hit = segmentHit(segment)
    if (hit !== null) return hit
  }
  return null
}

/**
 * Строковые значения аргументов операции (включая вложенный `args`).
 * @param value - аргументы вызова.
 * @param depth - остаток глубины обхода.
 * @returns строковые значения.
 */
function stringValues(value: unknown, depth: number): string[] {
  if (depth <= 0) return []
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap((item) => stringValues(item, depth - 1))
  if (typeof value === 'object' && value !== null) {
    return Object.values(value).flatMap((item) => stringValues(item, depth - 1))
  }
  return []
}

/**
 * Запрещённая операция `unica.run`: make либо launch Конфигуратора.
 * @param call - вызов инструмента.
 * @returns класс причины либо `null`.
 */
export function unicaRunHit(call: GateCall): DesignerHit | null {
  if (unicaVerb(call.tool, 'unica') !== 'run') return null
  if (typeof call.args !== 'object' || call.args === null || Array.isArray(call.args)) return null
  const args = call.args as Record<string, unknown>
  const op = String(args['op'] ?? '')
  if (op === 'make') return 'designer-make'
  if (op === 'launch' && stringValues(args, 4).some((value) => DESIGNER_CLIENTS.has(value.toLowerCase()))) {
    return 'designer-launch'
  }
  return null
}

/**
 * Блокирующее сообщение запрета Конфигуратора.
 * @param hit - класс причины.
 * @param project - проект контура.
 * @returns текст причины.
 */
function blockMessage(hit: DesignerHit, project: ContourProject): string {
  const head = hit === 'designer-make'
    ? 'make — только Конфигуратор (no-designer): раннер исполняет make пакетным дизайнером, пин make: ibcmd не читается; агенту make запрещён.'
    : 'Прямой запуск Конфигуратора запрещён (no-designer): агент не дергает 1cv8 DESIGNER и не открывает дизайн-клиент.'
  return [
    head,
    '',
    `Проект: ${project.root}`,
    '',
    'Маршрут сборки и поставки:',
    '  • epf/erf из XML — scripts/epf-load.sh (ibcmd config import --out),',
    '    разбор — scripts/epf-dump.sh (ibcmd config export --file): без ИБ;',
    '  • .cfe — ibcmd config save из базы; поставка — скилл 1c-ibcmd-delivery;',
    '  • проверка конфигурации — unica.check (batch-проверка раннера —',
    '    именованное исключение, не прямой запуск).',
  ].join('\n')
}

/**
 * Решение до вызова инструмента.
 * @param call - вызов инструмента.
 * @param project - проект контура.
 * @param state - журнал решений.
 * @returns разрешение либо отказ запуска Конфигуратора.
 */
export function evaluatePre(
  call: GateCall,
  project: ContourProject | null,
  state: GateState,
): PreDecision {
  if (project === null) return { kind: 'allow', code: 'none' }
  let hit: DesignerHit | null = null
  if (call.tool === 'bash') {
    const args = typeof call.args === 'object' && call.args !== null ? call.args as Record<string, unknown> : {}
    hit = designerHit(String(args['command'] ?? ''))
  } else if (call.tool.startsWith('mcp__')) {
    hit = unicaRunHit(call)
  }
  if (hit === null) return { kind: 'allow', code: 'none' }
  state.audit(RULE, `${call.tool} (${project.root})`, hit === 'designer-make' ? 'make: исполнитель только Конфигуратор' : 'прямой запуск Конфигуратора')
  return { kind: 'deny', code: hit, reason: blockMessage(hit, project) }
}
