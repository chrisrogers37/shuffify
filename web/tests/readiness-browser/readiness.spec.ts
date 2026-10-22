import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { securityHeaders } from "../../security-headers";
import signedIn from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
import signedOut from "../../fixtures/valid/session-signed-out.json" with { type: "json" };
import { createSettingsFixtures } from "../../src/fixtures/settings";
import { createActivityFixtures } from "../../src/fixtures/activity";
import { createLibraryFixtures } from "../../src/fixtures/library";
import { createRoutinesFixtures } from "../../src/fixtures/routines";
import { createWorkshopFixtures } from "../../src/fixtures/workshop";
import { createWorkshopToolsFixtures } from "../../src/fixtures/workshop-tools";
import { createWorkshopSourcesFixtures } from "../../src/fixtures/workshop-sources";

async function endpoints(page: Page, signed: boolean) {
  const core = createWorkshopFixtures("workshop-happy");
  const handlers = [
    createWorkshopSourcesFixtures("workshop-sources"),
    createWorkshopToolsFixtures("workshop-tools", core),
    core,
    createSettingsFixtures("happy"),
    createActivityFixtures("happy"),
    createLibraryFixtures("happy"),
    createRoutinesFixtures("routines-happy"),
  ];
  const unexpected: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname === "/api/session")
      return route.fulfill({ json: signed ? signedIn : signedOut });
    const path = url.pathname + url.search;
    for (const handle of handlers) {
      const response = await handle(path, request.method(), {
        headers: request.headers(),
        ...(request.postData() ? { body: request.postData()! } : {}),
      });
      if (response) return route.fulfill({
        status: response.status, body: await response.text(),
        headers: (() => { const values: Record<string, string> = {}; response.headers.forEach((value, name) => { values[name] = value; }); return values; })(),
      });
    }
    unexpected.push(path);
    return route.fulfill({ status: 404, json: { success: false, error_code: "missing_fixture", message: "Missing recorded endpoint.", category: "error" } });
  });
  return unexpected;
}

const pages = [
  { id: "landing", path: "/", heading: "Evolve your", signed: false },
  { id: "terms", path: "/terms", heading: "Terms of Service", signed: false },
  { id: "privacy", path: "/privacy", heading: "Privacy Policy", signed: false },
  { id: "library", path: "/library", heading: "Your playlists", signed: true },
  { id: "settings", path: "/settings", heading: "Settings", signed: true },
  { id: "activity", path: "/activity", heading: "Activity", signed: true },
  { id: "routines", path: "/routines", heading: "Routines", signed: true },
  { id: "routine-setup", path: "/playlist/target/routines/new?origin=routines", heading: "Create a routine", signed: true },
  { id: "playlist-setup", path: "/playlist/target/setup?return_target=library", heading: "Playlist setup", signed: true },
  { id: "workshop", path: "/workshop", heading: "Workshop", signed: true },
  { id: "workspace", path: "/workshop/target", heading: "Evening Collection", signed: true },
  { id: "not-found", path: "/missing-page", heading: "That page could not be found.", signed: false },
  { id: "signed-out", path: "/settings", heading: "Connect to Spotify", signed: false },
] as const;

