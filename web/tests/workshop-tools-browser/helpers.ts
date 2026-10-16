import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { createWorkshopFixtures } from "../../src/fixtures/workshop";
import { createWorkshopToolsFixtures } from "../../src/fixtures/workshop-tools";
import signedIn from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
export const errors = new WeakMap<Page, string[]>();
export const callsByPage = new WeakMap<
  Page,
  { path: string; method: string; body: string | null; key: string | null }[]
>();
export function checks() {
  test.beforeEach(async ({ page }) => {
    const issues: string[] = [];
    errors.set(page, issues);
    page.on("pageerror", (e) => issues.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error" && !m.text().includes("Failed to load resource"))
        issues.push(m.text());
    });
    await page.addInitScript(() => {
      (window as unknown as { violations: string[] }).violations = [];
      document.addEventListener("securitypolicyviolation", (e) =>
        (window as unknown as { violations: string[] }).violations.push(
          e.violatedDirective + ":" + e.blockedURI,
        ),
      );
    });
  });
  test.afterEach(async ({ page }) => {
    expect(errors.get(page)).toEqual([]);
    expect(
      await page.evaluate(
        () => (window as unknown as { violations: string[] }).violations,
      ),
    ).toEqual([]);
    expect(
      await page
        .locator('script:not([src]),style,[style]:not([style=""])')
        .count(),
    ).toBe(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    const small = await page
      .locator("button,input,textarea,select,summary,a[href]")
      .evaluateAll((nodes) =>
        nodes.flatMap((n) => {
          if (
            !(n instanceof HTMLElement) ||
            n.closest("[hidden],[inert]") ||
            n.getAttribute("href") === "#main-content"
          )
            return [];
          const target =
              n instanceof HTMLInputElement && n.type === "checkbox"
                ? (n.labels?.[0] ?? n)
                : n,
            r = target.getBoundingClientRect();
          return !r.width ||
            !r.height ||
            getComputedStyle(n).visibility === "hidden" ||
            (r.width >= 43.9 && r.height >= 43.9)
            ? []
            : [n.textContent?.trim() ?? n.tagName];
        }),
      );
    expect(small).toEqual([]);
  });
}
export async function start(
  page: Page,
  scenario = "workshop-tools",
  path = "/workshop/target",
) {
  const core = createWorkshopFixtures("workshop-happy"),
    tools = createWorkshopToolsFixtures(scenario, core),
    calls: {
      path: string;
      method: string;
      body: string | null;
      key: string | null;
    }[] = [];
  callsByPage.set(page, calls);
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      u = new URL(req.url()),
      path = u.pathname + u.search,
      method = req.method(),
      init = {
        method,
        headers: new Headers(req.headers()),
        body: req.postData(),
      };
    calls.push({
      path,
      method,
      body: req.postData(),
      key: req.headers()["idempotency-key"] ?? null,
    });
    const response =
      (await tools(path, method, init)) ??
      (await core(path, method, init)) ??
      new Response(
        JSON.stringify(
          u.pathname === "/api/session"
            ? signedIn
            : {
                success: false,
                error_code: "missing_fixture",
                message: `Missing fixture: ${path}`,
                category: "error",
              },
        ),
        {
          status: u.pathname === "/api/session" ? 200 : 404,
          headers: { "Content-Type": "application/json" },
        },
      );
    await route.fulfill({
      status: response.status,
      body: await response.text(),
      headers: (() => {
        const headers: Record<string, string> = {};
        response.headers.forEach((value, key) => (headers[key] = value));
        return headers;
      })(),
    });
  });
  await page.goto(path);
  await expect(
    page.getByRole("heading", { name: "Evening Collection", exact: true }),
  ).toBeVisible();
  return calls;
}
export async function open(page: Page, name: string) {
  const target = page.getByRole("button", { name, exact: true });
  if (!(await target.isVisible()))
    await page.getByRole("button", { name: "Tools", exact: true }).click();
  await target.click();
  await expect(
    page
      .locator("section.sh-task-panel:not([hidden])")
      .getByRole("heading", { name, exact: true }),
  ).toBeVisible();
}
export async function close(page: Page, one = false) {
  do {
    const active = panel(page);
    if (!(await active.count())) return;
    await active.getByRole("button", { name: "Close", exact: true }).click();
    if (one) return;
  } while (await panel(page).count());
}
export const panel = (page: Page) =>
  page.locator("section.sh-task-panel:not([hidden])");
