/**
 * Фикстуры паритет-стенда: временное дерево 1С, каталоги состояния, файлы
 * кейса. Один источник для паритет-теста и юнит-тестов раскладки.
 * @module tests/helpers/fixture
 */

import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'

/** Содержимое `v8project.yaml` фикстуры: основная конфигурация и расширение. */
export const V8PROJECT = `workPath: src
infobases:
  origin:
    connection: Srvr="host";Ref="erp";
source-set:
  - name: main
    type: CONFIGURATION
    path: src/cf
  - name: Тесты
    type: EXTENSION
    path: src/cfe/Тесты
`

/** Путь `.bsl`-исходника основной конфигурации в фикстуре. */
export const BSL = 'src/cf/CommonModules/ОбщегоНазначения.bsl'

/** Правила пересборки для кейсов с `contour.json`. */
export const REBUILD_RULES = {
  unica: {
    rebuild_rules: [
      {
        tool: 'run',
        op: 'push',
        source_sets: 'extensions',
        require: { full: true },
        message: 'Частичная загрузка расширений в этом проекте не работает — push только с full:true.',
      },
      {
        tool: 'run',
        op: 'push',
        source_sets: 'main',
        forbid: { full: true },
        message: 'Полная пересборка основной конфигурации запрещена — десятки минут.',
      },
    ],
  },
}

/** Описание временного дерева кейса. */
export interface Fixture {
  /** Корень временного каталога. */
  readonly root: string
  /** Дерево проекта 1С. */
  readonly tree: string
  /** Корень состояния контура для гейта. */
  readonly stateDir: string
}

/** Дополнительное содержимое кейса. */
export interface FixtureOptions {
  /** Файлы относительно корня временного каталога; `$TMP` заменяется на него. */
  readonly write?: Readonly<Record<string, string>>
  /** Каталоги относительно корня временного каталога. */
  readonly mkdir?: readonly string[]
  /** Записать `contour.json` в `.zcode/1c` дерева. */
  readonly contour?: unknown
  /** Раскладка конфига: `.zcode/1c` (legacy) или `.dsh/1c`. */
  readonly layout?: 'zcode' | 'dsh'
}

/**
 * Создать временное дерево 1С с маркерами, исходниками и каталогами.
 * @param options - дополнительные файлы и каталоги кейса.
 * @returns пути фикстуры.
 */
export function createFixture(options: FixtureOptions = {}): Fixture {
  const root = mkdtempSync(join(tmpdir(), 'dsh-1c-parity-'))
  const tree = join(root, 'tree')
  for (const dir of [
    join(tree, 'src', 'cf', 'CommonModules'),
    join(tree, 'src', 'docs'),
    join(tree, 'src', 'cfe', 'Расширение'),
    join(root, 'other-tree', 'src', 'cfe', 'Расширение'),
    ...(options.mkdir ?? []).map((entry) => join(root, entry)),
  ]) {
    mkdirSync(dir, { recursive: true })
  }
  writeFileSync(join(tree, 'v8project.yaml'), V8PROJECT, 'utf8')
  writeFileSync(join(root, 'other-tree', 'v8project.yaml'), V8PROJECT, 'utf8')
  const layout = options.layout ?? 'zcode'
  if (options.contour !== undefined) {
    const dir = join(tree, layout === 'dsh' ? '.dsh' : '.zcode', '1c')
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, 'contour.json'), `${JSON.stringify(options.contour, null, 2)}\n`, 'utf8')
  }
  for (const [relative, content] of Object.entries(options.write ?? {})) {
    const target = join(root, relative)
    mkdirSync(dirname(target), { recursive: true })
    writeFileSync(target, content.replaceAll('$TMP', root), 'utf8')
  }
  return {
    root,
    tree,
    stateDir: join(root, 'state', '1c'),
  }
}

/**
 * Прочитать журнал решений контура.
 * @param stateDir - корень состояния TS-гейта.
 * @returns записи журнала в порядке появления.
 */
export function readAudit(stateDir: string): Record<string, unknown>[] {
  let raw: string
  try {
    raw = readFileSync(join(stateDir, 'logs', 'rule-audit.jsonl'), 'utf8')
  } catch {
    return []
  }
  return raw
    .split('\n')
    .filter((line) => line !== '')
    .map((line) => JSON.parse(line) as Record<string, unknown>)
}
