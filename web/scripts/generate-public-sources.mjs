import { buildContact } from "./public-contact.mjs";
import { readFile, writeFile, mkdir, readdir, unlink } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../", import.meta.url));
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
const outputArgument = process.argv.find((value) =>
  value.startsWith("--output-dir="),
);
const outputRoot = outputArgument
  ? resolve(outputArgument.slice("--output-dir=".length))
  : root;
const escape = (value) =>
  value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
const contact = escape(buildContact());
const documents = {};
for (const [name, title] of [
  ["terms", "Terms of Service"],
  ["privacy", "Privacy Policy"],
]) {
  const fragment = await readFile(
    resolve(source, `assets/legal/${name}.html`),
    "utf8",
  );
  if (
    /<\s*(?:script|style|iframe|object)\b|\s(?:style|on\w+)\s*=/i.test(fragment)
  )
    throw Error(`Unsafe legal source: ${name}`);
  if (
    !fragment.includes("mailto:CONTACT_ADDRESS") ||
    !fragment.includes(">CONTACT_ADDRESS<")
  )
    throw Error(`Missing legal contact placeholder: ${name}`);
  documents[name] = {
    title,
    html: fragment.replaceAll("CONTACT_ADDRESS", contact),
  };
}
const outputs = {
  "src/pages/public/generated/legal.ts": `/* Generated from assets/legal/*.html. Trusted repository fragments only; never pass runtime HTML here. */\nexport const legalDocuments = ${JSON.stringify(documents, null, 2)} as const;\n`,
};
const directory = resolve(outputRoot, "src/pages/public/generated");
for (const name of await readdir(directory).catch((error) => {
  if (error.code === "ENOENT") return [];
  throw error;
})) {
  if (!Object.hasOwn(outputs, `src/pages/public/generated/${name}`)) {
    if (process.argv.includes("--check"))
      throw Error(`Obsolete public source: ${name}`);
    await unlink(resolve(directory, name));
  }
}
for (const [relative, content] of Object.entries(outputs)) {
  const path = resolve(outputRoot, relative);
  if (process.argv.includes("--check")) {
    if ((await readFile(path, "utf8").catch(() => null)) !== content)
      throw Error(`Missing or stale public source: ${relative}`);
  } else {
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, content);
  }
}
console.log(
  "Verified/generated literal public legal fragments with escaped build contact.",
);
