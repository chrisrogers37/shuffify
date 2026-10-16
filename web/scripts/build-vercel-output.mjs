import { cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { securityHeaders } from "../security-headers.ts";

const noStore = {
  "Cache-Control": "no-store",
  "CDN-Cache-Control": "no-store",
  "Vercel-CDN-Cache-Control": "no-store",
};

export function apiOrigin(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
      throw new Error("Invalid origin");
    }
    return url.origin;
  } catch {
    throw new Error("RAILWAY_API_URL must be an HTTPS origin without credentials, path, query or fragment.");
  }
}

async function validateRelease(directory, relative = "") {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const name = relative ? `${relative}/${entry.name}` : entry.name;
    const path = join(directory, entry.name);
    if (entry.isSymbolicLink()) throw Error(`Release output cannot contain a symlink: ${name}`);
    if (entry.isDirectory()) {
      await validateRelease(path, name);
    } else if (/\.html?$/i.test(name)) {
      const html = await readFile(path, "utf8");
      if (/CONTACT_ADDRESS/i.test(html))
        throw Error(`Unresolved public contact placeholder in ${name}.`);
      if (/<script(?![^>]*\bsrc=)[^>]*>|<style\b|\s(?:style|on\w+)\s*=/i.test(html))
        throw Error(`Inline behavior is forbidden in release HTML: ${name}`);
    } else if (/\.m?js$/i.test(name)) {
      const script = await readFile(path, "utf8");
      if (["__foundation", "foundation-fixture-intent", "fixture-csrf", "FixtureApp"].some((marker) => script.includes(marker)))
        throw Error(`Fixture code is forbidden in release assets: ${name}`);
    }
  }
}

export async function buildVercelOutput({ root, apiUrl }) {
  const origin = apiOrigin(apiUrl);
  const source = join(root, "dist");
  await Promise.all(["index.html", "terms.html", "privacy.html"].map((name) => readFile(join(source, name))));
  await validateRelease(source);
  const output = join(root, ".vercel", "output");
  const config = {
    version: 3,
    routes: [
      { src: "^/(.*)$", headers: securityHeaders, continue: true },
      { src: "^/(api(?:/.*)?)$", dest: `${origin}/$1`, headers: noStore },
      { src: "^/((?:login|callback|logout|health)/?)$", dest: `${origin}/$1`, headers: noStore },
      { src: "^/terms/?$", dest: "/terms.html" },
      { src: "^/privacy/?$", dest: "/privacy.html" },
      { handle: "filesystem" },
      { src: "^/(?:assets|fonts|images)(?:/.*)?$", status: 404 },
      { src: "^/.*\\.[^/]+$", status: 404 },
      { src: "^/.*$", dest: "/index.html" },
    ],
  };
  await rm(output, { recursive: true, force: true });
  await mkdir(output, { recursive: true });
  await cp(source, join(output, "static"), { recursive: true });
  await writeFile(join(output, "config.json"), JSON.stringify(config, null, 2) + "\n");
  return config;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  await buildVercelOutput({ root: process.cwd(), apiUrl: process.env.RAILWAY_API_URL });
  console.log("Built static Vercel output and same-origin API routes.");
}
