import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { securityHeaders } from "../security-headers.ts";
import { buildVercelOutput } from "../scripts/build-vercel-output.mjs";

async function fixture(run) {
  const root = await mkdtemp(join(tmpdir(), "shuffify-static-output-"));
  try {
    await mkdir(join(root, "dist", "assets"), { recursive: true });
    await writeFile(join(root, "dist", "index.html"), "<main>Application</main>");
    await writeFile(join(root, "dist", "terms.html"), "<main>Terms</main>");
    await writeFile(join(root, "dist", "privacy.html"), "<main>Privacy</main>");
    await writeFile(join(root, "dist", "assets", "app-example.js"), "export {};");
    await writeFile(join(root, ".env"), "EXAMPLE_SECRET=fake-value");
    await run(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

function destination(config, pathname) {
  for (const route of config.routes) {
    if (!route.dest) continue;
    const match = new RegExp(`^(?:${route.src})$`).exec(pathname);
    if (match) return route.dest.replace(/\$(\d+)/g, (_, index) => match[Number(index)] ?? "");
  }
}

test("API and root authentication paths route to the build-time origin before the SPA", async () => {
  await fixture(async (root) => {
    const config = await buildVercelOutput({ root, apiUrl: "https://api.example.invalid/" });
    for (const path of ["/api", "/api/session", "/api/playlist/example/items", "/login", "/callback", "/logout", "/health", "/login/", "/callback/", "/logout/", "/health/"]) {
      assert.equal(destination(config, path), `https://api.example.invalid${path}`);
    }
    assert.equal(destination(config, "/library"), "/index.html");
    assert.equal(destination(config, "/workshop/example"), "/index.html");
    assert.equal(destination(config, "/login-help"), "/index.html");
    assert.equal(destination(config, "/terms"), "/terms.html");
    assert.equal(destination(config, "/privacy/"), "/privacy.html");
    const dynamic = config.routes.filter((route) => route.dest?.startsWith("https://"));
    for (const route of dynamic) {
      for (const name of ["Cache-Control", "CDN-Cache-Control", "Vercel-CDN-Cache-Control"]) {
        assert.equal(route.headers[name], "no-store");
      }
    }
  });
});

test("only built static files are copied and stale output is replaced", async () => {
  await fixture(async (root) => {
    await mkdir(join(root, ".vercel", "output", "static"), { recursive: true });
    await writeFile(join(root, ".vercel", "output", "static", "stale.txt"), "stale");
    const config = await buildVercelOutput({ root, apiUrl: "https://api.example.invalid" });
    assert.equal(config.version, 3);
    assert.equal(await readFile(join(root, ".vercel", "output", "static", "assets", "app-example.js"), "utf8"), "export {};");
    await assert.rejects(readFile(join(root, ".vercel", "output", "static", ".env")));
    await assert.rejects(readFile(join(root, ".vercel", "output", "static", "stale.txt")));
    assert.deepEqual(JSON.parse(await readFile(join(root, ".vercel", "output", "config.json"), "utf8")), config);
    const filesystem = config.routes.findIndex((route) => route.handle === "filesystem");
    const fallback = config.routes.findIndex((route) => route.dest === "/index.html");
    assert.ok(filesystem > 0 && filesystem < fallback);
    assert.ok(config.routes.slice(filesystem + 1, fallback).some((route) => route.status === 404));
  });
});

test("static responses use the strict application security policy", async () => {
  await fixture(async (root) => {
    const config = await buildVercelOutput({ root, apiUrl: "https://api.example.invalid" });
    const headers = config.routes[0].headers;
    assert.deepEqual(headers, securityHeaders);
    const { default: viteConfig } = await import("../vite.config.ts");
    assert.deepEqual(viteConfig({ command: "serve", mode: "production" }).preview.headers, headers);
    assert.equal(headers["X-Content-Type-Options"], "nosniff");
    assert.equal(headers["X-Frame-Options"], "DENY");
    assert.equal(headers["Referrer-Policy"], "strict-origin-when-cross-origin");
    assert.equal(headers["Strict-Transport-Security"], "max-age=31536000; includeSubDomains; preload");
    for (const directive of ["script-src 'self'", "style-src 'self'", "frame-ancestors 'none'", "connect-src 'self' https://*.scdn.co https://*.spotifycdn.com", "form-action 'self' https://accounts.spotify.com"]) {
      assert.ok(headers["Content-Security-Policy"].includes(directive));
    }
    assert.ok(!headers["Content-Security-Policy"].includes("unsafe-inline"));
    assert.ok(!headers["Content-Security-Policy"].includes("cdn.jsdelivr.net"));
  });
});

for (const apiUrl of [undefined, "", "http://api.example.invalid", "https://alice:fake@api.example.invalid", "https://api.example.invalid/path", "https://api.example.invalid?secret=fake", "https://api.example.invalid/#fragment"]) {
  test(`invalid API origin refuses output: ${String(apiUrl)}`, async () => {
    await fixture(async (root) => {
      await assert.rejects(buildVercelOutput({ root, apiUrl }), /RAILWAY_API_URL/);
      await assert.rejects(readFile(join(root, ".vercel", "output", "config.json")));
    });
  });
}

test("release refuses unresolved contact in any built HTML before replacing output", async () => {
  await fixture(async (root) => {
    await mkdir(join(root, "dist", "extra"), { recursive: true });
    await writeFile(join(root, "dist", "extra", "legal.html"), "<main>Contact CONTACT_ADDRESS</main>");
    await assert.rejects(buildVercelOutput({ root, apiUrl: "https://api.example.invalid" }), /placeholder.*extra\/legal.html/i);
    await assert.rejects(readFile(join(root, ".vercel", "output", "config.json")));
  });
});

test("release refuses fixture code in built assets", async () => {
  await fixture(async (root) => {
    await writeFile(join(root, "dist", "assets", "fixture.js"), 'const token = "fixture-csrf";');
    await assert.rejects(buildVercelOutput({ root, apiUrl: "https://api.example.invalid" }), /fixture.*assets\/fixture.js/i);
  });
});

function resolvePath(config, pathname, files) {
  const headers = {};
  for (const route of config.routes) {
    if (route.handle === "filesystem") {
      if (files.includes(pathname)) return { destination: pathname, status: 200, headers };
      continue;
    }
    const match = new RegExp(route.src).exec(pathname);
    if (!match) continue;
    Object.assign(headers, route.headers);
    if (route.continue) continue;
    return {
      destination: route.dest?.replace(/\$(\d+)/g, (_, index) => match[Number(index)] ?? ""),
      status: route.status ?? 200,
      headers,
    };
  }
}

test("ordered output routing preserves files and public documents while missing assets stay 404", async () => {
  await fixture(async (root) => {
    const config = await buildVercelOutput({ root, apiUrl: "https://api.example.invalid" });
    const files = ["/assets/app-example.js", "/index.html", "/terms.html", "/privacy.html"];
    for (const file of files)
      assert.equal(resolvePath(config, file, files).destination, file);
    for (const path of ["/terms", "/terms/", "/privacy", "/privacy/"])
      assert.equal(resolvePath(config, path, files).destination, `${path.replace(/\/$/, "")}.html`);
    for (const path of ["/assets", "/assets/missing.js", "/fonts/missing.woff2", "/images/missing.svg", "/nested/missing.css", "/missing.ico"]) {
      assert.equal(resolvePath(config, path, files).status, 404, path);
      assert.equal(resolvePath(config, path, files).destination, undefined, path);
    }
    for (const path of ["/", "/library", "/workshop/target", "/settings", "/routines", "/playlist/target/setup", "/apiary", "/login-help"])
      assert.equal(resolvePath(config, path, files).destination, "/index.html", path);
    const api = resolvePath(config, "/api/operations/exact-id", files);
    assert.equal(api.destination, "https://api.example.invalid/api/operations/exact-id");
    assert.equal(api.headers["Cache-Control"], "no-store");
    assert.equal(api.headers["CDN-Cache-Control"], "no-store");
    assert.equal(api.headers["Vercel-CDN-Cache-Control"], "no-store");
    assert.equal(api.headers["Content-Security-Policy"], securityHeaders["Content-Security-Policy"]);
  });
});

test("release refuses inline HTML behavior and incomplete public output", async () => {
  await fixture(async (root) => {
    await writeFile(join(root, "dist", "privacy.html"), '<main style="color:red">Privacy</main>');
    await assert.rejects(buildVercelOutput({ root, apiUrl: "https://api.example.invalid" }), /Inline behavior/);
  });
  await fixture(async (root) => {
    await rm(join(root, "dist", "terms.html"));
    await assert.rejects(buildVercelOutput({ root, apiUrl: "https://api.example.invalid" }), /ENOENT/);
  });
});
