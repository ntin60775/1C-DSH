/**
 * `session_context` на DSH: где сессия — основное дерево или ворктри.
 *
 * Порт `reference/gates/session_context.py`. Работа по тикетам ведётся в
 * git-ворктри, merge — в основном дереве; сессия, запущенная не там, где
 * работа, легко начнёт править не то. На старте сессии гейт сообщает, где
 * cwd и какие ворктри активны. Вне git-репозитория и без новостей — молчит.
 *
 * Отличие от эталона: основное дерево берётся из списка ворктри, а не из
 * `show-toplevel` (в ворктри эталон называл основным само себя).
 * @module dsh-1c-contour/gates/session-context
 */

import { execFileSync } from 'node:child_process'
import { statSync } from 'node:fs'
import { join } from 'node:path'

import type { ContourProject } from '../contract.js'

/** Таймаут git-команды, как у эталона. */
const GIT_TIMEOUT_MS = 10_000

/**
 * Вывод git-команды; ошибка или таймаут — `null`.
 * @param cwd - рабочий каталог.
 * @param args - аргументы git.
 * @returns stdout без хвостовых пробелов либо `null`.
 */
function git(cwd: string, ...args: string[]): string | null {
  try {
    const output = execFileSync('git', args, {
      cwd,
      encoding: 'utf8',
      timeout: GIT_TIMEOUT_MS,
      stdio: ['ignore', 'pipe', 'ignore'],
    })
    return output.trim()
  } catch {
    return null
  }
}

/** Ворктри в списке: путь и короткое имя ветки. */
interface Worktree {
  readonly path: string
  readonly branch: string
}

/**
 * Разобрать `git worktree list --porcelain`.
 * @param out - вывод команды.
 * @returns ворктри с путём и веткой; bare-записи пропущены.
 */
export function parseWorktrees(out: string): Worktree[] {
  const result: Worktree[] = []
  let path = ''
  let branch = ''
  const flush = (): void => {
    if (path !== '' && branch !== '') result.push({ path, branch })
    path = ''
    branch = ''
  }
  for (const line of out.split(/\r?\n/)) {
    if (line === '') {
      flush()
      continue
    }
    const separator = line.indexOf(' ')
    const key = separator < 0 ? line : line.slice(0, separator)
    const value = separator < 0 ? '' : line.slice(separator + 1)
    if (key === 'worktree') path = value
    else if (key === 'branch') branch = value.split('/').at(-1) ?? value
    else if (key === 'bare') {
      path = ''
      branch = ''
    }
  }
  flush()
  return result
}

/**
 * Контекст старта сессии.
 * @param cwd - каталог сессии.
 * @returns текст для модели либо `null`, когда сообщать нечего.
 */
export function sessionContext(project: ContourProject | null, cwd: string): string | null {
  if (project === null || cwd === '') return null
  const toplevel = git(cwd, 'rev-parse', '--show-toplevel')
  if (toplevel === null || toplevel === '') return null
  const rawBranch = git(cwd, 'rev-parse', '--abbrev-ref', 'HEAD') ?? ''
  const branch = rawBranch === 'HEAD' || rawBranch === '' ? 'detached' : rawBranch
  const boxes = parseWorktrees(git(cwd, 'worktree', 'list', '--porcelain') ?? '')
  const others = boxes.filter((box) => box.path !== toplevel).map((box) => `${box.path} (${box.branch})`)
  let worktree: boolean
  try {
    worktree = statSync(join(toplevel, '.git')).isFile()
  } catch {
    worktree = false
  }
  if (worktree) {
    const main = boxes[0]?.path ?? toplevel
    const siblings = boxes.filter((box) => box.path !== toplevel && box.path !== main).map((box) => `${box.path} (${box.branch})`)
    let message = `сессия в ворктри ${toplevel} (${branch}); основное дерево: ${main} — merge тикета там.`
    if (siblings.length > 0) message += ` Прочие ворктри: ${siblings.join(', ')}.`
    return message
  }
  if (others.length === 0) return null
  return `cwd — основное дерево ${toplevel}; работа по тикетам ведётся в ворктри: ${others.join(', ')}. Merge — здесь.`
}