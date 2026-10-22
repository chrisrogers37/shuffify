import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
declare global {
  interface Window {
    __publicCspViolations: string[];
    __publicOriginalHeading: Element | null;
    __publicLiveUpdates: string[];
  }
}
const signedOut = JSON.parse(
  await readFile(resolve("fixtures/valid/session-signed-out.json"), "utf8"),
);
const signedIn = JSON.parse(
  await readFile(resolve("fixtures/valid/session-signed-in.json"), "utf8"),
);
const failures = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => {
  const captured: string[] = [];
  failures.set(page, captured);
  page.on("pageerror", (error) => captured.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") captured.push(message.text());
  });
  page.on("request", (request) => {
    if (/^https:\/\/(api|accounts)\.spotify\.com\//.test(request.url()))
      captured.push(`Unexpected Spotify request ${request.url()}`);
  });
  await page.addInitScript(() => {
    window.__publicCspViolations = [];
    window.__publicOriginalHeading = null;
    const observer = new MutationObserver(() => {
      const heading = document.querySelector("main h1");
      if (heading) {
        window.__publicOriginalHeading = heading;
        observer.disconnect();
      }
    });
    observer.observe(document, { childList: true, subtree: true });
    document.addEventListener("securitypolicyviolation", (event) =>
      window.__publicCspViolations.push(
        `${event.violatedDirective}:${event.blockedURI}`,
      ),
    );
  });
  await page.route("**/api/session", (route) =>
    route.fulfill({ json: signedOut }),
  );
});
test.afterEach(async ({ page }) => {
  expect.soft(failures.get(page) ?? []).toEqual([]);
  if (!page.isClosed()) {
    expect
      .soft(await page.evaluate(() => window.__publicCspViolations ?? []))
      .toEqual([]);
    expect
      .soft(
        await page
          .locator('script:not([src]),style,[style]:not([style=""])')
          .count(),
      )
      .toBe(0);
  }
});
async function stable(page: Page, path = "/") {
  const response = await page.goto(path);
  await expect(page.locator("main h1")).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return response;
}
async function targets(page: Page) {
  const small = await page
    .locator("a[href],button,input,summary")
    .evaluateAll((nodes) =>
      nodes.flatMap((node) => {
        const target =
          node instanceof HTMLInputElement && node.type === "checkbox"
            ? (node.labels?.[0] ?? node)
            : node;
        if (
          !(target instanceof HTMLElement) ||
          target.closest("[hidden],[inert]") ||
          target.getAttribute("href") === "#main-content"
        )
          return [];
        const box = target.getBoundingClientRect(),
          style = getComputedStyle(target);
        if (
          box.width === 0 ||
          box.height === 0 ||
          style.visibility === "hidden"
        )
          return [];
        return box.width >= 43.9 && box.height >= 43.9
          ? []
          : [
              {
                name: target.textContent?.trim(),
                width: box.width,
                height: box.height,
              },
            ];
      }),
    );
  expect(small).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
}

test("landing is prerendered, hydrates without replacing content and keeps the public shell", async ({
  page,
}, info) => {
  let sessions = 0;
  await page.route("**/api/session", (route) => {
    sessions++;
    return route.fulfill({ json: signedOut });
  });
  const response = await stable(page);
  expect(await response?.text()).toContain("Evolve your");
  expect(response?.headers()["content-security-policy"]).toContain(
    "script-src 'self'",
  );
  expect(response?.headers()["content-security-policy"]).not.toContain(
    "unsafe-inline",
  );
  await expect(page).toHaveTitle("Shuffify: Evolve your collection.");
  await expect(page.locator("#hero-title")).toBeFocused();
  expect(
    await page
      .locator("#hero-title")
      .evaluate((node) => node === window.__publicOriginalHeading),
  ).toBe(true);
  await expect(
    page.getByRole("navigation", { name: "Public navigation" }),
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Main navigation" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Preview Shuffle", exact: true }),
  ).toBeVisible();
  expect(sessions).toBe(1);
  await targets(page);
  await expect(page.locator("script:not([src]),style,[style]")).toHaveCount(0);
  await mkdir("evidence", { recursive: true });
  await page.screenshot({
    path: resolve("evidence", `${info.project.name}-landing.png`),
    fullPage: true,
    animations: "disabled",
  });
});

