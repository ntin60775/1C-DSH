/**
 * Порт чекера стиля BSL: движок пака либо внешний движок по контракту.
 *
 * ZCode-эталон резолвил чекер в порядке: `1C_STYLE_ENGINE` → `bsl-style-engine` в
 * PATH → вендоренный скрипт пака в дереве. Первые два вызываются как
 * `<движок> check <файлы>`, скрипт пака — как `<python> <скрипт> <файлы>`.
 *
 * Гейт получает порт и не знает, что внутри: так стиль-гейт остаётся
 * проверяемым без процессов, а хост сам решает, чем проверять.
 * @module dsh-1c-contour/style-checker
 */

import { spawn } from 'node:child_process'
import { accessSync, constants, existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { delimiter, join } from 'node:path'

import type { StyleCheckerPort } from './contract.js'

/** Предел одного прогона чекера — как у ZCode-эталона. */
export const CHECKER_TIMEOUT_MS = 30_000

/** Переменная окружения с внешним движком. */
export const ENGINE_ENV = '1C_STYLE_ENGINE'

/** Имя внешнего движка в PATH. */
export const ENGINE_NAME = 'bsl-style-engine'

/** Скрипты пака в порядке приоритета: новая раскладка, затем legacy. */
const PACK_CHECKERS: readonly string[] = [
  '.dsh/1c/skills/1c-bsl-code-style/scripts/bsl_style_check.py',
  '.dsh/skills/1c-bsl-code-style/scripts/bsl_style_check.py',
  '.zcode/skills/1c-bsl-code-style/scripts/bsl_style_check.py',
  '.dsh/style/scripts/bsl_style_check.py',
  '.zcode/style/scripts/bsl_style_check.py',
]

/** Найденный способ проверки. */
interface Resolved {
  /** Аргументы вызова до списка файлов. */
  readonly argv: readonly string[]
  /** Движок принимает подкоманду `check`. */
  readonly checkContract: boolean
}

/** Найти исполняемый файл в PATH. */
function which(name: string, env: NodeJS.ProcessEnv): string | null {
  const path = env['PATH'] ?? ''
  for (const dir of path.split(delimiter)) {
    if (dir === '') continue
    const candidate = join(dir, name)
    try {
      accessSync(candidate, constants.X_OK)
      return candidate
    } catch {
      continue
    }
  }
  return null
}

/** Запустить процесс с таймаутом; код `null` — не отработал. */
function runProcess(
  argv: readonly string[],
  cwd: string,
  timeoutMs: number,
): Promise<{ code: number | null; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const [command, ...args] = argv
    if (command === undefined) {
      resolve({ code: null, stdout: '', stderr: '' })
      return
    }
    let stdout = ''
    let stderr = ''
    let settled = false
    const finish = (code: number | null): void => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve({ code, stdout, stderr })
    }
    const child = spawn(command, args, { cwd, stdio: ['ignore', 'pipe', 'pipe'] })
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      finish(null)
    }, timeoutMs)
    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk: string) => {
      stdout += chunk
    })
    child.stderr.on('data', (chunk: string) => {
      stderr += chunk
    })
    child.on('error', () => finish(null))
    child.on('close', (code) => finish(code))
  })
}

/**
 * Создать порт чекера стиля.
 * @param options - окружение, предел прогона и интерпретатор python.
 * @returns порт для гейта стиля.
 */
export function createStyleChecker(options: {
  readonly env?: NodeJS.ProcessEnv
  readonly timeoutMs?: number
  readonly python?: string
} = {}): StyleCheckerPort {
  const env = options.env ?? process.env
  const timeoutMs = options.timeoutMs ?? CHECKER_TIMEOUT_MS
  const python = options.python ?? env['PYTHON'] ?? 'python3'

  const resolve = (root: string): Resolved | null => {
    const configured = env[ENGINE_ENV]
    if (configured !== undefined && configured !== '' && existsSync(configured)) {
      return { argv: [configured, 'check'], checkContract: true }
    }
    const engine = which(ENGINE_NAME, env)
    if (engine !== null) return { argv: [engine, 'check'], checkContract: true }
    for (const relative of PACK_CHECKERS) {
      const script = join(root, relative)
      if (existsSync(script)) return { argv: [python, script], checkContract: false }
    }
    return null
  }

  const execute = async (
    resolved: Resolved,
    targets: readonly string[],
    root: string,
  ): Promise<string | null> => {
    // Подкоманда `check` уже в argv при контрактном движке: второй раз её не
    // добавляем, иначе живой движок получит `check check <files>` и откажет.
    const argv = [...resolved.argv, ...targets]
    const done = await runProcess(argv, root, timeoutMs)
    if (done.code === null || (done.code !== 0 && done.code !== 1)) return null
    if (done.code === 0) return ''
    return (done.stdout.trim() === '' ? done.stderr : done.stdout).trim()
      || 'bsl_style_check: нарушения найдены'
  }

  return {
    async run(targets: readonly string[], root: string): Promise<string | null> {
      const resolved = resolve(root)
      if (resolved === null) return null
      return execute(resolved, targets, root)
    },
    async snippet(texts: readonly string[], root: string): Promise<string | null> {
      const resolved = resolve(root)
      if (resolved === null) return null
      const dir = mkdtempSync(join(tmpdir(), 'dsh-1c-style-'))
      const file = join(dir, 'fragment.bsl')
      try {
        writeFileSync(file, texts.join('\n'), 'utf8')
        const report = await execute(resolved, [file], root)
        if (report === null || report === '') return null
        return report.replaceAll(`${file}:`, 'фрагмент:')
      } finally {
        rmSync(dir, { recursive: true, force: true })
      }
    },
  }
}
