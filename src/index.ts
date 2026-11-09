/**
 * Контур 1С в DeepSeek Harness: гейты правки исходников на событиях хоста.
 *
 * Плагин только переводит события DSH в {@link GateCall} и обратно — вся
 * логика лежит в чистых функциях `gates/*` и сверяется с python-эталоном.
 *
 * - `agent/created` — состояние агента и проверка каталога сессии;
 * - `tools/pre-execute` — решение до вызова (`deny` с причиной, `ask`);
 * - `tools/post-execute` — решение после вызова (`accept`/`block`);
 * - `tools/result` — наблюдаемый финальный исход, вход гейта повторов.
 * @module dsh-1c-contour
 */

import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { PostToolDecision, PreToolDecision, ToolExecution } from '@deepseek-ai/dsh-tools'
import type {} from '@deepseek-ai/dsh-working-directory'

import type { ExecutedCall, GateCall } from './contract.js'
import { evaluatePost, evaluatePre } from './gates/unica-source.js'
import { resolveProject } from './paths.js'
import { observeResult, sessionStateOf } from './session.js'
import { createGateState, defaultStateDir } from './state.js'

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

/**
 * Подключить гейты контура к хосту.
 * @param ctx - контекст плагина.
 */
export function apply(ctx: Context): void {
  const directoryOf = (agent: Agent | undefined): string =>
    agent === undefined ? process.cwd() : ctx.workingDirectory.get(agent.session)

  // Корень состояния читается на каждый вызов: смена DSH_1C_STATE_DIR в
  // окружении процесса подхватывается без перезапуска плагина.
  const gateStateFor = (agent: Agent | undefined) =>
    createGateState({ sessionId: agent?.session.id ?? 'default', stateDir: defaultStateDir() })

  ctx.on('agent/created', async ({ agent }) => {
    sessionStateOf(agent)
    try {
      await ctx.workingDirectory.ensure(agent)
    } catch (error: unknown) {
      ctx.logger.warn(`dsh-1c-contour: каталог сессии недоступен: ${String(error)}`)
    }
  })

  ctx.on('tools/pre-execute', async (exec, next): Promise<PreToolDecision> => {
    const cwd = directoryOf(exec.agent)
    const decision = evaluatePre(gateCall(exec, cwd), resolveProject(cwd), gateStateFor(exec.agent))
    if (decision.kind === 'deny') {
      return { kind: 'deny', reason: decision.reason, info: { name: 'ContourDeniedError', code: DENIED_CODE } }
    }
    if (decision.kind === 'ask') {
      return { kind: 'ask', reason: decision.reason }
    }
    return next()
  })

  ctx.on('tools/post-execute', async (exec, result, next): Promise<PostToolDecision> => {
    const cwd = directoryOf(exec.agent)
    const executed: ExecutedCall = {
      call: gateCall(exec, cwd),
      isError: result.isError,
      denied: result.isError && result.error.info?.code === DENIED_CODE,
    }
    const decision = evaluatePost(executed, resolveProject(cwd), gateStateFor(exec.agent))
    if (decision.kind === 'block') {
      return { kind: 'block', feedback: [{ type: 'text', text: decision.feedback }] }
    }
    return next()
  })

  ctx.on('tools/result', (exec, result) => {
    const cwd = directoryOf(exec.agent)
    observeResult(sessionStateOf(exec.agent), gateCall(exec, cwd), result, DENIED_CODE)
  })
}
