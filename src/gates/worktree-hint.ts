/**
 * `worktree_hint` на DSH: навигация после `git worktree add`.
 *
 * Порт `reference/gates/worktree_hint.py`. Смена cwd сессии из плагина
 * невозможна, но после создания ворктри агент должен продолжать работу в
 * нём, иначе гейты, doctor и e2e смотрят в основное дерево. Гейт разбирает
 * команду, проверяет каталог и отдаёт заметку в контекст модели.
 *
 * Это навигация, не запрет: блокировать нечего. Не нашли команду, путь не
 * извлекли, каталога нет или bash упал — молчим.
 * @module dsh-1c-contour/gates/worktree-hint
 */

import { statSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'

import { resolvedPath } from '../paths.js'

import type { ContourProject, ExecutedCall, GateState, PostDecision } from '../contract.js'

/** Флаги `git worktree add`, требующие значения: пропускаем вместе с ним. */
const VALUE_FLAGS = new Set(['-b', '-B', '-m', '-M', '-c', '--checkout', '--guess-remote', '--track'])

/**
 * Путь из команды `git worktree add …`; пустая строка — не нашли.
 * @param command - текст bash-команды.
 * @returns путь как он записан в команде.
 */
export function extractPath(command: string): string {
  const tokens = command.split(/\s+/).filter((token) => token !== '')
  for (let index = 0; index < tokens.length - 2; index += 1) {
    if (tokens[index] !== 'git' || tokens[index + 1] !== 'worktree' || tokens[index + 2] !== 'add') continue
    const rest = tokens.slice(index + 3)
    let cursor = 0
    while (cursor < rest.length) {
      const token = rest[cursor] as string
      if (token === '--') {
        cursor += 1
        break
      }
      if (token.startsWith('-')) {
        cursor += VALUE_FLAGS.has(token) ? 2 : 1
        continue
      }
      return token
    }
    break
  }
  return ''
}

/**
 * Заметка навигации после успешного создания ворктри.
 * @param executed - вызов и его исход.
 * @param state - состояние гейта (не используется, контракт единый).
 * @returns решение после вызова с заметкой либо без неё.
 */
export function evaluatePost(executed: ExecutedCall, project: ContourProject | null, state: GateState): PostDecision {
  void state
  if (project === null || executed.isError || executed.denied) return { kind: 'accept' }
  if (executed.call.tool !== 'bash') return { kind: 'accept' }
  const args = typeof executed.call.args === 'object' && executed.call.args !== null && !Array.isArray(executed.call.args)
    ? executed.call.args as Record<string, unknown>
    : {}
  const command = typeof args['command'] === 'string' ? args['command'] : ''
  const relative = extractPath(command)
  if (relative === '') return { kind: 'accept' }
  const rawWorkdir = args['workdir']
  const base = typeof rawWorkdir === 'string' && rawWorkdir !== ''
    ? resolvedPath(isAbsolute(rawWorkdir) ? rawWorkdir : join(executed.call.cwd, rawWorkdir))
    : executed.call.cwd
  const path = isAbsolute(relative) ? resolvedPath(relative) : resolvedPath(join(base, relative))
  try {
    if (!statSync(path).isDirectory()) return { kind: 'accept' }
  } catch {
    return { kind: 'accept' }
  }
  const notice = `ворктри создан: ${path}. Дальнейшая работа — в нём: команды с cd ${path}, doctor и e2e — из него; надёжнее перезапустить сессию DSH в ${path}.`
  return { kind: 'accept', notice }
}