for (const item of [
  { path: "/terms", title: "Terms of Service", sections: 10 },
  { path: "/privacy", title: "Privacy Policy", sections: 11 },
])
  test(`${item.path} serves complete legal HTML and accessible current navigation`, async ({
    page,
  }, info) => {
    const response = await stable(page, item.path);
    expect(await response?.text()).toContain("Last updated: October 14, 2026");
    await expect(page).toHaveTitle(`${item.title} - Shuffify`);
    await expect(page.locator("main h1")).toHaveText(item.title);
    await expect(page.locator("main h1")).toBeFocused();
    expect(await page.locator(".legal-copy section").count()).toBe(
      item.sections,
    );
    await expect(
      page.locator('.legal-copy a[href="mailto:support@example.invalid"]'),
    ).toHaveText("support@example.invalid");
    const header = page.getByRole("navigation", { name: "Public navigation" });
    await expect(header.locator('[aria-current="page"]')).toHaveCount(1);
    await targets(page);
    await mkdir("evidence", { recursive: true });
    await page.screenshot({
      path: resolve(
        "evidence",
        `${info.project.name}-${item.path.slice(1)}.png`,
      ),
      fullPage: true,
      animations: "disabled",
    });
    await page
      .getByRole("link", { name: "← Back to Shuffify", exact: true })
      .click();
    await expect(page).toHaveURL("/");
    await expect(page.locator("#hero-title")).toBeFocused();
    await expect(page.locator("#hero-title")).toBeInViewport();
  });

test("consent remains keyboard operated and submits only the native login contract", async ({
  page,
}) => {
  let destination = "";
  await page.route("**/login?*", (route) => {
    destination = route.request().url();
    return route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><html><body><h1>Auth handoff</h1></body></html>",
    });
  });
  await stable(page);
  const consent = page.getByRole("checkbox"),
    connect = page.getByRole("button", {
      name: "Connect with Spotify to start shuffling playlists",
      exact: true,
    });
  await expect(connect).toBeDisabled();
  await consent.focus();
  await page.keyboard.press("Space");
  await expect(consent).toBeChecked();
  await expect(connect).toBeEnabled();
  await expect(page.locator("#cta-subtext")).toHaveText(
    "Free · Ready to Start",
  );
  await connect.focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Auth handoff" }),
  ).toBeVisible();
  const url = new URL(destination);
  expect(url.pathname).toBe("/login");
  expect([...url.searchParams]).toEqual([["legal_consent", "1"]]);
});

test("consent checkbox is centred on the first text line and links keep 44px targets", async ({
  page,
}) => {
  await stable(page);
  const consent = page.getByRole("checkbox");
  const offset = await consent.evaluate((input) => {
    const box = input.getBoundingClientRect();
    const text = input.nextElementSibling as HTMLElement;
    const range = document.createRange();
    range.setStart(text.firstChild as Node, 0);
    range.setEnd(text.firstChild as Node, 1);
    const line = range.getClientRects()[0]!;
    return Math.abs(box.top + box.height / 2 - (line.top + line.height / 2));
  });
  expect(offset).toBeLessThanOrEqual(3);
  for (const name of ["Terms of Service", "Privacy Policy"]) {
    const link = await page
      .locator(".sh-landing-consent")
      .getByRole("link", { name, exact: true })
      .boundingBox();
    expect(link!.height).toBeGreaterThanOrEqual(44);
  }
});

test("consent legal links keep the form open in the original page", async ({
  page,
  context,
}) => {
  await stable(page);
  const popupPromise = context.waitForEvent("page");
  await page
    .locator(".sh-landing-consent")
    .getByRole("link", { name: "Terms of Service", exact: true })
    .click();
  const popup = await popupPromise;
  await popup.waitForLoadState();
  await expect(popup.locator("main h1")).toHaveText("Terms of Service");
  expect(page.url()).toMatch(/\/$/);
  await expect(page.getByRole("checkbox")).not.toBeChecked();
  await popup.close();
});