for (const item of pages) test(`${item.id} has keyboard, accessibility-tree and preview-header parity`, async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const unexpected = await endpoints(page, item.signed);
  await page.addInitScript(() => {
    (window as unknown as { csp: string[] }).csp = [];
    document.addEventListener("securitypolicyviolation", (event) =>
      (window as unknown as { csp: string[] }).csp.push(`${event.violatedDirective}:${event.blockedURI}`));
  });
  const response = await page.goto(item.path);
  for (const [name, value] of Object.entries(securityHeaders))
    expect(response?.headers()[name.toLowerCase()], name).toBe(value);
  const config = JSON.parse(await readFile(".vercel/output/config.json", "utf8"));
  expect(config.routes[0].headers).toEqual(securityHeaders);
  const heading = page.locator("main h1:visible");
  await expect(heading).toHaveCount(1);
  await expect(heading).toContainText(item.heading);
  if (item.id === "workshop") await expect(page.getByRole("searchbox", { name: "Find a playlist", exact: true })).toBeFocused();
  else await expect(heading).toBeFocused();
  await page.evaluate(async () => document.fonts.ready);
  await expect(page.getByRole("main")).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator('script:not([src]),style,[style]:not([style=""])')).toHaveCount(0);

  const tree = await (await page.context().newCDPSession(page)).send("Accessibility.getFullAXTree");
  const exposed = tree.nodes.filter((node) => !node.ignored).map((node) => ({ role: node.role?.value, name: node.name?.value }));
  expect(exposed.some((node) => node.role === "main")).toBe(true);
  expect(exposed.some((node) => node.role === "heading" && node.name?.includes(item.heading))).toBe(true);
  expect(exposed.filter((node) => ["button", "link", "checkbox", "textbox", "combobox", "spinbutton"].includes(node.role ?? "") && !node.name)).toEqual([]);
  expect(await page.locator("body").ariaSnapshot()).toContain('level=1');

  const mismatches = await page.locator("a[aria-label],button[aria-label],input[aria-label]").evaluateAll((nodes) => nodes.flatMap((node) => {
    if (!(node instanceof HTMLElement) || node.closest("[hidden],[inert]") || !node.getBoundingClientRect().height) return [];
    const clone = node.cloneNode(true) as HTMLElement;
    clone.querySelectorAll('[aria-hidden="true"],svg').forEach((hidden) => hidden.remove());
    const text = clone.textContent?.replace(/\s+/g, " ").trim().toLowerCase() ?? "";
    const name = node.getAttribute("aria-label")?.replace(/\s+/g, " ").trim().toLowerCase() ?? "";
    return text && !name.includes(text) ? [{ text, name }] : [];
  }));
  expect(mismatches).toEqual([]);
  const small = await page.locator("button,input,textarea,select,summary,a[href]").evaluateAll((nodes) => nodes.flatMap((node) => {
    if (!(node instanceof HTMLElement) || node.closest("[hidden],[inert]") || node.getAttribute("href") === "#main-content" || (node instanceof HTMLAnchorElement && node.closest(".sh-consent"))) return [];
    const target = node instanceof HTMLInputElement && node.type === "checkbox" ? (node.labels?.[0] ?? node) : node;
    const rect = target.getBoundingClientRect();
    if (!rect.width || !rect.height || getComputedStyle(node).visibility === "hidden") return [];
    return rect.width >= 43.9 && rect.height >= 43.9 ? [] : [{ label: node.textContent?.trim(), width: rect.width, height: rect.height }];
  }));
  expect(small).toEqual([]);
  for (const artwork of await page.locator("[data-spotify-artwork]").all()) {
    await expect(artwork.getByAltText("Spotify", { exact: true })).toHaveAttribute("src", "/images/spotify-logo-white.svg");
    await expect(artwork.locator('a[href^="https://open.spotify.com/"]')).toHaveCount(1);
    expect(await artwork.locator(".sh-artwork-image").evaluate((node) => getComputedStyle(node).borderRadius)).toBe(info.project.name.endsWith("360") ? "4px" : "8px");
  }
  const first = page.locator("main button:visible:not(:disabled), main input:visible:not(:disabled), main select:visible:not(:disabled), main a[href]:visible, main summary:visible").first();
  if (await first.count()) {
    await first.focus();
    await expect(first).toBeFocused();
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => { const node = document.activeElement; return node instanceof HTMLElement && node !== document.body && !!node.getBoundingClientRect().height; })).toBe(true);
    expect(await page.evaluate(() => { const node = document.activeElement!; const style = getComputedStyle(node); return (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0) || style.boxShadow !== "none"; })).toBe(true);
  }
  expect(errors).toEqual([]);
  expect(unexpected).toEqual([]);
  expect(await page.evaluate(() => (window as unknown as { csp: string[] }).csp)).toEqual([]);
  const directory = resolve("evidence/readiness", info.project.name);
  await mkdir(directory, { recursive: true });
  await writeFile(resolve(directory, `${item.id}-accessibility.json`), JSON.stringify(exposed, null, 2));
  await page.screenshot({ path: resolve(directory, `${item.id}.png`), fullPage: true, animations: "disabled" });
});

test("workspace tools return keyboard focus to their trigger", async ({ page }) => {
  await endpoints(page, true);
  await page.goto("/workshop/target");
  const tools = page.getByRole("button", { name: "Tools", exact: true });
  await tools.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Workshop tools", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(tools).toBeFocused();
  await expect(page.locator("section.sh-task-panel:visible")).toHaveCount(0);
});

test("keyboard settings save announces one result without replaying the POST", async ({ page }) => {
  const posts: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/settings" && request.method() === "POST") posts.push(request.postData() ?? "");
  });
  await endpoints(page, true);
  await page.goto("/settings");
  const maximum = page.getByRole("spinbutton", { name: "Maximum snapshots per playlist" });
  await maximum.fill("11");
  await page.getByRole("button", { name: "Save settings", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator('[aria-live="polite"]')).toContainText("Settings saved successfully.");
  expect(posts).toHaveLength(1);
  expect(JSON.parse(posts[0]!).max_snapshots_per_playlist).toBe(11);
  await expect(maximum).toHaveValue("11");
});
