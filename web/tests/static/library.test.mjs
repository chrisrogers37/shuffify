import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
const assets = new URL("../../dist/assets/", import.meta.url);
test("compiled production Library includes bundled Sortable and excludes its fixture scenario corpus", async () => {
  const names = await readdir(assets);
  const code = (
    await Promise.all(
      names
        .filter((name) => name.endsWith(".js"))
        .map((name) => readFile(new URL(name, assets), "utf8")),
    )
  ).join("");
  for (const text of [
    "Lost admission response",
    "library-preference-unknown",
    "Queue admission was refused.",
    "synthetic-fixture",
    "Weekend discoveries",
  ])
    assert.ok(!code.includes(text), text + " leaked into production");
  assert.ok(code.includes("sortable-ghost"), "native Sortable is bundled");
});