test("shuffle preview and undo preserve fictional occurrences, focus and reduced motion", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await stable(page);
  const rows = page.locator("[data-demo-track]"),
    names = page.locator(".sh-landing-track-name"),
    before = await names.allTextContents();
  const shuffle = page.getByRole("button", {
    name: "Preview Shuffle",
    exact: true,
  });
  await shuffle.click();
  await expect(page.locator("#shuffle-demo")).toHaveAttribute(
    "data-demo-state",
    "shuffled",
  );
  await expect(page.locator("#shuffle-track-list")).toHaveAttribute(
    "aria-busy",
    "false",
  );
  expect(await names.allTextContents()).toEqual([
    "Golden Hour",
    "Ocean Waves",
    "Midnight Drive",
    "Cosmic Drift",
    "Electric Dreams",
  ]);
  expect(await rows.first().getAttribute("style")).toBeNull();
  await expect(shuffle).toBeFocused();
  await expect(page.locator('[aria-live="polite"]')).toContainText(
    "Sample playlist reordered.",
  );
  const undo = page.getByRole("button", { name: "Undo Changes", exact: true });
  await undo.click();
  await expect(page.locator("#shuffle-demo")).toHaveAttribute(
    "data-demo-state",
    "original",
  );
  expect(await names.allTextContents()).toEqual(before);
  await expect(undo).toBeFocused();
  await expect(page.locator('[aria-live="polite"]')).toContainText(
    "Original sample order restored.",
  );
  expect(
    await page
      .locator("#shuffle-track-list")
      .evaluate((node) => node.getAnimations({ subtree: true }).length),
  ).toBe(0);
});

test("native jump links target the named section and restore the hero", async ({
  page,
}) => {
  await stable(page);
  await page.getByRole("link", { name: "Find new songs", exact: true }).click();
  await expect(page).toHaveURL("/#raid");
  await expect(page.locator("#raid")).toBeFocused();
  await page.getByRole("link", { name: "Connect", exact: true }).click();
  await expect(page).toHaveURL("/#hero-title");
  await expect(page.locator("#hero-title")).toBeFocused();
});

test("official Spotify attribution and two artwork radius tokens survive responsive layout", async ({
  page,
}) => {
  await stable(page);
  const logo = page.locator(".sh-landing-attribution img");
  await expect(logo).toHaveAttribute("src", "/images/spotify-logo-white.svg");
  expect(
    await logo.evaluate((node) => node.getBoundingClientRect().width),
  ).toBeGreaterThanOrEqual(70);
  await expect(page.locator(".sh-landing-attribution a")).toHaveAttribute(
    "href",
    "https://www.spotify.com/",
  );
  const clearSpace = await logo.evaluate((node) => {
    const image = node.getBoundingClientRect(),
      style = getComputedStyle(node.parentElement!);
    return {
      halfHeight: image.height / 2,
      padding: [
        style.paddingTop,
        style.paddingRight,
        style.paddingBottom,
        style.paddingLeft,
      ].map((value) => Number.parseFloat(value)),
    };
  });
  expect(
    clearSpace.padding.every((value) => value >= clearSpace.halfHeight),
  ).toBe(true);
  const radius = (page.viewportSize()?.width ?? 0) < 1024 ? "4px" : "8px";
  expect(
    await page
      .locator(".sh-landing-cover")
      .first()
      .evaluate((node) => getComputedStyle(node).borderRadius),
  ).toBe(radius);
  expect(
    await page
      .locator(".sh-song-cover")
      .first()
      .evaluate((node) => getComputedStyle(node).borderRadius),
  ).toBe(radius);
  await targets(page);
});

