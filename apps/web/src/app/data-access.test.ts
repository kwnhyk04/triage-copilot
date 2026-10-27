import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';

// Pages, actions and components reach the gateway only through lib/data.ts, which checks the
// session first. Type-only imports load nothing.
const SRC = join(__dirname, '..');
const GATEWAY = /(?:^@triage\/ts-clients(?:\/|$)|\/lib\/(?:investigations|decisions|gateway)\.ts$)/;
// readyz's health read: a public probe, with no data in its answer.
const EXCEPTIONS = new Set(['app/readyz/route.ts -> ../../lib/gateway.ts']);

/** The modules a source file loads: every import and re-export but `import type`. */
export const loadedModules = (source: string) =>
  [...source.matchAll(/^\s*(?:import|export)\s+(type\s+)?(?:[^'"]*?\sfrom\s+)?['"]([^'"]+)['"]/gm)]
    .filter((match) => match[1] === undefined)
    .map((match) => match[2] ?? '')
    .concat([...source.matchAll(/\bimport\(\s*['"]([^'"]+)['"]\s*\)/g)].map((m) => m[1] ?? ''));

const sources = (dir: string) =>
  readdirSync(join(SRC, dir), { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && /\.tsx?$/.test(entry.name))
    .filter((entry) => !/\.test\.tsx?$/.test(entry.name))
    .map((entry) => relative(SRC, join(entry.parentPath, entry.name)).replaceAll('\\', '/'));

describe('data access', () => {
  it('finds what a file loads, and nothing a type import names', () => {
    const source = [
      "import { type Investigation } from '../lib/investigations.ts';",
      "import type { Plan } from '../lib/decisions.ts';",
      "import { listInvestigations } from '@triage/ts-clients/gateway';",
      "export { x } from './x.ts';",
      "import './side-effect.ts';",
      "const lazy = () => import('../lib/gateway.ts');",
    ].join('\n');
    expect(loadedModules(source)).toEqual([
      '../lib/investigations.ts',
      '@triage/ts-clients/gateway',
      './x.ts',
      './side-effect.ts',
      '../lib/gateway.ts',
    ]);
  });

  it('lets no page, action or component reach the gateway but through lib/data.ts', () => {
    const files = [...sources('app'), ...sources('components')];
    expect(files).toEqual(expect.arrayContaining(['app/readyz/route.ts', 'components/report.tsx']));
    const reached = files.flatMap((file) =>
      loadedModules(readFileSync(join(SRC, file), 'utf8'))
        .filter((module) => GATEWAY.test(module))
        .map((module) => `${file} -> ${module}`),
    );
    expect(reached.filter((link) => !EXCEPTIONS.has(link))).toEqual([]);
  });
});
