import {
  readFile,
  writeFile,
  mkdir,
  copyFile,
  readdir,
  unlink,
} from "node:fs/promises";
import { resolve, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../", import.meta.url));
const output = process.argv
  .find((argument) => argument.startsWith("--output-dir="))
  ?.slice("--output-dir=".length);
const outputRoot = output ? resolve(output) : root;
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
const tokenPath =
  process.env.SHUFFIFY_TOKENS_FILE ||
  (local.tokensFile
    ? resolve(root, local.tokensFile)
    : resolve(source, "assets/design/tokens.json"));
const tokens = JSON.parse(await readFile(tokenPath, "utf8"));
for (const [name, font] of Object.entries(tokens.fonts.roles)) {
  if (
    !Array.isArray(font.family) ||
    !font.family.length ||
    font.family.some((item) => typeof item !== "string" || !item.trim())
  )
    throw Error(`Invalid font family: ${name}`);
}
const values = {};
const add = (prefix, group) =>
  Object.entries(group).forEach(([name, value]) => {
    values[`--sh-${prefix}${name}`] = value;
  });
add("", tokens.colors);
add(
  "font-",
  Object.fromEntries(
    Object.entries(tokens.fonts.roles).map(([name, font]) => [
      name,
      font.family
        .map((item) => (item === "sans-serif" ? item : JSON.stringify(item)))
        .join(", "),
    ]),
  ),
);
for (const [name, type] of Object.entries(tokens.type.scale)) {
  values[`--sh-type-${name}`] = type.size;
  values[`--sh-type-leading-${name}`] = type.lineHeight;
}
add("leading-", tokens.type.leading);
add("tracking-", tokens.type.tracking);
add("weight-", tokens.type.weights);
add("space-", tokens.spacing.scale);
for (const [name, key] of Object.entries(tokens.spacing.semantic)) {
  if (!Object.hasOwn(tokens.spacing.scale, key))
    throw Error(`Unknown spacing alias ${name}`);
  values[`--sh-space-${name}`] = `var(--sh-space-${key})`;
}
add("layout-", tokens.layout);
add("radius-", tokens.radii);
add("breakpoint-", tokens.breakpoints);
add("shadow-", tokens.shadows);
add("motion-", tokens.motion.duration);
values["--sh-motion-ease"] = tokens.motion.ease.standard;
if (tokens.radii.artwork !== "4px" || tokens.radii["artwork-wide"] !== "8px")
  throw Error("The two artwork radius tokens are required.");
let css =
  "/* Generated from assets/design/tokens.json; do not edit. */\n:root {\n" +
  Object.entries(values)
    .map(([key, value]) => `  ${key}: ${value};`)
    .join("\n") +
  "\n}\n";
for (const font of Object.values(tokens.fonts.roles)) {
  if (!/^\.\.\/fonts\/[a-z0-9-]+\.woff2$/.test(font.source))
    throw Error(`Invalid local font: ${font.source}`);
  try {
    await readFile(
      resolve(source, "shuffify/static/fonts", basename(font.source)),
    );
  } catch {
    throw Error(`Invalid local font: ${font.source} is missing`);
  }
  css += `@font-face { font-family: ${JSON.stringify(font.family[0])}; font-style: normal; font-weight: ${font.weight}; font-display: ${tokens.fonts.display}; src: url("/fonts/${basename(font.source)}") format("woff2"); unicode-range: ${tokens.fonts.latinRange}; }\n`;
}
css += "@theme inline {\n  --color-*: initial;\n";
for (const name of Object.keys(tokens.colors))
  css += `  --color-sh-${name}: var(--sh-${name});\n`;
for (const name of Object.keys(tokens.fonts.roles))
  css += `  --font-sh-${name}: var(--sh-font-${name});\n`;
for (const name of Object.keys(tokens.type.scale))
  css += `  --text-sh-${name}: var(--sh-type-${name});\n  --text-sh-${name}--line-height: var(--sh-type-leading-${name});\n`;
for (const name of Object.keys(tokens.type.weights))
  css += `  --font-weight-sh-${name}: var(--sh-weight-${name});\n`;
for (const name of Object.keys(tokens.type.tracking))
  css += `  --tracking-sh-${name}: var(--sh-tracking-${name});\n`;
for (const name of Object.keys(tokens.type.leading))
  css += `  --leading-sh-${name}: var(--sh-leading-${name});\n`;
for (const name of Object.keys(tokens.radii))
  css += `  --radius-sh-${name}: var(--sh-radius-${name});\n`;
for (const name of [
  ...Object.keys(tokens.spacing.scale),
  ...Object.keys(tokens.spacing.semantic),
])
  css += `  --spacing-sh-${name}: var(--sh-space-${name});\n`;
for (const name of Object.keys(tokens.layout))
  css += `  --container-sh-${name}: var(--sh-layout-${name});\n`;
for (const name of Object.keys(tokens.shadows))
  css += `  --shadow-sh-${name}: var(--sh-shadow-${name});\n`;
css += "  --ease-sh-standard: var(--sh-motion-ease);\n";
for (const [name, value] of Object.entries(tokens.breakpoints))
  css += `  --breakpoint-${name === "workspace" ? "sh-workspace" : name}: ${value};\n`;
css += "}\n";
for (const name of Object.keys(tokens.motion.duration))
  css += `@utility duration-sh-${name} { transition-duration: var(--sh-motion-${name}); }\n`;
const entry = await readFile(
  resolve(source, "assets/tailwind.src.css"),
  "utf8",
);
let recipes =
  "/* Generated compatibility composition of authoritative After Hours sources. */\n";
for (const match of entry.matchAll(/@import "(.+?)";/g)) {
  const file = resolve(source, "assets", match[1]);
  let content = await readFile(file, "utf8");
  content = content.replace(/@screen ([\w-]+)\s*\{/g, (_whole, name) => {
    const key = name === "sh-workspace" ? "workspace" : name;
    const value = tokens.breakpoints[key];
    if (!value) throw Error(`Unknown @screen ${name}`);
    return `@media (min-width: ${value}) {`;
  });
  if (content.includes("@screen"))
    throw Error("Unconverted legacy screen directive");
  recipes += `/* ${match[1]} */\n${content}\n`;
}
const outputs = {
  "src/design/generated/theme.css": css,
  "src/design/generated/recipes.css": recipes,
};
const generatedDirectory = resolve(outputRoot, "src/design/generated");
for (const name of await readdir(generatedDirectory).catch((error) => {
  if (error.code === "ENOENT") return [];
  throw error;
})) {
  if (!Object.hasOwn(outputs, `src/design/generated/${name}`)) {
    if (process.argv.includes("--check"))
      throw Error(`Obsolete design output: ${name}`);
    await unlink(resolve(generatedDirectory, name));
  }
}
for (const [relative, content] of Object.entries(outputs)) {
  const path = resolve(outputRoot, relative);
  await mkdir(dirname(path), { recursive: true });
  if (process.argv.includes("--check")) {
    if ((await readFile(path, "utf8").catch(() => null)) !== content)
      throw Error(`Missing or stale design output: ${relative}`);
  } else await writeFile(path, content);
}
for (const directory of ["fonts", "images"]) {
  await mkdir(resolve(outputRoot, "public", directory), { recursive: true });
  for (const name of await readdir(
    resolve(source, "shuffify/static", directory),
  )) {
    if (
      directory === "images" &&
      !["spotify-logo-white.svg", "favicon.svg", "favicon.ico"].includes(name)
    )
      continue;
    const from = resolve(source, "shuffify/static", directory, name),
      to = resolve(outputRoot, "public", directory, name);
    if (process.argv.includes("--check")) {
      const existing = await readFile(to).catch(() => null);
      if (!existing || !existing.equals(await readFile(from)))
        throw Error(`Missing/stale asset: ${directory}/${name}`);
    } else await copyFile(from, to);
  }
}
console.log("Verified/generated shared tokens, recipes and local assets.");
