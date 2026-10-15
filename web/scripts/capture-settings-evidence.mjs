import { chromium } from "@playwright/test";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const output = resolve("../evidence");
await mkdir(output, { recursive: true });
const initial = JSON.parse(
  await readFile(
    new URL("../fixtures/settings/initial.json", import.meta.url),
    "utf8",
  ),
);
const session = JSON.parse(
  await readFile(
    new URL("../fixtures/valid/session-signed-in.json", import.meta.url),
    "utf8",
  ),
);
const base = process.env.SETTINGS_EVIDENCE_URL || "http://127.0.0.1:4176";
const browser = await chromium.launch();
const record = [];
async function connect(context) {
  let settings = structuredClone(initial),
    posts = 0;
  await context.route("**/api/session", (route) =>
    route.fulfill({ json: session }),
  );
  await context.route("**/api/settings", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: settings });
      return;
    }
    posts++;
    record.push({
      method: "POST",
      path: "/api/settings",
      csrfPresent:
        route.request().headers()["x-csrftoken"] === session.csrf_token,
      body: route.request().postDataJSON(),
    });
    if (posts === 1) {
      await route.fulfill({
        status: 400,
        json: {
          success: false,
          error_code: "validation_error",
          category: "error",
          message: "Check your settings.",
          field_errors: [
            {
              path: "max_snapshots_per_playlist",
              message: "Choose at most 20 snapshots.",
            },
          ],
        },
      });
      return;
    }
    settings = {
      ...settings,
      message: "Settings saved successfully.",
      settings: { ...settings.settings, ...route.request().postDataJSON() },
    };
    await route.fulfill({ json: settings });
  });
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
  await page.goto(`${base}/settings`);
  await page
    .getByRole("spinbutton", { name: "Maximum snapshots per playlist" })
    .waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({
    path: resolve(output, `settings-${name}-initial.png`),
    fullPage: true,
  });
  await page
    .getByRole("spinbutton", { name: "Maximum snapshots per playlist" })
    .fill("30");
  await page
    .getByRole("button", { name: "Save settings", exact: true })
    .click();
  await page.locator("#settings-error").waitFor();
  await page.screenshot({
    path: resolve(output, `settings-${name}-validation-focus.png`),
  });
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({
    path: resolve(output, `settings-${name}-validation.png`),
    fullPage: true,
  });
  await page
    .locator("#settings-error")
    .getByRole("link", { name: "Maximum snapshots per playlist" })
    .click();
  await page
    .getByRole("spinbutton", { name: "Maximum snapshots per playlist" })
    .fill("20");
  await page
    .getByRole("button", { name: "Save settings", exact: true })
    .click();
  await page.locator(".sh-toast").waitFor();
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({
    path: resolve(output, `settings-${name}-saved.png`),
    fullPage: true,
  });
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
await page.goto(`${base}/settings`);
await page
  .getByRole("spinbutton", { name: "Maximum snapshots per playlist" })
  .waitFor();
await page.waitForTimeout(1000);
await page.getByRole("combobox", { name: "Default shuffle algorithm" }).focus();
await page.keyboard.press("ArrowDown");
await page.keyboard.press("Tab");
await page.keyboard.press("Space");
await page.keyboard.press("Tab");
await page
  .getByRole("spinbutton", { name: "Maximum snapshots per playlist" })
  .fill("30");
await page.waitForTimeout(700);
await page.getByRole("button", { name: "Save settings", exact: true }).click();
await page.locator("#settings-error").waitFor();
await page.waitForTimeout(1100);
await page
  .locator("#settings-error")
  .getByRole("link", { name: "Maximum snapshots per playlist" })
  .click();
await page.keyboard.press("ControlOrMeta+A");
await page.keyboard.type("20");
await page.keyboard.press("Tab");
await page.keyboard.press("Tab");
await page.keyboard.press("Enter");
await page.locator(".sh-toast").waitFor();
await page.waitForTimeout(1100);
await page
  .getByRole("spinbutton", { name: "Maximum snapshots per playlist" })
  .fill("21");
await page.getByRole("link", { name: "Privacy Policy", exact: true }).click();
await page.getByRole("dialog").waitFor();
await page.waitForTimeout(900);
await page.keyboard.press("Enter");
await page.waitForTimeout(800);
const video = page.video();
await context.close();
await video.saveAs(
  resolve(output, "settings-keyboard-validation-save-guard.webm"),
);
await browser.close();
await writeFile(
  resolve(output, "request-proof.json"),
  JSON.stringify(
    {
      scope:
        "Production build, intercepted same-origin API with synthetic shared fixtures; no live account or deployment.",
      requests: record,
    },
    null,
    2,
  ) + "\n",
);
console.log(
  "Captured six full-page screenshots, two focus viewport screenshots, keyboard video, and actual request bodies.",
);
