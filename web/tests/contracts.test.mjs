import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import Ajv from 'ajv';

const fixtures = new URL('../fixtures/', import.meta.url);
const cases = JSON.parse(await readFile(new URL('contract-cases.json', fixtures), 'utf8'));
// JSON Schema validates the wire response. Validation must never repair a payload.
const ajv = new Ajv({ allErrors: true, strict: true, coerceTypes: false, useDefaults: false, removeAdditional: false });
const validators = new Map();
for (const item of cases) {
  if (!validators.has(item.schema)) {
    const schema = JSON.parse(await readFile(new URL(`../schemas/${item.schema}.schema.json`, import.meta.url), 'utf8'));
    validators.set(item.schema, ajv.compile(schema));
  }
  test(`${item.valid ? 'accepts' : 'rejects'} ${item.name}`, async () => {
    const value = item.fixture
      ? JSON.parse(await readFile(new URL(item.fixture, fixtures), 'utf8'))
      : structuredClone(item.data);
    const before = structuredClone(value);
    const validate = validators.get(item.schema);
    assert.equal(validate(value), item.valid, JSON.stringify(validate.errors));
    assert.deepEqual(value, before, 'validation must preserve the exact response');
  });
}

test('shared playlist summary is the library projection without library-only fields', async () => {
  const library = JSON.parse(await readFile(new URL('valid/library-context.json', fixtures), 'utf8'));
  const picker = JSON.parse(await readFile(new URL('valid/user-playlists-context.json', fixtures), 'utf8'));
  const { public: visibility, pinned, hidden, can_undo, ...summary } = library.sections.favorites[0];
  assert.equal(visibility, false);
  assert.equal(pinned, true);
  assert.equal(hidden, false);
  assert.equal(can_undo, true);
  assert.deepEqual(summary, picker.playlists[0]);
});

test('one-time notice has a fixture for the consumed signed-out session', async () => {
  const once = JSON.parse(await readFile(new URL('valid/session-signed-out-notice.json', fixtures), 'utf8'));
  const consumed = JSON.parse(await readFile(new URL('valid/session-signed-out.json', fixtures), 'utf8'));
  assert.notEqual(once.notice, null);
  assert.equal(consumed.notice, null);
  assert.equal(Object.hasOwn(once, 'csrf_token'), false);
  assert.equal(Object.hasOwn(consumed, 'csrf_token'), false);
});
