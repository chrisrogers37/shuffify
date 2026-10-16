import assert from 'node:assert/strict';
import { mkdtemp, rm, unlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const generator = new URL('../scripts/generate-schemas.mjs', import.meta.url);
function run(directory, check = false) {
  return spawnSync(process.execPath, [generator.pathname, `--output-dir=${directory}`, ...(check ? ['--check'] : [])], {
    encoding: 'utf8',
  });
}

test('schema check catches missing, stale, and obsolete generated files', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'shuffify-contracts-'));
  try {
    const generated = run(directory);
    assert.equal(generated.status, 0, generated.stderr);
    assert.equal(run(directory, true).status, 0);
    await unlink(join(directory, 'session-context.schema.json'));
    await writeFile(join(directory, 'library-context.schema.json'), '{}\n');
    await writeFile(join(directory, 'obsolete.schema.json'), '{}\n');
    const checked = run(directory, true);
    assert.equal(checked.status, 1);
    assert.match(checked.stderr, /Missing schema: session-context/);
    assert.match(checked.stderr, /Stale schema: library-context/);
    assert.match(checked.stderr, /Obsolete schema: obsolete/);
    assert.equal(run(directory).status, 0);
    assert.equal(run(directory, true).status, 0);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
