import { buildContact } from "../../scripts/public-contact.mjs";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";
const root = new URL("../../", import.meta.url).pathname;
const config = JSON.parse(
  await readFile(resolve(root, ".foundation-source.json"), "utf8").catch(
    (error) => {
      if (error.code === "ENOENT") return "{}";
      throw error;
    },
  ),
);
const source =
  process.env.SHUFFIFY_SOURCE_ROOT || config.sourceRoot || resolve(root, "..");
const pages = [
  ["/", "Shuffify: Evolve your collection.", "Evolve your"],
  ["/terms", "Terms of Service - Shuffify", "Terms of Service"],
  ["/privacy", "Privacy Policy - Shuffify", "Privacy Policy"],
];
for (const [path, title, heading] of pages)
  test(`built ${path} has complete static semantic content and external assets`, async () => {
    const html = await readFile(
      resolve(
        root,
        "dist",
        path === "/" ? "index.html" : `${path.slice(1)}/index.html`,
      ),
      "utf8",
    );
    assert.ok(html.includes(`<title>${title}</title>`));
    assert.match(html, /<meta name="description"/);
    assert.ok(html.includes(`data-prerendered-route="${path}"`));
    assert.equal((html.match(/<main\b/g) || []).length, 1);
    assert.equal((html.match(/<h1\b/g) || []).length, 1);
    assert.ok(html.includes(heading));
    assert.doesNotMatch(
      html,
      /<script(?![^>]*\bsrc=)[^>]*>|<style\b|\s(?:style|on\w+)\s*=/i,
    );
    assert.match(html, /<script[^>]+src="\/assets\//);
    assert.match(html, /<link[^>]+rel="stylesheet"/);
    assert.ok(html.includes('href="/terms"'));
    assert.ok(html.includes('href="/privacy"'));
    if (path !== "/") {
      assert.equal(
        await readFile(resolve(root, "dist", `${path.slice(1)}.html`), "utf8"),
        html,
        "Extensionless and trailing-slash aliases contain the same document.",
      );
      assert.ok(
        html.includes(
          (
            await readFile(resolve(source, `assets/legal${path}.html`), "utf8")
          ).replaceAll(
            "CONTACT_ADDRESS",
            buildContact()
              .replaceAll("&", "&amp;")
              .replaceAll("<", "&lt;")
              .replaceAll(">", "&gt;")
              .replaceAll('"', "&quot;")
              .replaceAll("'", "&#39;"),
          ),
        ),
        "Legal fragment is preserved byte for byte inside the static shell.",
      );
    } else {
      for (const text of [
        "Midnight Drive",
        "Golden Hour",
        "Electric Dreams",
        "Ocean Waves",
        "Cosmic Drift",
        "753",
        "757",
        "raid",
        "drip-rotation",
        "rotation",
        "schedules",
        "Why I built this.",
      ])
        assert.ok(html.includes(text));
      assert.match(html, /<form[^>]+action="\/login"[^>]+method="get"/);
      assert.match(
        html,
        /<input(?=[^>]*name="legal_consent")(?=[^>]*required)[^>]*>/,
      );
      assert.doesNotMatch(html, /name="(?:return_to|csrf_token)"/);
      assert.match(html, /id="shuffle-demo-controls"[^>]+hidden/);
    }
  });
test("production assets exclude fixture router, transport and recorded API data", async () => {
  const files = await readdir(resolve(root, "dist/assets"));
  const scripts = (
    await Promise.all(
      files
        .filter((name) => name.endsWith(".js"))
        .map((name) => readFile(resolve(root, "dist/assets", name), "utf8")),
    )
  ).join("\n");
  for (const forbidden of [
    "__foundation",
    "foundation-fixture-intent",
    "fixture-csrf",
    "Alice",
    "FixtureApp",
  ])
    assert.equal(scripts.includes(forbidden), false, forbidden);
  assert.equal(
    files.some(
      (name) => name.includes("FixtureApp") || name.includes("transport"),
    ),
    false,
  );
});
