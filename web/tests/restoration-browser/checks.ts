import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
const errors = new WeakMap<Page, string[]>();
export function checks() {
  test.beforeEach(async ({ page }) => {
    const issues: string[] = [];
    errors.set(page, issues);
    page.on("pageerror", (error) => issues.push(error.message));
    page.on("console", (message) => {
      if (
        message.type() === "error" &&
        !message.text().includes("Failed to load resource")
      )
        issues.push(message.text());
    });
    await page.addInitScript(() => {
      (window as unknown as { violations: string[] }).violations = [];
      document.addEventListener("securitypolicyviolation", (event) =>
        (window as unknown as { violations: string[] }).violations.push(
          event.violatedDirective,
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
        nodes.flatMap((node) => {
          if (
            !(node instanceof HTMLElement) ||
            node.closest("[hidden],[inert]") ||
            node.getAttribute("href") === "#main-content"
          )
            return [];
          const target =
            node instanceof HTMLInputElement && node.type === "checkbox"
              ? (node.labels?.[0] ?? node)
              : node;
          const rect = target.getBoundingClientRect();
          return !rect.width ||
            !rect.height ||
            getComputedStyle(node).visibility === "hidden" ||
            (rect.width >= 43.9 && rect.height >= 43.9)
            ? []
            : [node.textContent?.trim() ?? node.tagName];
        }),
      );
    expect(small).toEqual([]);
  });
}