test("all public pages retain useful content, native links and consent with JavaScript disabled", async ({
  browser,
}, info) => {
  const context = await browser.newContext({
    javaScriptEnabled: false,
    viewport: info.project.use.viewport ?? { width: 360, height: 800 },
  });
  const page = await context.newPage();
  try {
    await page.goto("http://127.0.0.1:4174/");
    await expect(page.locator("#hero-title")).toHaveText(
      "Evolve yourcollection.",
    );
    await expect(page.locator("#shuffle-demo-controls")).toBeHidden();
    await expect(page.locator("[data-demo-track]")).toHaveCount(5);
    await expect(page.locator("#shuffle-track-list")).toHaveAttribute(
      "start",
      "753",
    );
    const consent = page.getByRole("checkbox"),
      connect = page.getByRole("button", {
        name: "Connect with Spotify to start shuffling playlists",
        exact: true,
      });
    await expect(connect).toBeEnabled();
    await connect.click();
    expect(page.url()).toBe("http://127.0.0.1:4174/");
    expect(
      await consent.evaluate(
        (node) => (node as HTMLInputElement).validity.valueMissing,
      ),
    ).toBe(true);
    await page
      .getByRole("navigation", { name: "Public navigation" })
      .getByRole("link", { name: "Terms", exact: true })
      .click();
    await expect(page.locator("main h1")).toHaveText("Terms of Service");
    await page
      .getByRole("navigation", { name: "Public navigation" })
      .getByRole("link", { name: "Privacy", exact: true })
      .click();
    await expect(page.locator("main h1")).toHaveText("Privacy Policy");
    await expect(
      page.getByText("Last updated: October 14, 2026", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("link", { name: "Shuffify home", exact: true })
      .click();
    let loginUrl = "";
    await page.route("**/login?*", (route) => {
      loginUrl = route.request().url();
      return route.fulfill({
        contentType: "text/html",
        body: "<!doctype html><html><body><h1>Native auth handoff</h1></body></html>",
      });
    });
    await page.getByRole("checkbox").check();
    await page
      .getByRole("button", {
        name: "Connect with Spotify to start shuffling playlists",
        exact: true,
      })
      .click();
    await expect(
      page.getByRole("heading", { name: "Native auth handoff", exact: true }),
    ).toBeVisible();
    expect([...new URL(loginUrl).searchParams]).toEqual([
      ["legal_consent", "1"],
    ]);
  } finally {
    await context.close();
  }
});

test("signed-in index waits for the authoritative session then navigates to the fixed local library", async ({
  page,
}) => {
  let release: () => void = () => {};
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/session", async (route) => {
    await ready;
    await route.fulfill({ json: signedIn });
  });
  await page.goto("/?return_to=https://example.invalid");
  await expect(page.locator("#hero-title")).toBeVisible();
  expect(new URL(page.url()).pathname).toBe("/");
  release();
  await expect(page).toHaveURL("/library");
});

test("normal-motion demo animates the existing rows once and leaves no inline style or busy state", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await stable(page);
  const list = page.locator("#shuffle-track-list"),
    shuffle = page.getByRole("button", {
      name: "Preview Shuffle",
      exact: true,
    });
  await shuffle.click();
  await expect(list).toHaveAttribute("aria-busy", "true");
  expect(
    await list.evaluate((node) => node.getAnimations({ subtree: true }).length),
  ).toBeGreaterThan(0);
  await expect(list).toHaveAttribute("aria-busy", "false");
  await expect(shuffle).toBeFocused();
  expect(await page.locator("[data-demo-track][style]").count()).toBe(0);
  await expect(page.locator('[aria-live="polite"]')).toContainText(
    "Sample playlist reordered.",
  );
});

test("control accessible names retain their visible labels and public aliases serve the right HTML", async ({
  page,
}) => {
  await stable(page);
  const mismatches = await page
    .locator("a[aria-label],button[aria-label],input[aria-label]")
    .evaluateAll((nodes) =>
      nodes.flatMap((node) => {
        const clone = node.cloneNode(true) as HTMLElement;
        clone
          .querySelectorAll('[aria-hidden="true"]')
          .forEach((hidden) => hidden.remove());
        const text =
          clone.textContent?.replace(/\s+/g, " ").trim().toLowerCase() ?? "";
        const name = node.getAttribute("aria-label")?.toLowerCase() ?? "";
        return text && !name.includes(text) ? [{ text, name }] : [];
      }),
    );
  expect(mismatches).toEqual([]);
  for (const path of ["/terms/", "/privacy/"]) {
    const response = await page.goto(path);
    expect(await response?.text()).toContain(
      path === "/terms/"
        ? "Terms of Service - Shuffify"
        : "Privacy Policy - Shuffify",
    );
    await expect(page.locator("main h1")).toHaveText(
      path === "/terms/" ? "Terms of Service" : "Privacy Policy",
    );
  }
});

test("returning to the landing announces a new identical demo outcome once", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await stable(page);
  await page.locator('[aria-live="polite"]').evaluate((node) => {
    window.__publicLiveUpdates = [];
    const observer = new MutationObserver(() => {
      if (node.textContent?.includes("Sample playlist reordered."))
        window.__publicLiveUpdates.push(node.textContent);
    });
    observer.observe(node, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  });
  await page
    .getByRole("button", { name: "Preview Shuffle", exact: true })
    .click();
  await expect
    .poll(() => page.evaluate(() => window.__publicLiveUpdates.length))
    .toBe(1);
  await page
    .getByRole("navigation", { name: "Public navigation" })
    .getByRole("link", { name: "Terms", exact: true })
    .click();
  await page
    .getByRole("link", { name: "← Back to Shuffify", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Preview Shuffle", exact: true })
    .click();
  await expect
    .poll(() => page.evaluate(() => window.__publicLiveUpdates.length))
    .toBe(2);
});
