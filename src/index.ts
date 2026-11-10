/**
 * Контур 1С в DeepSeek Harness: гейты правки исходников на событиях хоста.
 *
 * Плагин только переводит события DSH в {@link GateCall} и обратно — вся
 * логика лежит в чистых функциях `gates/*` и сверяется с python-эталоном.
 *
 * - `agent/created` — состояние агента, уборка состояния, контекст старта и
 *   пробуждение станции kd (побочные эффекты, не чистые гейты; субагентам
 *   стартовые действия не нужны);
 * - `tools/pre-execute` — решения до вызова: правка исходников, изменяющие
 *   вызовы тест-контура, требование сверки с документацией;
 * - `tools/post-execute` — решения после вызова: флаг сверки, стиль BSL,
 *   контракт носителя, освобождение замка инфобазы;
 * - `tools/result` — наблюдаемый финальный исход, вход гейта повторов.
 *
 * Порядок важен только для текста отказа: прямой запрет правки исходника
 * информативнее, чем «нужна сверка».
 * @module dsh-1c-contour
 */

import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { UserMessage } from '@deepseek-ai/dsh-session'
import type { PostToolDecision, PreToolDecision, ToolExecution, ToolExecutionResult } from '@deepseek-ai/dsh-tools'
import type {} from '@deepseek-ai/dsh-working-directory'

import type { ExecutedCall, GateCall, GateState, PostDecision, PreDecision } from './contract.js'
import { evaluatePre as evaluateAnonPre } from './gates/anon.js'
import { evaluatePost as evaluateModulePost } from './gates/bsl-module.js'
import { evaluatePost as evaluateStylePost } from './gates/bsl-style.js'
import { evaluatePost as evaluateDocPost, evaluatePre as evaluateDocPre } from './gates/doc.js'
import { ensureKdStation } from './gates/kd-session-start.js'
import { evaluatePre as evaluateMcpPre } from './gates/mcp.js'
import { evaluatePre as evaluateRetryPre, observeResult as observeRetryResult } from './gates/retry.js'
import { cleanSessionState } from './gates/session-clean.js'
import { sessionContext } from './gates/session-context.js'
import { evaluatePost as evaluateSourcePost, evaluatePre as evaluateSourcePre } from './gates/unica-source.js'
import { evaluatePost as evaluateWorktreePost } from './gates/worktree-hint.js'
import { resolveProject } from './paths.js'
import { observeResult, sessionStateOf } from './session.js'
import { createGateState, defaultStateDir } from './state.js'
import { createStyleChecker } from './style-checker.js'

/** Имя плагина в профиле. */
export const name = 'dsh-1c-contour'

/** Сервисы хоста, без которых гейт не работает. */
export const inject = ['tools', 'workingDirectory'] as const

/** Код отказа гейта в структурированной ошибке вызова. */
export const DENIED_CODE = 'CONTOUR_DENIED'

/** Собрать вызов гейта из события DSH. */
function gateCall(exec: Readonly<ToolExecution>, cwd: string): GateCall {
  return { tool: exec.name, args: exec.arguments, cwd }
}

/** Текст результата инструмента: гейту сверки нужен ответ провайдера. */
function resultText(result: Readonly<ToolExecutionResult>): string {
  return result.content
    .map((block) => block.type === 'text' ? block.text : '')
    .filter((text) => text !== '')
    .join('\n')
}

/** Политика вызова: отказ гейта приходит ошибкой с известным кодом. */
function deniedBy(result: Readonly<ToolExecutionResult>): boolean {
  return result.isError && result.error.info?.code === DENIED_CODE
}

/** Маркер ненулевого завершения команды в хвосте результата. */
const NONZERO_EXIT_RE = /\n\[exit code: (\d+)\]$/

/** Маркер убийства команды сигналом в хвосте результата. */
const KILLED_BY_SIGNAL_RE = /\n\[killed by signal: .+\]$/

/**
 * Вызов завершился ошибкой.
 *
 * `isError` у bash-инструмента означает сбой исполнения, а ненулевой код
 * возврата приходит обычным результатом с маркером `[exit code: N]` в хвосте
 * текста. Для дисциплины повторов это та же ошибка.
 */
function failedResult(result: Readonly<ToolExecutionResult>): boolean {
  if (result.isError) return true
  const text = resultText(result).trimEnd()
  const exit = NONZERO_EXIT_RE.exec(text)
  if (exit !== null) return Number(exit[1]) !== 0
  return KILLED_BY_SIGNAL_RE.test(text)
}

/**
 * Подключить гейты контура к хосту.
 * @param ctx - контекст плагина.
 */
