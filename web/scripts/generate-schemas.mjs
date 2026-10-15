import { readFile, readdir, mkdir, writeFile, rm } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolve, sep } from 'node:path';
import { createGenerator } from 'ts-json-schema-generator';

export const schemaTypes = Object.freeze({
  'api-error': 'ApiError',
  'session-context': 'SessionContext',
  'settings-context': 'SettingsContext',
  'settings-update-request': 'SettingsUpdateRequest',
  'activity-context': 'ActivityContext',
  'library-context': 'LibraryContext',
  'routines-context': 'RoutinesContext',
  'workshop-context': 'WorkshopContext',
  'user-playlists-context': 'UserPlaylistsContext',
  'return-target': 'ReturnTarget',
});

const root = fileURLToPath(new URL('../', import.meta.url));
const outputArgument = process.argv.find((value) => value.startsWith('--output-dir='));
const schemaDirectory = outputArgument
  ? pathToFileURL(`${resolve(outputArgument.slice('--output-dir='.length))}${sep}`)
  : new URL('../schemas/', import.meta.url);
const check = process.argv.includes('--check');
const generator = createGenerator({
  path: `${root}src/api/contracts.ts`,
  tsconfig: `${root}tsconfig.json`,
  type: '*',
  expose: 'export',
  topRef: true,
  jsDoc: 'extended',
  additionalProperties: false,
  skipTypeCheck: false,
});
const expected = new Map(Object.entries(schemaTypes).map(([name, type]) => [
  `${name}.schema.json`, `${JSON.stringify(generator.createSchema(type), null, 2)}\n`,
]));
await mkdir(schemaDirectory, { recursive: true });
const actual = await readdir(schemaDirectory);
const obsolete = actual.filter((name) => name.endsWith('.schema.json') && !expected.has(name));
const failures = [];
for (const [name, generated] of expected) {
  const path = new URL(name, schemaDirectory);
  let saved;
  try { saved = await readFile(path, 'utf8'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (saved === generated) continue;
  if (check) failures.push(`${saved === undefined ? 'Missing' : 'Stale'} schema: ${name}`);
  else await writeFile(path, generated);
}
for (const name of obsolete) {
  if (check) failures.push(`Obsolete schema: ${name}`);
  else await rm(new URL(name, schemaDirectory));
}
if (failures.length) {
  process.stderr.write(`${failures.join('\n')}\nRun npm run schemas:generate and commit the generated schemas.\n`);
  process.exitCode = 1;
} else {
  process.stdout.write(`${check ? 'Verified' : 'Generated'} ${expected.size} API schemas.\n`);
}
