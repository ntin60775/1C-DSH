/**
 * `mcp_gate` на DSH: изменяющие вызовы тест-контура в прод-контуре.
 *
 * Порт `reference/gates/mcp_gate.py`. Тест-контур (`1c-testpilot`, семейство
 * `1c-db`) даёт агенту доступ к живой базе: читающие инструменты безопасны,
 * меняющие данные или состояние сессии — нет. Прод-контур блокирует их до
 * эскалации, тест-контур пропускает, отсутствие признака считается продом.
 *
 * Признак контура: файл `.dsh/contour` (legacy `.zcode/contour`) со словом
 * `test`/`prod`; ключ `contour:` в `v8project*.yaml` — только обратная
 * совместимость старых проектов.
 *
 * Второй гард — stand-замок: `launch_client` не поднимает второй клиент на
 * файловую базу профиля.
 * @module dsh-1c-contour/gates/mcp
 */

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

import type { ContourProject, GateCall, GateState, PreDecision } from '../contract.js'
import { contourPaths, mcpShortName } from '../contour.js'
import { describeHolder } from '../lock.js'
import { resolvedPath } from '../paths.js'

/** Имя правила в журнале решений. */
export const RULE = 'mcp-unsafe-gate'

/** Имя правила гарда stand-замка в журнале. */
export const STAND_RULE = 'stand-lock'

/** Опасные инструменты по умолчанию: сервер → короткие имена. */
export const DEFAULT_DANGEROUS: Readonly<Record<string, readonly string[]>> = {
  '1c-testpilot': ['tc_execute_code', 'tc_execute_query', 'tc_execute_custom_bsl_function'],
  '1c-db': ['execute_code', 'submit_for_deanonymization', 'restart_1c_session', 'close_1c_session'],
}

