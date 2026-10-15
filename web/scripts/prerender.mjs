import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { publicDocument } from "./public-document.mjs";
import { publicPages, renderPublic } from "../.prerender/prerender.js";
const root = fileURLToPath(new URL("../", import.meta.url));
const template = await readFile(resolve(root, "dist/index.html"), "utf8");
for (const [path, meta] of Object.entries(publicPages)) {
  const body = await renderPublic(path),
    document = publicDocument(template, { ...meta, path, body });
  const output = resolve(
    root,
    "dist",
    path === "/" ? "index.html" : `${path.slice(1)}/index.html`,
  );
  await mkdir(dirname(output), { recursive: true });
  await writeFile(output, document);
  if (path !== "/") {
    // Vite/static hosts can serve extensionless URLs from a sibling .html file;
    // the directory index also keeps direct trailing-slash links useful.
    await writeFile(resolve(root, "dist", `${path.slice(1)}.html`), document);
  }
  console.log(
    `Prerendered ${path}: ${body.length} characters of semantic HTML.`,
  );
}
