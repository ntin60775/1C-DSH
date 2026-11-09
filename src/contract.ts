/**
 * Контракт гейта контура 1С: чистые функции над вызовом инструмента.
 *
 * Гейт не знает про Cordis и DSH-события: адаптер в `index.ts` переводит
 * событие в {@link GateCall}, зовёт гейт и переводит решение обратно.
 * Благодаря этому гейт проверяется юнит-тестами и сверяется с python-эталоном
 * без поднятия хоста.
 * @module dsh-1c-contour/contract
 */

/** Класс причины — по нему сверяется паритет с эталоном и группируется журнал. */
export type ReasonCode =
  /** Прямая правка исходника 1С инструментом файловой записи. */
  | 'source-edit'
  /** Запись в исходник 1С через bash (sed -i, перенаправление). */
  | 'source-bash'
  /** Вызов Unica в неинициализированном ворктри. */
  | 'worktree-uninit'
  /** Нарушено правило пересборки проекта из `contour.json`. */
  | 'rebuild-rule'
  /** Инфобаза занята другим деревом. */
  | 'ib-lock'
  /** Гейт не нашёл нарушения. */
  | 'none'
  /** Прямая правка разрешена allowlist'ом эскалаций. */
  | 'escalation-granted'

/** Один вызов инструмента так, как его видит гейт. */
export interface GateCall {
  /** Имя инструмента в DSH: `write`, `edit`, `apply_patch`, `bash`, `mcp__*`. */
  readonly tool: string
  /** Разобранные аргументы вызова. */
  readonly args: unknown
  /** Каталог сессии — база для относительных путей и поиска корня проекта. */
  readonly cwd: string
}

/** Проект контура: корень, раскладка конфига и его содержимое. */
export interface ContourProject {
  /** Абсолютный корень дерева 1С. */
  readonly root: string
  /** Какая раскладка дала корень: `.dsh/1c`, legacy `.zcode/1c` или маркеры. */
  readonly layout: 'dsh' | 'zcode' | 'markers'
  /** Содержимое `contour.json` (пустой объект, когда файла нет). */
  readonly contour: Readonly<Record<string, unknown>>
}

/** Запрос файлового замка инфобазы. */
export interface LockRequest {
  /** Дерево, берущее замок. */
  readonly tree: string
  /** Строка подключения к базе. */
  readonly connection: string
  /** Операция раннера, ради которой берётся замок. */
  readonly operation: string
  /** Сессия-держатель. */
  readonly sessionId: string
}

/** Запись замка на диске; поля совпадают с python-эталоном. */
export interface LockRecord {
  readonly connection: string
  readonly tree: string
  readonly session_id: string
  readonly operation: string
  readonly started_at: string
  readonly expires_ts: number
}

/** Порт побочных эффектов гейта: журнал, замки, время. */
export interface GateState {
  /** Сессия-держатель состояния. */
  readonly sessionId: string
  /** Текущее время в секундах эпохи — как у эталона в `expires_ts`. */
  readonly nowSeconds: number
  /** Запись в журнал решений контура. */
  audit(rule: string, action: string, reason: string, decision?: 'blocked' | 'confirmed'): void
  /** Взять или продлить замок инфобазы. */
  acquireBaseLock(request: LockRequest): { readonly status: 'ok' } | { readonly status: 'busy'; readonly holder: LockRecord }
  /** Отпустить замок своего дерева. */
  releaseBaseLock(request: Omit<LockRequest, 'operation'>): void
}

/** Решение до вызова инструмента. */
export type PreDecision =
  | { readonly kind: 'allow'; readonly code: 'none' | 'escalation-granted' }
  | { readonly kind: 'deny'; readonly reason: string; readonly code: Exclude<ReasonCode, 'none' | 'escalation-granted'> }
  | { readonly kind: 'ask'; readonly reason: string; readonly code: Exclude<ReasonCode, 'none' | 'escalation-granted'> }

/** Наблюдаемый финальный исход вызова — вход постового решения. */
export interface ExecutedCall {
  /** Вызов, который прошёл конвейер. */
  readonly call: GateCall
  /** Инструмент сообщил об ошибке. */
  readonly isError: boolean
  /**
   * Вызов отклонён политикой, а не упал в теле инструмента. Отказ гейта
   * приходит тем же `isError`, но сбоем инструмента не является и в счётчик
   * повторов не идёт.
   */
  readonly denied: boolean
}

/** Решение после вызова инструмента. */
export type PostDecision =
  | { readonly kind: 'accept' }
  | { readonly kind: 'block'; readonly feedback: string }
