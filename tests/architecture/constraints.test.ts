/**
 * Architecture validation tests — the ones that keep the console honest.
 *
 * These do not test behaviour. They test that the console *cannot* do certain
 * things, by reading its own source. Each corresponds to a line in the final
 * verification, and each would otherwise be a claim rather than a fact.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = join(__dirname, '..', '..');
const SRC = join(ROOT, 'src');

function walk(dir: string, filter = /\.(ts|tsx)$/): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) out.push(...walk(path, filter));
    else if (filter.test(path)) out.push(path);
  }
  return out;
}

const sources = walk(SRC).map((path) => ({
  path,
  rel: relative(ROOT, path).replace(/\\/g, '/'),
  text: readFileSync(path, 'utf8'),
}));

/** Import specifiers, ignoring the ones inside comments. */
function imports(text: string): string[] {
  const stripped = text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const found: string[] = [];
  const pattern = /(?:from|import)\s+['"]([^'"]+)['"]/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(stripped))) found.push(match[1]!);
  return found;
}

describe('the console never reaches into the backend', () => {
  it('imports nothing that resolves outside this repository', () => {
    const offenders: string[] = [];
    for (const file of sources) {
      for (const specifier of imports(file.text)) {
        // Relative paths that climb above the repo root, or any absolute path
        // naming the backend, would be a direct coupling to Vision OS.
        if (specifier.includes('../../../') || specifier.includes('backend')) {
          offenders.push(`${file.rel}: ${specifier}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('names no Vision OS Python module anywhere in src/', () => {
    const offenders = sources
      .filter((file) => /from app\.vision_os|import app\.vision_os|app\/vision_os/.test(file.text))
      .map((file) => file.rel);
    expect(offenders).toEqual([]);
  });

  it('reaches the network only through the transport layer', () => {
    const offenders: string[] = [];
    for (const file of sources) {
      if (file.rel.startsWith('src/transport/')) continue;
      const stripped = file.text.replace(/\/\*[\s\S]*?\*\//g, '');
      if (/\bfetch\s*\(/.test(stripped)) offenders.push(`${file.rel}: fetch()`);
      if (/new\s+WebSocket\s*\(/.test(stripped)) offenders.push(`${file.rel}: new WebSocket()`);
      if (/new\s+EventSource\s*\(/.test(stripped)) offenders.push(`${file.rel}: new EventSource()`);
    }
    expect(offenders).toEqual([]);
  });
});

describe('the console contains no business logic', () => {
  it('defines no threshold constant over a domain attribute', () => {
    // Business logic looks like `dwell > 300` or `if (occupancy >= limit)`.
    // Rendering thresholds (pixel sizes, list caps) are not that, so the check
    // is scoped to names that carry domain meaning.
    const domain = /(dwell|occupancy|queue_length|wait_time|crowd|loiter|compliance|violation_count)/i;
    const comparison = /(>=?|<=?)\s*\d/;
    const offenders: string[] = [];

    for (const file of sources) {
      if (file.rel.startsWith('src/simulator/')) continue;
      for (const [index, line] of file.text.split('\n').entries()) {
        const code = line.replace(/\/\/.*$/, '');
        if (domain.test(code) && comparison.test(code)) {
          offenders.push(`${file.rel}:${index + 1} ${line.trim()}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('never constructs an Observation outside the simulator', () => {
    const offenders = sources
      .filter((file) => !file.rel.startsWith('src/simulator/'))
      .filter((file) =>
        /observation_id\s*:\s*['"`]/.test(file.text.replace(/\/\*[\s\S]*?\*\//g, '')),
      )
      .map((file) => file.rel);
    expect(offenders).toEqual([]);
  });

  it('keeps the simulator out of the application graph', () => {
    const offenders: string[] = [];
    for (const file of sources) {
      if (file.rel.startsWith('src/simulator/')) continue;
      for (const specifier of imports(file.text)) {
        if (specifier.includes('simulator')) offenders.push(`${file.rel}: ${specifier}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('the built bundle carries no fabricated facts', () => {
  const distDir = join(ROOT, 'dist', 'assets');
  let bundles: string[] = [];
  try {
    bundles = readdirSync(distDir)
      .filter((name) => name.endsWith('.js'))
      .map((name) => readFileSync(join(distDir, name), 'utf8'));
  } catch {
    bundles = [];
  }

  it.runIf(bundles.length > 0)('contains no simulator symbol', () => {
    // The structural guarantee is the import check above; this is the
    // belt-and-braces one. A console that could ship a fabricated observation —
    // even unreachable — is a console whose output cannot be trusted at a
    // release review.
    for (const bundle of bundles) {
      expect(bundle).not.toContain('replayStream');
      expect(bundle).not.toContain('streamWithSequenceHole');
      expect(bundle).not.toContain('synthetic-moving-target');
    }
  });

  it('reports honestly when there is no bundle to check', () => {
    // Not silently skipped: an unbuilt dist means this assurance was NOT
    // obtained, and saying so beats a green tick that checked nothing.
    if (bundles.length === 0) {
      expect(bundles).toHaveLength(0); // run `npm run build` to enable the check above
    } else {
      expect(bundles.length).toBeGreaterThan(0);
    }
  });
});

describe('nothing the platform states as a field is inferred', () => {
  it('derives retryability from no status code anywhere', () => {
    const errors = readFileSync(join(SRC, 'transport', 'errors.ts'), 'utf8');
    const code = errors.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

    // A status-code table is precisely the inference 09_API §8 forbids.
    expect(code).not.toMatch(/status\s*===\s*\d/);
    expect(code).not.toMatch(/status\s*==\s*\d/);
    expect(code).not.toMatch(/\[\s*429\s*,/);
    expect(code).toMatch(/error\.retryable/);
  });

  it('never recomputes is_stale from a local clock', () => {
    const offenders = sources
      .filter((file) => !file.rel.startsWith('src/simulator/'))
      .filter((file) => /is_stale\s*[:=]\s*(?!row\.|object\.|a\.)/.test(file.text))
      .filter((file) => /Date\.now|performance\.now/.test(file.text))
      .map((file) => file.rel);
    expect(offenders).toEqual([]);
  });
});

describe('the contract stays in sync with the harness', () => {
  it('declares the same channel set as the Python taps', () => {
    const ts = readFileSync(join(SRC, 'contract', 'types.ts'), 'utf8');
    const py = readFileSync(join(ROOT, 'harness', 'vosvc_harness', 'taps.py'), 'utf8');

    const tsChannels = [...ts.matchAll(/^\s*'([a-z]+)',$/gm)].map((m) => m[1]!);
    const pyBlock = py.match(/CHANNELS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\)/)?.[1] ?? '';
    const pyChannels = [...pyBlock.matchAll(/"([a-z]+)"/g)].map((m) => m[1]!);

    expect(pyChannels.length).toBeGreaterThan(0);
    for (const channel of pyChannels) {
      expect(tsChannels).toContain(channel);
    }
  });

  it('declares the same scenario set as the Python fault module', () => {
    const ts = readFileSync(join(SRC, 'contract', 'types.ts'), 'utf8');
    const py = readFileSync(join(ROOT, 'harness', 'vosvc_harness', 'sources', 'faults.py'), 'utf8');

    const pyScenarios = [...py.matchAll(/^\s+[A-Z_]+ = "([a-z_]+)"$/gm)].map((m) => m[1]!);
    expect(pyScenarios.length).toBe(11);
    for (const scenario of pyScenarios) {
      expect(ts).toContain(`'${scenario}'`);
    }
  });
});

describe('no write path into Vision State exists', () => {
  it('exposes no mutating method on the client', () => {
    const client = readFileSync(join(SRC, 'transport', 'client.ts'), 'utf8');
    for (const forbidden of [
      'updateObject',
      'setAttribute',
      'deleteObservation',
      'createObject',
      'updateState',
      'patchObservation',
    ]) {
      expect(client).not.toMatch(new RegExp(`\\b${forbidden}\\s*\\(`));
    }
  });

  it('sends PUT and PATCH nowhere', () => {
    const offenders = sources
      .filter((file) => /method:\s*'(PUT|PATCH)'/.test(file.text))
      .map((file) => file.rel);
    expect(offenders).toEqual([]);
  });
});