export function apply(ctx: Context): void {
  const styleChecker = createStyleChecker()

  const directoryOf = (agent: Agent | undefined): string =>
    agent === undefined ? process.cwd() : ctx.workingDirectory.get(agent.session)

  // Корень состояния читается на каждый вызов: смена DSH_1C_STATE_DIR в
  // окружении процесса подхватывается без перезапуска плагина.
  const gateState = (agent: Agent | undefined): GateState => {
    const session = sessionStateOf(agent)
    return createGateState({
      sessionId: agent?.session.id ?? 'default',
      stateDir: defaultStateDir(),
      memory: session.memory,
      styleChecker,
    })
  }

  ctx.on('agent/created', async ({ agent }) => {
    sessionStateOf(agent)
    let cwd = process.cwd()
    try {
      cwd = await ctx.workingDirectory.ensure(agent)
    } catch (error: unknown) {
      ctx.logger.warn(`dsh-1c-contour: каталог сессии недоступен: ${String(error)}`)
    }
    // Стартовые гейты — для сессии человека; субагент получает свой cwd и
    // не должен ни подметать состояние, ни будить станцию на каждое создание.
    if (agent.session.header.origin === 'subagent') return
    const project = resolveProject(cwd)

    // Уборка и контекст — best-effort: старт сессии не роняем.
    try {
      const cleaned = cleanSessionState(defaultStateDir(), Date.now() / 1000)
      if (cleaned.locks > 0 || cleaned.files > 0) {
        ctx.logger.info(`dsh-1c-contour: выметено замков ${cleaned.locks}, файлов состояния ${cleaned.files}`)
      }
    } catch (error: unknown) {
      ctx.logger.warn(`dsh-1c-contour: уборка состояния не удалась: ${String(error)}`)
    }
    try {
      const context = sessionContext(project, cwd)
      if (context !== null) agent.inject(contextMessage(context))
    } catch (error: unknown) {
      ctx.logger.warn(`dsh-1c-contour: контекст сессии не собран: ${String(error)}`)
    }
    try {
      await ensureKdStation(project, cwd)
    } catch (error: unknown) {
      ctx.logger.warn(`dsh-1c-contour: станция 1c-kd не поднята: ${String(error)}`)
    }
  })

  ctx.on('tools/pre-execute', async (exec, next): Promise<PreToolDecision> => {
    const cwd = directoryOf(exec.agent)
    const project = resolveProject(cwd)
    const state = gateState(exec.agent)
    const call = gateCall(exec, cwd)
    const decisions: PreDecision[] = [
      evaluateSourcePre(call, project, state),
      evaluateMcpPre(call, project, state),
      evaluateDocPre(call, project, state),
      evaluateAnonPre(call, state),
      evaluateRetryPre(call, project, state),
    ]
    for (const decision of decisions) {
      if (decision.kind === 'deny') {
        return { kind: 'deny', reason: decision.reason, info: { name: 'ContourDeniedError', code: DENIED_CODE } }
      }
      if (decision.kind === 'ask') return { kind: 'ask', reason: decision.reason }
    }
    return next()
  })

  ctx.on('tools/post-execute', async (exec, result, next): Promise<PostToolDecision> => {
    const cwd = directoryOf(exec.agent)
    const project = resolveProject(cwd)
    const state = gateState(exec.agent)
    const executed: ExecutedCall = {
      call: gateCall(exec, cwd),
      isError: failedResult(result),
      denied: deniedBy(result),
      resultText: resultText(result),
    }

    const blocks: string[] = []
    const notices: string[] = []
    const collect = (decision: PostDecision): void => {
      if (decision.kind === 'block') blocks.push(decision.feedback)
      else if (decision.notice !== undefined) notices.push(decision.notice)
    }

    // Прямые правки: гейт носителя смотрит файл целиком, стиль — изменённые
    // строки, поэтому оба идут после вызова. Сверка ведёт свой флаг.
    collect(evaluateSourcePost(executed, project, state))
    collect(await evaluateDocPost(executed, project, state))
    collect(await evaluateStylePost(executed, project, state))
    collect(evaluateModulePost(executed, project, state))
    collect(evaluateWorktreePost(executed, project, state))

    const downstream = await next()
    const contexts = notices.length === 0 ? [] : [contextMessage(notices.join('\n\n'))]

    if (blocks.length > 0) {
      return {
        kind: 'block',
        feedback: [{ type: 'text', text: blocks.join('\n\n') }],
        ...contexts.length === 0 ? {} : { additionalContexts: contexts },
      }
    }
    if (contexts.length === 0) return downstream
    return { ...downstream, additionalContexts: [...contexts, ...downstream.additionalContexts ?? []] }
  })

  ctx.on('tools/result', (exec, result) => {
    const cwd = directoryOf(exec.agent)
    const project = resolveProject(cwd)
    const call = gateCall(exec, cwd)
    const state = gateState(exec.agent)
    const failed = failedResult(result)
    observeResult(sessionStateOf(exec.agent), call, failed, deniedBy(result))
    observeRetryResult(state, call, project, failed, deniedBy(result))
  })
}

/**
 * Сообщение модели из заметки гейта.
 *
 * Хост ждёт `UserMessage`; идентификатор и роль добавляются здесь, потому что
 * пакет сессии не входит в поставку бандла.
 * @param text - текст заметки.
 * @returns сообщение для приклеивания к результату.
 */
function contextMessage(text: string): UserMessage {
  return {
    id: `contour-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`,
    role: 'user',
    content: [{ type: 'text', text }],
    source: { kind: 'dsh-1c-contour' },
  } as unknown as UserMessage
}
