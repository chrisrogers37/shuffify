import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { createLibraryFixtures } from "../src/fixtures/library.ts";
import session from "../fixtures/valid/session-signed-in.json" with { type: "json" };
const base = process.env.LIBRARY_EVIDENCE_URL || "http://127.0.0.1:4180";
const output = resolve("../evidence");
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
const requests = [];
async function connect(context, scenario = "happy") {
  const handler = createLibraryFixtures(scenario);
  await context.route("**/api/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      path = url.pathname + url.search,
      headers = new Headers(req.headers());
    let body = req.postData() ?? undefined,
      fields = null;
    if (
      body &&
      headers.get("content-type")?.startsWith("multipart/form-data")
    ) {
      body = await new Request(req.url(), {
        method: req.method(),
        headers,
        body,
      }).formData();
      fields = Object.fromEntries(body);
    } else if (body) fields = JSON.parse(body);
    requests.push({
      scenario,
      path,
      method: req.method(),
      fields,
      key: headers.get("Idempotency-Key"),
      csrf: headers.get("X-CSRFToken"),
    });
    if (path === "/api/session") return route.fulfill({ json: session });
    const response = await handler(path, req.method(), {
      headers,
      ...(body ? { body } : {}),
    }).catch(() => null);
    if (!response) return route.abort();
    await route.fulfill({
      status: response.status,
      headers: Object.fromEntries(response.headers),
      body: await response.text(),
    });
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
  await page.goto(`${base}/library`);
  await page.locator('[data-playlist-id="alice-mix"]').waitFor();
  await page.evaluate(() => document.fonts.ready);
  await full(page, `library-${name}-initial.png`);
  await page.getByRole("button", { name: "Organize", exact: true }).click();
  await full(page, `library-${name}-organize.png`);
  await page
    .getByRole("button", { name: "Done organizing", exact: true })
    .click();
  const row = page.locator('[data-playlist-id="alice-mix"]');
  await row.locator(">details>summary").click();
  await row
    .getByRole("button", { name: "Shuffle now Evening rotation", exact: true })
    .click();
  const panel = page.locator(".sh-task-panel:visible");
  await panel
    .getByRole("button", { name: "Run shuffle", exact: true })
    .waitFor();
  await full(page, `library-${name}-review.png`);
  const cdp = await context.newCDPSession(page),
    ax = await cdp.send("Accessibility.getFullAXTree");
  await writeFile(
    resolve(output, `library-${name}-accessibility.json`),
    JSON.stringify(
      {
        scope:
          "Native Chromium accessibility-tree spot check, not a screen-reader audio session.",
        controls: ax.nodes
          .filter((n) =>
            ["button", "DisclosureTriangle", "combobox"].includes(
              n.role?.value,
            ),
          )
          .map((n) => ({
            role: n.role.value,
            name: n.name?.value,
            properties: n.properties,
          })),
      },
      null,
      2,
    ) + "\n",
  );
  await panel.getByRole("button", { name: "Run shuffle", exact: true }).click();
  await panel
    .getByText("The playlist change was verified.", { exact: true })
    .waitFor();
  await full(page, `library-${name}-result.png`);
  await context.close();
  const unknown = await browser.newContext({
    viewport,
    colorScheme: "dark",
    reducedMotion: "reduce",
  });
  await connect(unknown, "library-unknown");
  const unknownPage = await unknown.newPage();
  await unknownPage.goto(`${base}/library`);
  const unknownRow = unknownPage.locator('[data-playlist-id="alice-mix"]');
  await unknownRow.locator(">details>summary").click();
  await unknownRow
    .getByRole("button", { name: "Shuffle now Evening rotation", exact: true })
    .click();
  await unknownPage
    .getByRole("button", { name: "Run shuffle", exact: true })
    .click();
  await unknownPage
    .getByRole("button", { name: "Retry this exact request", exact: true })
    .waitFor();
  await full(unknownPage, `library-${name}-unknown.png`);
  await unknown.close();
}
const videoContext = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  colorScheme: "dark",
  reducedMotion: "reduce",
  recordVideo: {
    dir: resolve(output, "raw-video"),
    size: { width: 1440, height: 1000 },
  },
});
await connect(videoContext);
const page = await videoContext.newPage();
await page.goto(`${base}/library`);
const organize = page.getByRole("button", { name: "Organize", exact: true });
await organize.focus();
await page.waitForTimeout(600);
await page.keyboard.press("Enter");
const down = page
  .locator('[data-playlist-id="bob-mix"]')
  .getByRole("button", { name: "Move down Weekend discoveries", exact: true });
await down.focus();
await page.waitForTimeout(600);
await page.keyboard.press("Enter");
await page.waitForTimeout(700);
await page
  .getByRole("button", { name: "Done organizing", exact: true })
  .focus();
await page.keyboard.press("Enter");
const row = page.locator('[data-playlist-id="alice-mix"]');
await row.locator(">details>summary").focus();
await page.keyboard.press("Enter");
await row
  .getByRole("button", { name: "Shuffle now Evening rotation", exact: true })
  .focus();
await page.keyboard.press("Enter");
const panel = page.locator(".sh-task-panel:visible");
await panel.getByRole("button", { name: "Run shuffle", exact: true }).waitFor();
await page.waitForTimeout(900);
await panel.getByRole("button", { name: "Run shuffle", exact: true }).focus();
await page.keyboard.press("Enter");
await panel
  .getByText("The playlist change was verified.", { exact: true })
  .waitFor();
await page.waitForTimeout(1000);
await panel.getByRole("button", { name: "Close", exact: true }).focus();
await page.keyboard.press("Enter");
await page.waitForTimeout(800);
const video = page.video();
await videoContext.close();
await video.saveAs(resolve(output, "library-keyboard.webm"));
await browser.close();
await writeFile(
  resolve(output, "requests.json"),
  JSON.stringify(
    {
      scope:
        "Synthetic responses under production CSP; no live Spotify writes.",
      requests,
    },
    null,
    2,
  ) + "\n",
);
console.log(
  `Wrote Library screenshots, keyboard video, AX tree and request proof to ${output}`,
);