/** Ключ `contour:` в файлах проекта. */
const CONTOUR_KEY_RE = /^\s*contour\s*:\s*['"]?([A-Za-zА-Яа-я]+)/m

/** REST-toolkit в bash: порт умолчания либо путь `/mcp` / `/api`. */
const TOOLKIT_REST_RE = /(:6003|\/api\b|\/mcp\b)/

/** Нативный вызов сервера семейства `1c-db` (включая инстансы). */
const TOOLKIT_MCP_RE = /^mcp__(1c-db(?:-[a-z0-9_-]+?)?)__(.+)$/

/** Чтение файла как текста; отсутствующий файл — `null`. */
function readText(path: string): string | null {
  try {
    return readFileSync(path, 'utf8')
  } catch {
    return null
  }
}

/**
 * Первый найденный файл раскладки: новая `.dsh`, затем legacy `.zcode`.
 * @param root - корень дерева.
 * @param relative - путь внутри каталога раскладки.
 * @returns содержимое и путь файла либо `null`.
 */
function readLayoutFile(root: string, relative: string): { text: string; path: string } | null {
  for (const layout of ['.dsh', '.zcode']) {
    const path = join(root, layout, relative)
    const text = readText(path)
    if (text !== null) return { text, path }
  }
  return null
}

/**
 * Контур проекта: `test` либо `prod`; всё, кроме явного `test`, — прод.
 * @param project - проект контура.
 * @returns признак контура.
 */
export function contourOf(project: ContourProject | null): 'test' | 'prod' {
  if (project === null) return 'prod'
  const marker = readLayoutFile(project.root, 'contour')
  if (marker !== null) {
    const value = marker.text.trim().toLowerCase()
    if (value.startsWith('test')) return 'test'
    if (value.startsWith('prod')) return 'prod'
  }
  for (const name of ['v8project.local.yaml', 'v8project.test.local.yaml', 'v8project.yaml']) {
    const text = readText(join(project.root, name))
    if (text === null) continue
    const match = CONTOUR_KEY_RE.exec(text)
    if (match?.[1] === undefined) continue
    const value = match[1].toLowerCase()
    if (value.startsWith('test')) return 'test'
    if (value.startsWith('prod')) return 'prod'
  }
  return 'prod'
}

/**
 * Опасные инструменты с учётом override из `contour.json`.
 * @param project - проект контура.
 * @returns карта «сервер → опасные имена».
 */
export function dangerousTools(project: ContourProject | null): Map<string, Set<string>> {
  const tools = new Map<string, Set<string>>()
  for (const [server, names] of Object.entries(DEFAULT_DANGEROUS)) tools.set(server, new Set(names))
  const gate = project?.contour['mcp_gate']
  if (typeof gate !== 'object' || gate === null || Array.isArray(gate)) return tools
  const override = (gate as Record<string, unknown>)['dangerous']
  if (typeof override !== 'object' || override === null || Array.isArray(override)) return tools
  for (const [server, names] of Object.entries(override as Record<string, unknown>)) {
    if (!Array.isArray(names)) continue
    tools.set(server, new Set(names.map((name) => String(name))))
  }
  return tools
}

/**
 * Разрешённые короткие имена из allowlist эскалаций.
 * @param project - проект контура.
 * @returns разрешённые имена и признак «разрешено всё».
 */
export function escalations(project: ContourProject | null): { allowed: Set<string>; allTools: boolean } {
  const allowed = new Set<string>()
  let allTools = false
  if (project === null) return { allowed, allTools }
  const found = readLayoutFile(project.root, 'mcp-write-allow.txt')
  if (found === null) return { allowed, allTools }
  for (const raw of found.text.split('\n')) {
    const line = raw.split('#', 1)[0]?.trim() ?? ''
    if (line === '') continue
    if (line === '*') allTools = true
    else allowed.add(line)
  }
  return { allowed, allTools }
}

/**
 * Опасный инструмент из нативного вызова MCP.
 * @param toolName - имя инструмента.
 * @param project - проект контура.
 * @returns сервер и короткое имя либо `null`.
 */
export function mutatingViaMcp(toolName: string, project: ContourProject | null): [string, string] | null {
  for (const [server, names] of dangerousTools(project)) {
    let hitServer = server
    let short: string
    if (server === '1c-db') {
      const match = TOOLKIT_MCP_RE.exec(toolName)
      if (match?.[1] === undefined || match[2] === undefined) continue
      hitServer = match[1]
      short = match[2]
    } else {
      short = mcpShortName(toolName, server)
    }
    if (short !== '' && names.has(short)) return [hitServer, short]
  }
  return null
}

/**
 * Опасный вызов REST-toolkit через bash.
 * @param command - командная строка.
 * @param project - проект контура.
 * @returns сервер и короткое имя либо `null`.
 */
export function mutatingViaBash(command: string, project: ContourProject | null): [string, string] | null {
  const tokens = dangerousTools(project).get('1c-db')
  if (tokens === undefined || tokens.size === 0) return null
  if (!TOOLKIT_REST_RE.test(command)) return null
  for (const short of tokens) {
    if (new RegExp(`\\b${short.replaceAll(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).test(command)) {
      return ['1c-db', short]
    }
  }
  return null
}

/**
 * Строка `File=` из `base` вызова; относительный путь — от корня дерева.
 * @param root - корень дерева.
 * @param base - путь базы из аргументов.
 * @returns строка подключения файловой базы.
 */
function fileConnection(root: string, base: string): string {
  // Символические ссылки снимаются: ключ stand-замка совпадает с тем, что
  // положил стартер, даже если до базы шли через ссылку.
  return base.startsWith('/') ? `File=${resolvedPath(base)}` : `File=${resolvedPath(join(root, base))}`
}

/**
 * Строка `File=` профиля из `profiles.yaml`.
 *
 * Серверный профиль (`server: true`) не разрешается: адрес сервера в профиле
 * не живёт, гард файловой базы его не видит.
 * @param project - проект контура.
 * @param profile - имя профиля.
 * @returns строка подключения либо `null`.
 */
export function profileConnection(project: ContourProject | null, profile: string): string | null {
  if (profile === '' || project === null) return null
  const found = readLayoutFile(project.root, 'testpilot/profiles.yaml')
  if (found === null) return null
  let inProfiles = false
  let current: string | null = null
  let base: string | null = null
  let server = false
  for (const line of found.text.split('\n')) {
    const stripped = (line.split('#', 1)[0] ?? '').trimEnd()
    if (stripped.trim() === '') continue
    const first = line.slice(0, 1)
    if (first !== ' ' && first !== '\t') {
      inProfiles = stripped === 'profiles:'
      current = null
      continue
    }
    if (!inProfiles) continue
    const indent = line.length - line.trimStart().length
    if (indent >= 2 && stripped.endsWith(':') && !stripped.slice(0, -1).includes(':')) {
      current = stripped.slice(0, -1).trim().replace(/^['"]|['"]$/g, '')
      continue
    }
    if (current !== profile) continue
    const separator = stripped.indexOf(':')
    const key = separator < 0 ? stripped : stripped.slice(0, separator)
    const value = (separator < 0 ? '' : stripped.slice(separator + 1)).trim().replace(/^['"]|['"]$/g, '')
    if (key.trim() === 'base' && value !== '') base = value
    else if (key.trim() === 'server') server = ['true', 'yes', '1'].includes(value.toLowerCase())
  }
  if (base === null || server) return null
  // Относительный `base` считается от каталога фактического файла профилей, а
  // не от жёсткой раскладки: на legacy `.zcode` иначе разъедется ключ замка.
  const absolute = base.startsWith('/') ? base : join(dirname(found.path), base)
  return `File=${resolvedPath(absolute)}`
}

/**
 * Живой stand-замок на базу вызова `tc_session(action="launch_client")`.
 *
 * Явные `base`/`server` перекрывают профиль; относительный `base`
 * разрешается от корня дерева.
 * @param call - вызов инструмента.
 * @param project - проект контура.
 * @param state - замки.
 * @returns запись замка либо `null`.
 */
export function standLockHit(
  call: GateCall,
  project: ContourProject | null,
  state: GateState,
): ReturnType<GateState['standLockStatus']> {
  const connection = connectionOf(call, project)
  if (connection === null || project === null) return null
  return state.standLockStatus({ tree: project.root, connection })
}

/**
 * Строка подключения базы, на которую нацелен `launch_client`.
 *
 * Явные `base`/`server` перекрывают профиль; относительный `base`
 * разрешается от корня дерева, относительный `base` профиля — от каталога
 * фактического файла профилей.
 * @param call - вызов инструмента.
 * @param project - проект контура.
 * @returns строка подключения либо `null`, когда база не файловая.
 */
export function connectionOf(call: GateCall, project: ContourProject | null): string | null {
  if (project === null) return null
  const args = typeof call.args === 'object' && call.args !== null && !Array.isArray(call.args)
    ? call.args as Record<string, unknown>
    : {}
  if (args['action'] !== 'launch_client') return null
  const base = String(args['base'] ?? '').trim()
  if (base !== '') {
    return args['server'] === true || args['server'] === 'true' ? null : fileConnection(project.root, base)
  }
  return profileConnection(project, String(args['profile'] ?? ''))
}

/**
 * Блокирующее сообщение изменяющего вызова в прод-контуре.
 * @param server - сервер MCP.
 * @param short - короткое имя инструмента.
 * @param project - проект контура.
 * @param contour - признак контура.
 * @returns текст причины.
 */
function blockMessage(
  server: string,
  short: string,
  project: ContourProject,
  contour: string,
): string {
  const layout = contourPaths(project).escalationFile.split('/')[0] ?? '.dsh'
  return [
    `Инструмент ${short} (сервер ${server}) меняет данные/состояние базы,`,
    `а контур проекта — ${contour} (mcp-unsafe-gate).`,
    '',
    `Проект: ${project.root}`,
    '',
    'Что делать:',
    `  • если это тестовая база — объяви контур файлом ${layout}/contour`,
    '    со словом test (ключ `contour:` в v8project.local.yaml не писать:',
    '    v8-runner 0.15 его отвергает);',
    `  • разовое разрешение — строка «${short}» в ${layout}/mcp-write-allow.txt`,
    '    (каждое разрешение пишется в журнал аудита);',
    '  • на проде безопаснее читать: execute_query, get_metadata,',
    '    get_event_log, find_references_to_object, get_access_rights.',
  ].join('\n')
}

/**
 * Блокирующее сообщение гарда stand-замка.
 * @param holder - запись замка.
 * @returns текст причины.
 */
function standBlockMessage(holder: NonNullable<ReturnType<GateState['standLockStatus']>>): string {
  return [
    'launch_client (tc_session) не поднимает второй клиент: база занята',
    '  stand-замком (гард файловой базы, stand-lock).',
    `  Держатель: ${describeHolder(holder)}`,
    '  Живой стенд гасится start_1c_db.sh --stop в дереве держателя;',
    '  connect и stop_client клиент не поднимают — не блокируются.',
  ].join('\n')
}

/**
 * Решение до вызова инструмента.
 * @param call - вызов инструмента.
 * @param project - проект контура.
 * @param state - журнал и замки.
 * @returns разрешение либо отказ изменяющего вызова.
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

  if (mcpShortName(call.tool, '1c-testpilot') === 'tc_session') {
    const action = (call.args as Record<string, unknown>)['action']
    if (action === 'launch_client') {
      const holder = standLockHit(call, project, state)
      if (holder !== null) {
        const args = call.args as Record<string, unknown>
        const what = String(args['profile'] ?? args['base'] ?? '?')
        state.audit(
          STAND_RULE,
          `launch_client ${what} (${project.root})`,
          `база занята stand-замком, держатель pid ${String(holder.pid ?? '?')}`,
        )
        return { kind: 'deny', code: 'stand-lock', reason: standBlockMessage(holder) }
      }
    }
  }

  let hit: [string, string] | null = null
  if (call.tool === 'bash') {
    hit = mutatingViaBash(String((call.args as Record<string, unknown>)['command'] ?? ''), project)
  } else if (call.tool.startsWith('mcp__')) {
    hit = mutatingViaMcp(call.tool, project)
  }
  if (hit === null) return { kind: 'allow', code: 'none' }

  const [server, short] = hit
  const contour = contourOf(project)
  if (contour === 'test') {
    state.audit(RULE, `${short} (${server}, ${project.root})`, 'тест-контур: изменяющий вызов разрешён', 'confirmed')
    return { kind: 'allow', code: 'none' }
  }
  const { allowed, allTools } = escalations(project)
  if (allTools || allowed.has(short)) {
    const layout = contourPaths(project).escalationFile.split('/')[0] ?? '.dsh'
    state.audit(
      RULE,
      `${short} (${server}, ${project.root})`,
      `эскалация из ${layout}/mcp-write-allow.txt`,
      'confirmed',
    )
    return { kind: 'allow', code: 'none' }
  }
  state.audit(RULE, `${short} (${server}, ${project.root})`, `${contour}-контур: изменяющий вызов без эскалации`)
  return { kind: 'deny', code: 'mcp-unsafe', reason: blockMessage(server, short, project, contour) }
}
