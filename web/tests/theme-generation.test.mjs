import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
const root = new URL("../", import.meta.url).pathname;
const generator = new URL("../scripts/generate-theme.mjs", import.meta.url)
  .pathname;
let local = {};
try {
  local = JSON.parse(
    await readFile(resolve(root, ".foundation-source.json"), "utf8"),
  );
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
const source =
  process.env.SHUFFIFY_SOURCE_ROOT || local.sourceRoot || resolve(root, "..");
const tokenFile =
  process.env.SHUFFIFY_TOKENS_FILE ||
  (local.tokensFile
    ? resolve(root, local.tokensFile)
    : resolve(source, "assets/design/tokens.json"));
const tokens = JSON.parse(await readFile(tokenFile, "utf8"));
function run(directory, check = false, tokenPath = tokenFile) {
  return spawnSync(
    process.execPath,
    [generator, `--output-dir=${directory}`, ...(check ? ["--check"] : [])],
    {
      encoding: "utf8",
      env: {
        ...process.env,
        SHUFFIFY_SOURCE_ROOT: source,
        SHUFFIFY_TOKENS_FILE: tokenPath,
      },
    },
  );
}

test("theme exposes token-backed Tailwind typography, layout, spacing and motion", async () => {
  const directory = await mkdtemp(join(tmpdir(), "shuffify-theme-"));
  try {
    const generated = run(directory);
    assert.equal(generated.status, 0, generated.stderr);
    const css = await readFile(
      join(directory, "src/design/generated/theme.css"),
      "utf8",
    );
    for (const [alias, value] of Object.entries({
      "--text-sh-body": "var(--sh-type-body)",
      "--text-sh-body--line-height": "var(--sh-type-leading-body)",
      "--font-weight-sh-medium": "var(--sh-weight-medium)",
      "--tracking-sh-heading": "var(--sh-tracking-heading)",
      "--leading-sh-reading": "var(--sh-leading-reading)",
      "--container-sh-container": "var(--sh-layout-container)",
      "--spacing-sh-control": "var(--sh-space-control)",
      "--shadow-sh-panel": "var(--sh-shadow-panel)",
      "--ease-sh-standard": "var(--sh-motion-ease)",
    }))
      assert.ok(css.includes(`${alias}: ${value};`), `Missing ${alias}`);
    assert.match(
      css,
      /@utility duration-sh-feedback \{ transition-duration: var\(--sh-motion-feedback\); \}/,
    );
    assert.equal(run(directory, true).status, 0);
    await writeFile(
      join(directory, "src/design/generated/obsolete.css"),
      "/* obsolete */",
    );
    const obsolete = run(directory, true);
    assert.equal(obsolete.status, 1);
    assert.match(obsolete.stderr, /Obsolete design output/);
    await unlink(join(directory, "src/design/generated/obsolete.css"));
    await unlink(join(directory, "src/design/generated/theme.css"));
    const missing = run(directory, true);
    assert.equal(missing.status, 1);
    assert.match(missing.stderr, /Missing or stale design output/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("font tokens must identify existing local bundled font files", async () => {
  const directory = await mkdtemp(join(tmpdir(), "shuffify-font-"));
  try {
    for (const invalid of [
      "https://fonts.example.invalid/ui.woff2",
      "../../private/font.woff2",
      "../fonts/absent.woff2",
    ]) {
      const changed = structuredClone(tokens);
      changed.fonts.roles.ui.source = invalid;
      const invalidTokens = join(directory, "tokens.json");
      await writeFile(invalidTokens, JSON.stringify(changed));
      const generated = run(directory, true, invalidTokens);
      assert.equal(generated.status, 1);
      assert.match(generated.stderr, /Invalid local font/);
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("font tokens must name at least one non-blank font family", async () => {
  const directory = await mkdtemp(join(tmpdir(), "shuffify-font-family-"));
  try {
    for (const invalid of [[], [""], ["  "], ["DM Sans", ""], [42], "DM Sans"]) {
      const changed = structuredClone(tokens);
      changed.fonts.roles.ui.family = invalid;
      const invalidTokens = join(directory, "tokens.json");
      await writeFile(invalidTokens, JSON.stringify(changed));
      const generated = run(directory, true, invalidTokens);
      assert.equal(generated.status, 1);
      assert.match(generated.stderr, /Invalid font family: ui/);
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
