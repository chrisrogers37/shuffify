import { chromium } from "@playwright/test";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const output = resolve("../evidence");
await mkdir(output, { recursive: true });
const initial = JSON.parse(
  await readFile(
    new URL("../fixtures/activity/initial.json", import.meta.url),
    "utf8",
  ),
);
const session = JSON.parse(
  await readFile(
    new URL("../fixtures/valid/session-signed-in.json", import.meta.url),
    "utf8",
  ),
);
const base = process.env.ACTIVITY_EVIDENCE_URL || "http://127.0.0.1:4178",
  requests = [];
const browser = await chromium.launch();
async function connect(context) {
  await context.route("**/api/session", (route) =>
    route.fulfill({ json: session }),
  );
  await context.route("**/api/activity", (route) => {
    requests.push({ method: route.request().method(), path: "/api/activity" });
    return route.fulfill({ json: initial });
  });
}
async function full(page, name) {
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({ path: resolve(output, name), fullPage: true });
}
for (const [name, viewport] of [
  ["mobile", { width: 360, height: 800 }],
  ["desktop", { width: 1440, height: 1000 }],
]) {
  const context = await browser.newContext({
    viewport,
    colorScheme: "dark",
    reducedMotion: "reduce",
  });
  await connect(context);
  const page = await context.newPage();
  await page.goto(`${base}/activity`);
  await page.locator("#run-7").waitFor();
  await page.evaluate(() => document.fonts.ready);
  await full(page, `activity-${name}-initial.png`);
  await page
    .getByRole("combobox", { name: "Show runs", exact: true })
    .selectOption("unknown");
  await page.locator("#run-3 summary").focus();
  await page.keyboard.press("Enter");
  await page.locator("#run-3").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: resolve(output, `activity-${name}-detail-focus.png`),
  });
  await full(page, `activity-${name}-detail.png`);
  const cdp = await context.newCDPSession(page),
    ax = await cdp.send("Accessibility.getFullAXTree");
  await writeFile(
    resolve(output, `activity-${name}-accessibility.json`),
    JSON.stringify(
      {
        scope:
          "Native Chromium accessibility tree spot check; not a screen-reader audio test.",
        controls: ax.nodes
          .filter((node) =>
            ["DisclosureTriangle", "combobox"].includes(node.role?.value),
          )
          .map((node) => ({
            role: node.role.value,
            name: node.name?.value,
            properties: node.properties,
          })),
      },
      null,
      2,
    ) + "\n",
  );
  await page
    .getByRole("combobox", { name: "Operation", exact: true })
    .selectOption("shuffle");
  await full(page, `activity-${name}-no-matches.png`);
  await context.close();
}
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  recordVideo: {
    dir: resolve(output, "raw-video"),
    size: { width: 1440, height: 1000 },
  },
  colorScheme: "dark",
  reducedMotion: "reduce",
});
await connect(context);
const page = await context.newPage();
await page.goto(`${base}/activity`);
await page.locator("#run-7").waitFor();
await page.waitForTimeout(1000);
const runs = page.getByRole("combobox", { name: "Show runs", exact: true });
await runs.focus();
await page.keyboard.press("p");
await page.keyboard.press("Enter");
await page.waitForTimeout(800);
const summary = page.locator("#run-2 summary");
await summary.focus();
await page.keyboard.press("Enter");
await page.waitForTimeout(1100);
await page.keyboard.press("Tab");
await page.waitForTimeout(600);
await page.keyboard.press("Shift+Tab");
await page.keyboard.press("Enter");
await page.waitForTimeout(700);
const events = page.getByRole("combobox", { name: "Show events", exact: true });
await events.focus();
await page.keyboard.press("m");
await page.keyboard.press("Enter");
await page.waitForTimeout(1000);
await page
  .getByRole("combobox", { name: "Operation", exact: true })
  .selectOption("rotate");
await page.waitForTimeout(1000);
const video = page.video();
await context.close();
await video.saveAs(resolve(output, "activity-keyboard-filter-details.webm"));
await browser.close();
await writeFile(
  resolve(output, "request-proof.json"),
  JSON.stringify(
    {
      scope:
        "Production bundle with synthetic same-origin API interceptions. Filters, details and links issue no further request. No live account or deployment.",
      requests,
    },
    null,
    2,
  ) + "\n",
);
console.log(
  "Captured six full-page screenshots, two focus viewport screenshots, AX spot checks, keyboard video and read-only request proof.",
);
