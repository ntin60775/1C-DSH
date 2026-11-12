/**
 * Контракт гейта контура 1С: чистые функции над вызовом инструмента.
 *
 * Гейт не знает про Cordis и DSH-события: адаптер в `index.ts` переводит
 * событие в {@link GateCall}, зовёт гейт и переводит решение обратно.
 * Благодаря этому гейт проверяется юнит-тестами и сверяется с векторами паритета
 * без поднятия хоста.
 * @module dsh-1c-contour/contract
 */

/** Класс причины — по нему сверяется корпус векторов и группируется журнал. */
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
  /** Мутация метаданных/кода без сверки с документацией платформы. */
  | 'docs-unverified'
  /** Изменяющий вызов тест-контура в прод-контуре без эскалации. */
  | 'mcp-unsafe'
  /** База занята stand-замком: второй клиент не поднимается. */
  | 'stand-lock'
  /** Нарушение стиля BSL в изменённых строках. */
  | 'bsl-style'
  /** Нарушение контракта носителя 1С-кода. */
  | 'bsl-module'
  /** Попытка отключить анонимизацию канала 1c-db. */
  | 'anon-raw'
  /** Третий идентичный повтор упавшей команды. */
  | 'retry-discipline'

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
  /** Какая раскладка дала корень: `.dsh/1c` или маркеры. */
  readonly layout: 'dsh' | 'markers'
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

/**
 * Запись замка на диске; поля унаследованы от ZCode-эталона.
 *
 * Замок ИБ несёт `session_id`/`operation`/`expires_ts` (TTL 20 минут).
 * Stand-замок python-стартера (`scripts/ib_lock.py`) этих полей не пишет:
 * владелец — pid, срок жизни — живость процесса, поэтому они опциональны.
 */
export interface LockRecord {
  readonly connection: string
  readonly tree: string
  readonly session_id?: string
  readonly operation?: string
  readonly started_at: string
  readonly expires_ts?: number
  /** Вид замка: `stand` — стенд-замок файловой базы, иначе замок ИБ. */
  readonly kind?: 'stand'
  /** Процесс-владелец stand-замка. */
  readonly pid?: number
}

/**
 * Память сессии для гейтов с состоянием.
 *
 * Эталон хранил состояние в файлах `state/1c/<gate>/<session>.json`; в хосте
 * сессия живёт в памяти, поэтому состояние — её часть и умирает вместе с ней.
 * Ключ включает имя гейта (`doc:verified`), чтобы гейты не делили поля.
 */
export interface GateMemory {
  /** Прочитать значение; `undefined` — не записано. */
  get<T>(key: string): T | undefined
  /** Записать значение. */
  set(key: string, value: unknown): void
}

/**
 * Порт чекера стиля BSL: движок пака или вендоренный скрипт.
 *
 * Гейт не знает, чем именно проверяют: движок выбирает адаптер. Отчёт —
 * `''` чисто, строка с нарушениями, `null` — проверка не отработала (это не
 * сигнал о нарушениях).
 */
export interface StyleCheckerPort {
  /** Прогнать чекер по файлам дерева. */
  run(targets: readonly string[], root: string): Promise<string | null>
  /** Предварительный чек фрагмента текста без контекста модуля. */
  snippet(texts: readonly string[], root: string): Promise<string | null>
}

/** Порт побочных эффектов гейта: журнал, замки, время, память, чекер. */
export interface GateState {
  /** Сессия-держатель состояния. */
  readonly sessionId: string
  /** Текущее время в секундах эпохи — как у ZCode-эталона в `expires_ts`. */
  readonly nowSeconds: number
  /** Запись в журнал решений контура. */
  audit(rule: string, action: string, reason: string, decision?: 'blocked' | 'confirmed' | 'advisory'): void
  /** Взять или продлить замок инфобазы. */
  acquireBaseLock(request: LockRequest): { readonly status: 'ok' } | { readonly status: 'busy'; readonly holder: LockRecord }
  /** Отпустить замок своего дерева. */
  releaseBaseLock(request: Omit<LockRequest, 'operation'>): void
  /** Живой stand-замок на базу вызова либо `null`. */
  standLockStatus(request: { tree: string; connection: string }): LockRecord | null
  /** Память сессии: состояние гейтов, живущее между вызовами. */
  readonly memory: GateMemory
  /** Чекер стиля BSL; `undefined` — не установлен (advisory-пропуск). */
  readonly styleChecker?: StyleCheckerPort
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
  /**
   * Текст результата инструмента: гейту сверки нужен ответ docs-провайдера,
   * а не только признак ошибки.
   */
  readonly resultText: string
}

/**
 * Решение после вызова инструмента.
 *
 * `notice` — текст, который хост добавляет в контекст модели. Эталон отдавал
 * его через `additionalContext` в stdout: advisory-отчёты стиля и носителя
 * доезжают до модели, не роняя правку.
 */
export type PostDecision =
  | { readonly kind: 'accept'; readonly notice?: string }
  | { readonly kind: 'block'; readonly feedback: string; readonly notice?: string }
