import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { createRoutinesFixtures } from "../src/fixtures/routines.ts";
import session from "../fixtures/valid/session-signed-in.json" with { type: "json" };
const output = resolve("../evidence/routines"),
  base = process.env.ROUTINES_EVIDENCE_URL ?? "http://127.0.0.1:4192",
  requests = [];
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
async function connect(context, scenario = "routines-happy") {
  const fixture = createRoutinesFixtures(scenario);
  await context.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname + url.search;
    if (url.pathname === "/api/session")
      return route.fulfill({ json: session });
    const raw = request.postData();
    requests.push({
      method: request.method(),
      path,
      key: request.headers()["idempotency-key"] ?? null,
      body: raw ? JSON.parse(raw) : null,
    });
    const response = await fixture(path, request.method(), {
      body: raw,
      headers: request.headers(),
    });
    if (!response) throw Error("Unexpected API path " + path);
    return route.fulfill({
      status: response.status,
      body: await response.text(),
      headers: {
        "content-type": "application/json",
        "cache-control": "no-store",
      },
    });
  });
}
async function screenshot(page, name, fullPage = true) {
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({ path: resolve(output, name), fullPage });
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
  await page.goto(base + "/routines");
  await page.locator("#routine-3").waitFor();
  await screenshot(page, `routines-${name}-list.png`);
  await page
    .locator("#routine-2")
    .getByRole("button", { name: "History", exact: true })
    .click();
  await page.getByText("Run #1 · success").waitFor();
  await screenshot(page, `routines-${name}-history.png`);
  await page.goto(
    base + "/playlist/target1/routines/new?schedule_id=2&origin=routines",
  );
  await page
    .getByRole("button", { name: "Review routine", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Review and activate", exact: true })
    .waitFor();
  await screenshot(page, `routines-${name}-exact-edit-review.png`);
  await page.goto(base + "/playlist/target1/setup?return_target=routines");
  await page
    .getByLabel("Add by search words", { exact: true })
    .fill("Quiet jazz evenings");
  await page
    .getByRole("button", { name: "Add search source", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Review playlist settings", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Review shared impact", exact: true })
    .waitFor();
  await screenshot(page, `routines-${name}-setup-impact.png`);
  const cdp = await context.newCDPSession(page),
    ax = await cdp.send("Accessibility.getFullAXTree");
  await writeFile(
    resolve(output, `routines-${name}-accessibility.json`),
    JSON.stringify(
      {
        scope:
          "Chromium accessibility-tree spot check; not a screen-reader audio test",
        controls: ax.nodes
          .filter((node) =>
            ["button", "combobox", "checkbox"].includes(node.role?.value),
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
  await context.close();
}
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  colorScheme: "dark",
  reducedMotion: "reduce",
  recordVideo: {
    dir: resolve(output, "raw-video"),
    size: { width: 1440, height: 1000 },
  },
});
await connect(context);
const page = await context.newPage();
await page.goto(base + "/routines");
await page.locator("#routine-1").waitFor();
await page.waitForTimeout(700);
await page.getByLabel("State", { exact: true }).focus();
await page.keyboard.press("p");
await page.keyboard.press("Enter");
await page.waitForTimeout(700);
const history = page
  .locator("#routine-2")
  .getByRole("button", { name: "History", exact: true });
await history.focus();
await page.keyboard.press("Enter");
await page.waitForTimeout(900);
await page.getByLabel("State", { exact: true }).selectOption("");
const run = page
  .locator("#routine-1")
  .getByRole("button", { name: "Run now", exact: true });
await run.focus();
await page.keyboard.press("Enter");
const panel = page.locator(".sh-task-panel:visible");
await panel
  .getByRole("button", { name: "Run reviewed routine", exact: true })
  .focus();
await page.waitForTimeout(700);
await page.keyboard.press("Enter");
await panel.getByRole("heading", { name: /Operation #1001/ }).waitFor();
await page.waitForTimeout(2100);
await page.keyboard.press("Escape");
await page.waitForTimeout(1000);
const video = page.video();
await context.close();
await video.saveAs(resolve(output, "routines-keyboard-review-run.webm"));
await browser.close();
await writeFile(
  resolve(output, "request-proof.json"),
  JSON.stringify(
    {
      scope:
        "Production bundle with synthetic same-origin API interception. No live account, Spotify API traffic, or deployment.",
      requests,
    },
    null,
    2,
  ) + "\n",
);
console.log(
  "Captured 8 responsive screenshots, 2 AX spot checks, keyboard video and exact request proof.",
);
