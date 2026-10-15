import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { DEFAULT_CONTACT_ADDRESS } from "../scripts/public-contact.mjs";
const root = new URL("../", import.meta.url).pathname;
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
function run(directory, { check = false, contact } = {}) {
  const env = { ...process.env, SHUFFIFY_SOURCE_ROOT: source };
  delete env.CONTACT_ADDRESS;
  if (contact !== undefined) env.CONTACT_ADDRESS = contact;
  return spawnSync(
    process.execPath,
    [
      resolve(root, "scripts/generate-public-sources.mjs"),
      `--output-dir=${directory}`,
      ...(check ? ["--check"] : []),
    ],
    { encoding: "utf8", env },
  );
}

test("public legal generation preserves authoritative fragments with the published contact", async () => {
  const output = await mkdtemp(join(tmpdir(), "shuffify-public-"));
  try {
    const result = run(output);
    assert.equal(result.status, 0, result.stderr);
    const generated = await readFile(
      join(output, "src/pages/public/generated/legal.ts"),
      "utf8",
    );
    assert.ok(!generated.includes("CONTACT_ADDRESS"));
    assert.match(generated, /mailto:[^\s<>]+@[^\s<>]+/);
    for (const name of ["terms", "privacy"]) {
      const fragment = await readFile(
        resolve(source, `assets/legal/${name}.html`),
        "utf8",
      );
      assert.ok(
        generated.includes(JSON.stringify(fragment.replaceAll("CONTACT_ADDRESS", DEFAULT_CONTACT_ADDRESS))),
        `${name} fragment must remain literal`,
      );
    }
    assert.equal(run(output, { check: true }).status, 0);
    await writeFile(
      join(output, "src/pages/public/generated/obsolete.ts"),
      "// obsolete",
    );
    assert.match(run(output, { check: true }).stderr, /Obsolete public source/);
    assert.equal(run(output).status, 0);
    await writeFile(
      join(output, "src/pages/public/generated/legal.ts"),
      "// stale",
    );
    assert.match(
      run(output, { check: true }).stderr,
      /Missing or stale public source/,
    );
    assert.equal(run(output).status, 0);
    await unlink(join(output, "src/pages/public/generated/legal.ts"));
    assert.match(
      run(output, { check: true }).stderr,
      /Missing or stale public source/,
    );
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("build contact input is escaped in both legal text and mailto attributes", async () => {
  const output = await mkdtemp(join(tmpdir(), "shuffify-contact-"));
  try {
    const contact = 'team+test@example.invalid" onclick="alert(1)<&';
    const result = run(output, { contact });
    assert.equal(result.status, 0, result.stderr);
    const generated = await readFile(
      join(output, "src/pages/public/generated/legal.ts"),
      "utf8",
    );
    assert.ok(
      generated.includes("team+test@example.invalid&amp;quot;") === false,
    );
    assert.ok(
      generated.includes(
        "team+test@example.invalid&quot; onclick=&quot;alert(1)&lt;&amp;",
      ),
    );
    assert.ok(!generated.includes("CONTACT_ADDRESS"));
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("prerender document keeps external entry/CSS and rejects inline behavior", async () => {
  const { publicDocument } = await import("../scripts/public-document.mjs");
  const template =
    '<!doctype html><html lang="en"><head><title>Shuffify</title><script type="module" src="/assets/app.js"></script><link rel="stylesheet" href="/assets/app.css"></head><body><div id="root"></div></body></html>';
  const html = publicDocument(template, {
    title: "Terms & Conditions",
    description: 'A "safe" description',
    body: "<main><h1>Terms</h1></main>",
  });
  assert.match(html, /<title>Terms &amp; Conditions<\/title>/);
  assert.match(html, /<main><h1>Terms<\/h1><\/main>/);
  assert.match(html, /content="A &quot;safe&quot; description"/);
  assert.match(html, /src="\/assets\/app.js"/);
  assert.match(html, /data-prerendered="true"/);
  for (const body of [
    "<script>alert(1)</script>",
    "<style>body{}</style>",
    '<div style="color:red">Text</div>',
    '<a onclick="alert(1)">Go</a>',
  ])
    assert.throws(
      () =>
        publicDocument(template, { title: "Page", description: "Text", body }),
      /Inline/,
    );
  assert.throws(
    () =>
      publicDocument(template.replace('<div id="root"></div>', ""), {
        title: "Page",
        description: "Text",
        body: "<h1>Page</h1>",
      }),
    /root/,
  );
});

for (const contact of ["CONTACT_ADDRESS", "contact_address", "team-CONTACT_ADDRESS@example.invalid"]) {
  test(`unresolved public contact refuses generation: ${contact}`, async () => {
    const output = await mkdtemp(join(tmpdir(), "shuffify-unresolved-contact-"));
    try {
      const result = run(output, { contact });
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /public contact.*placeholder/i);
      await assert.rejects(readFile(join(output, "src/pages/public/generated/legal.ts")));
    } finally {
      await rm(output, { recursive: true, force: true });
    }
  });
}
