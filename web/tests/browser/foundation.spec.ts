import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

declare global {
  interface Window {
    __foundationCspViolations: string[];
  }
}
const failures = new WeakMap<
  Page,
  { console: string[]; errors: string[]; spotifyRequests: string[] }
>();
const evidenceDirectory = fileURLToPath(
  new URL("../../evidence/", import.meta.url),
);

test.beforeEach(async ({ page }) => {
  const captured = {
    console: [] as string[],
    errors: [] as string[],
    spotifyRequests: [] as string[],
  };
  failures.set(page, captured);
  page.on("console", (message) => {
    if (message.type() === "error") captured.console.push(message.text());
  });
  page.on("pageerror", (error) => captured.errors.push(error.message));
  page.on("request", (request) => {
    if (/^https:\/\/(?:api|accounts)\.spotify\.com\//u.test(request.url()))
      captured.spotifyRequests.push(request.url());
  });
  await page.addInitScript(() => {
    window.__foundationCspViolations = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      window.__foundationCspViolations.push(
        `${event.violatedDirective}: ${event.blockedURI}`,
      );
    });
  });
});

test.afterEach(async ({ page }) => {
  const captured = failures.get(page);
  expect.soft(captured?.console ?? [], "No browser console errors").toEqual([]);
  expect.soft(captured?.errors ?? [], "No uncaught browser errors").toEqual([]);
  expect
    .soft(
      captured?.spotifyRequests ?? [],
      "Fixture browser must never contact Spotify APIs",
    )
    .toEqual([]);
  if (!page.isClosed()) {
    expect
      .soft(
        await page.evaluate(() => window.__foundationCspViolations ?? []),
        "No CSP violations",
      )
      .toEqual([]);
    // Playwright screenshots briefly set input caret styles and leave empty attributes.
    expect
      .soft(
        await page
          .locator('script:not([src]), style, [style]:not([style=""])')
          .count(),
        "No inline scripts, style elements, or nonempty style attributes",
      )
      .toBe(0);
  }
});

async function app(page: Page) {
  const response = await page.goto("/__foundation/app");
  await expect(
    page.getByRole("heading", { name: "Foundation review", exact: true }),
  ).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return response;
}

async function assertNoOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "No horizontal document overflow",
  ).toBe(true);
}

async function assertTargets(page: Page) {
  const undersized = await page
    .locator("button, input, select, summary, a[href]")
    .evaluateAll((elements) => {
      return elements.flatMap((element) => {
        const target =
          element instanceof HTMLInputElement && element.type === "checkbox"
            ? (element.labels?.[0] ?? element)
            : element;
        if (
          !(target instanceof HTMLElement) ||
          target.closest("[hidden], [inert]") ||
          target.getAttribute("href") === "#main-content"
        )
          return [];
        const rect = target.getBoundingClientRect();
        const style = getComputedStyle(target);
        if (
          rect.width === 0 ||
          rect.height === 0 ||
          style.visibility === "hidden"
        )
          return [];
        return rect.width >= 43.9 && rect.height >= 43.9
          ? []
          : [
              {
                name:
                  target.textContent?.trim() ||
                  target.getAttribute("aria-label") ||
                  target.tagName,
                width: rect.width,
                height: rect.height,
              },
            ];
      });
    });
  expect(
    undersized,
    "Every visible interactive target has at least 44×44px hit area",
  ).toEqual([]);
}

test("built shell enforces CSP, local assets, official attribution and responsive targets", async ({
  page,
}, testInfo) => {
  const response = await app(page);
  const headers = response?.headers() ?? {};
  expect(headers["content-security-policy"]).toContain("script-src 'self'");
  expect(headers["content-security-policy"]).toContain("style-src 'self'");
  expect(headers["content-security-policy"]).not.toContain("unsafe-inline");
  expect(headers["content-security-policy"]).not.toContain("cdn.jsdelivr.net");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-frame-options"]).toBe("DENY");
  const logo = page
    .locator('[data-spotify-artwork] img[alt="Spotify"]')
    .first();
  await expect(logo).toBeVisible();
  expect(
    await logo.evaluate((element) => element.getBoundingClientRect().width),
  ).toBeGreaterThanOrEqual(70);
  await expect(logo).toHaveAttribute("src", "/images/spotify-logo-white.svg");
  const art = page.locator("[data-spotify-artwork] .sh-artwork-image").first();
  await expect(art).toBeVisible();
  expect(
    await art.evaluate((element) => getComputedStyle(element).objectFit),
  ).toBe("contain");
  const tokens = await art.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      small: style.getPropertyValue("--sh-radius-artwork").trim(),
      large: style.getPropertyValue("--sh-radius-artwork-wide").trim(),
      radius: style.borderRadius,
    };
  });
  expect(tokens.small).toBe("4px");
  expect(tokens.large).toBe("8px");
  expect(tokens.radius).toBe(
    (page.viewportSize()?.width ?? 0) < 1024 ? "4px" : "8px",
  );
  await expect(
    page.locator("[data-spotify-artwork] a").first(),
  ).toHaveAttribute("href", /^https:\/\/open\.spotify\.com\//u);
  await assertNoOverflow(page);
  await assertTargets(page);
  await expect(page.locator("script:not([src]), style, [style]")).toHaveCount(
    0,
  );
  await mkdir(evidenceDirectory, { recursive: true });
  await page.screenshot({
    path: resolve(evidenceDirectory, `${testInfo.project.name}.png`),
    fullPage: true,
    animations: "disabled",
  });
});

test("context task closes with Escape and restores its trigger focus", async ({
  page,
}) => {
  await app(page);
  const opener = page.getByRole("button", { name: "Open task", exact: true });
  await opener.click();
  const task = page.getByRole("region", { name: "Playlist task", exact: true });
  await expect(task).toBeVisible();
  await expect(
    task.getByRole("heading", { name: "Playlist task", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(task).toBeHidden();
  await expect(opener).toBeFocused();
});

test("child task suspends parent input and restores its draft on return", async ({
  page,
}) => {
  await app(page);
  await page.getByRole("button", { name: "Open task", exact: true }).click();
  const parent = page.getByRole("region", {
    name: "Playlist task",
    exact: true,
    includeHidden: true,
  });
  await parent
    .getByLabel("Task note", { exact: true })
    .fill("Preserve this unfinished note.");
  const childTrigger = parent.getByRole("button", {
    name: "Open child task",
    exact: true,
  });
  await childTrigger.click();
  const child = page.getByRole("region", { name: "Child task", exact: true });
  await expect(child).toBeVisible();
  await expect(parent).toBeHidden();
  await expect(parent).toHaveAttribute("inert", "");
  await child.getByRole("button", { name: "Back", exact: true }).click();
  await expect(child).toBeHidden();
  await expect(parent).toBeVisible();
  await expect(parent.getByLabel("Task note", { exact: true })).toHaveValue(
    "Preserve this unfinished note.",
  );
  await expect(childTrigger).toBeFocused();
});

test("narrow context hides and inerts primary content while desktop remains available", async ({
  page,
}) => {
  await app(page);
  await page.getByRole("button", { name: "Open task", exact: true }).click();
  const primary = page.locator("[data-panel-primary]");
  if ((page.viewportSize()?.width ?? 0) < 1024) {
    await expect(primary).toBeHidden();
    await expect(primary).toHaveAttribute("inert", "");
  } else {
    await expect(primary).toBeVisible();
    await expect(primary).not.toHaveAttribute("inert", "");
    await page.keyboard.press("Shift+Tab");
    expect(
      await page.evaluate(
        () => !!document.activeElement?.closest("[data-panel-primary]"),
      ),
      "Context panel does not trap desktop focus",
    ).toBe(true);
  }
  await assertNoOverflow(page);
});

test("confirmation dialog traps keyboard focus and cancel restores its trigger", async ({
  page,
}) => {
  await app(page);
  const trigger = page.getByRole("button", {
    name: "Confirm action",
    exact: true,
  });
  await trigger.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const cancel = dialog.getByRole("button", { name: "Cancel", exact: true });
  await expect(cancel).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(
    dialog.getByRole("button", { name: "Confirm", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(cancel).toBeFocused();
  for (let index = 0; index < 5; index += 1) {
    await page.keyboard.press("Tab");
    expect(
      await dialog.evaluate((element) =>
        element.contains(document.activeElement),
      ),
      "Tab stays inside modal",
    ).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("guarded router navigation preserves draft on Stay and leaves after one confirmation", async ({
  page,
}) => {
  await app(page);
  await page.getByLabel("Protect unsaved changes", { exact: true }).check();
  const link = page.getByRole("link", { name: "Public shell", exact: true });
  await link.click();
  const dialog = page.getByRole("dialog", { name: "Leave this page?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Stay", exact: true }).click();
  await expect(page).toHaveURL(/\/__foundation\/app$/u);
  await expect(
    page.getByLabel("Protect unsaved changes", { exact: true }),
  ).toBeChecked();
  const nativeDialogs: string[] = [];
  page.on("dialog", async (native) => {
    nativeDialogs.push(native.type());
    await native.dismiss();
  });
  await link.click();
  await dialog.getByRole("button", { name: "Leave page", exact: true }).click();
  await expect(page).toHaveURL(/\/__foundation\/public$/u);
  expect(
    nativeDialogs,
    "Approved custom confirmation must not produce another beforeunload prompt",
  ).toEqual([]);
});

test("toast is announced through one stable live region", async ({ page }) => {
  await app(page);
  await page.getByRole("button", { name: "Show toast", exact: true }).click();
  const liveRegions = page.locator('[aria-live="polite"]');
  expect(await liveRegions.count()).toBe(1);
  await expect(liveRegions).not.toHaveText("");
});

test("reduced motion removes animations and smooth scrolling", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await app(page);
  const movement = await page.locator(".sh-shell").evaluate((element) => {
    const durations = Array.from(element.querySelectorAll("*"), (node) =>
      getComputedStyle(node),
    );
    const seconds = (value: string) =>
      value
        .split(",")
        .map(
          (part) =>
            Number.parseFloat(part) * (part.trim().endsWith("ms") ? 0.001 : 1),
        );
    return {
      max: Math.max(
        0,
        ...durations.flatMap((style) => [
          ...seconds(style.animationDuration),
          ...seconds(style.transitionDuration),
        ]),
      ),
      scroll: getComputedStyle(document.documentElement).scrollBehavior,
    };
  });
  expect(movement.max).toBeLessThanOrEqual(0.001);
  expect(movement.scroll).toBe("auto");
});

test("public and error shells retain legal navigation and no horizontal overflow", async ({
  page,
}) => {
  for (const path of ["/__foundation/public", "/__foundation/errors/404"]) {
    await page.goto(path);
    await expect(page.locator("main h1")).toBeVisible();
    await expect(
      page.getByRole("link", { name: /^Terms(?: of Service)?$/u }).first(),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: /^Privacy(?: Policy)?$/u }).first(),
    ).toBeVisible();
    await assertNoOverflow(page);
    await assertTargets(page);
  }
});

test("guarded native form leaves after its single authored confirmation", async ({
  page,
}) => {
  await app(page);
  await page.getByLabel("Protect unsaved changes", { exact: true }).check();
  await page
    .getByLabel("Fixture scenario", { exact: true })
    .selectOption("empty");
  const nativeDialogs: string[] = [];
  page.on("dialog", async (native) => {
    nativeDialogs.push(native.type());
    await native.dismiss();
  });
  await page
    .getByRole("button", { name: "Load scenario", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Leave this page?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Leave page", exact: true }).click();
  expect(
    nativeDialogs,
    "Approved form navigation must not ask for a second confirmation",
  ).toEqual([]);
  await expect(page).toHaveURL(/\/__foundation\/app\?scenario=empty$/u);
});

test("empty fixture renders an authored state with working shared controls", async ({
  page,
}) => {
  await page.goto("/__foundation/app?scenario=empty");
  await expect(
    page.getByRole("heading", { name: "No playlists yet", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Fixture scenario", { exact: true }),
  ).toHaveValue("empty");
  await assertNoOverflow(page);
});

test("refused fixture admission shows its server error without inventing an operation", async ({
  page,
}) => {
  await page.goto("/__foundation/app?scenario=error");
  await page
    .getByRole("button", { name: "Queue fixture operation", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "The request needs attention",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("The fixture command was refused.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Operation /u })).toHaveCount(
    0,
  );
});

for (const outcome of [
  {
    scenario: "happy",
    state: "succeeded",
    message: "The fixture operation completed.",
  },
  {
    scenario: "partial",
    state: "failed",
    message: "One step finished. Review the remaining changes.",
  },
  {
    scenario: "rollback",
    state: "rolled back",
    message: "Changes were verified as restored.",
  },
]) {
  test(`${outcome.scenario} fixture reports its actual terminal operation outcome`, async ({
    page,
  }) => {
    await page.goto(`/__foundation/app?scenario=${outcome.scenario}`);
    const button = page.getByRole("button", {
      name: "Queue fixture operation",
      exact: true,
    });
    await button.click();
    await expect(button).toHaveAttribute("aria-disabled", "true");
    await expect(
      page.getByRole("heading", {
        name: `Operation ${outcome.state}`,
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByText(outcome.message, { exact: true }),
    ).toBeVisible();
    await expect(button).not.toHaveAttribute("aria-disabled", "true");
    await expect(button).toBeFocused();
    await expect(
      page.getByRole("button", { name: "Review recovery", exact: true }),
    ).toHaveCount(0);
  });
}

test("needs-attention fixture advertises review without automatically restoring", async ({
  page,
}) => {
  await page.goto("/__foundation/app?scenario=recovery");
  await page
    .getByRole("button", { name: "Queue fixture operation", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Operation needs attention",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Review recovery", exact: true })
    .click();
  const review = page.getByRole("region", {
    name: "Review recovery",
    exact: true,
  });
  await expect(review).toBeVisible();
  await expect(review.getByText(/No automatic write is sent/u)).toBeVisible();
});

test("busy fixture preserves the conflict and lets the user inspect its operation", async ({
  page,
}) => {
  await page.goto("/__foundation/app?scenario=busy");
  await page
    .getByRole("button", { name: "Queue fixture operation", exact: true })
    .click();
  await expect(
    page.getByText("This playlist has an operation in progress.", {
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Inspect operation", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Operation succeeded", exact: true }),
  ).toBeVisible();
});

test("rate-limit fixture waits the full server hint before reaching its result", async ({
  page,
}) => {
  await page.goto("/__foundation/app?scenario=rate-limit");
  const started = Date.now();
  await page
    .getByRole("button", { name: "Queue fixture operation", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Operation succeeded", exact: true }),
  ).toBeVisible();
  expect(Date.now() - started).toBeGreaterThanOrEqual(1000);
});

test("signed-out fixture renders native consent before starting login", async ({
  page,
}) => {
  await page.goto("/__foundation/app?scenario=signed-out");
  await expect(
    page.getByRole("heading", { name: "Connect to Spotify", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Queue fixture operation", exact: true }),
  ).toHaveCount(0);
  const form = page.locator('form[action="/login"]');
  await expect(form).toHaveAttribute("method", "get");
  const consent = form.getByRole("checkbox");
  await expect(consent).toHaveAttribute("name", "legal_consent");
  await expect(consent).toHaveAttribute("required", "");
  await expect(consent).not.toBeChecked();
});

test("signed-out reconnect consent is adjacent, linked, required and submits legal_consent", async ({
  page,
}) => {
  await page.goto("/__foundation/app?scenario=signed-out");
  const form = page.locator('form[action="/login"]');
  const consent = form.getByRole("checkbox");
  await expect(
    form.getByRole("link", { name: "Terms of Service" }),
  ).toHaveAttribute("href", "/terms");
  await expect(
    form.getByRole("link", { name: "Privacy Policy" }),
  ).toHaveAttribute("href", "/privacy");
  const gap = await consent.evaluate((input) => {
    const box = input.getBoundingClientRect();
    const text = input.nextElementSibling as HTMLElement;
    const range = document.createRange();
    range.setStart(text.firstChild as Node, 0);
    range.setEnd(text.firstChild as Node, 1);
    return range.getClientRects()[0]!.left - box.right;
  });
  expect(gap).toBeGreaterThanOrEqual(0);
  expect(gap).toBeLessThan(24);
  const checkAlignment = async () =>
    consent.evaluate((input) => {
      const box = input.getBoundingClientRect();
      const text = input.nextElementSibling as HTMLElement;
      const range = document.createRange();
      range.setStart(text.firstChild as Node, 0);
      range.setEnd(text.firstChild as Node, 1);
      const line = range.getClientRects()[0]!;
      return Math.abs(
        box.top + box.height / 2 - (line.top + line.height / 2),
      );
    });
  expect(await checkAlignment()).toBeLessThanOrEqual(3);
  for (const name of ["Terms of Service", "Privacy Policy"]) {
    const link = await form.getByRole("link", { name }).boundingBox();
    expect(link!.height).toBeGreaterThanOrEqual(44);
  }
  await page.setViewportSize({ width: 360, height: 800 });
  expect(await checkAlignment()).toBeLessThanOrEqual(3);
  await page.setViewportSize({ width: 1280, height: 800 });
  const label = await form.locator("label").boundingBox();
  const button = await form
    .getByRole("button", { name: "Connect with Spotify" })
    .boundingBox();
  const panel = await page.locator(".sh-state").boundingBox();
  const mid = (b: { x: number; width: number } | null) => b!.x + b!.width / 2;
  expect(Math.abs(mid(label) - mid(panel))).toBeLessThan(4);
  expect(Math.abs(mid(button) - mid(panel))).toBeLessThan(4);
  expect(label!.height).toBeGreaterThanOrEqual(44);
  await consent.focus();
  await expect(consent).toBeFocused();
  let navigated = false;
  await page.route("**/login**", async (route) => {
    navigated = true;
    await route.fulfill({ status: 200, contentType: "text/html", body: "ok" });
  });
  await form.getByRole("button", { name: "Connect with Spotify" }).click();
  expect(navigated).toBe(false);
  expect(await form.evaluate((f: HTMLFormElement) => f.checkValidity())).toBe(
    false,
  );
  expect(page.url()).toContain("/__foundation/app");
  await consent.press("Space");
  await expect(consent).toBeChecked();
  await Promise.all([
    page.waitForURL(/\/login\?legal_consent=1$/),
    consent.press("Enter"),
  ]);
});

test("session-error fixture offers explicit retry and keeps protected controls unavailable", async ({
  page,
}) => {
  await page.goto("/__foundation/app?scenario=session-error");
  await expect(
    page.getByRole("heading", {
      name: "Your session could not be loaded",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Try again", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Queue fixture operation", exact: true }),
  ).toHaveCount(0);
});

test("closed confirmation dialog is visually absent before any action", async ({
  page,
}) => {
  await app(page);
  await expect(page.locator("dialog")).toBeHidden();
  await expect(page.locator("dialog")).not.toHaveAttribute("open", "");
});

test("close confirmation suspends the context task and cancel preserves its note", async ({
  page,
}) => {
  await app(page);
  const opener = page.getByRole("button", { name: "Open task", exact: true });
  await opener.click();
  const task = page.getByRole("region", {
    name: "Playlist task",
    exact: true,
    includeHidden: true,
  });
  await task
    .getByLabel("Task note", { exact: true })
    .fill("Keep this task note after cancelling.");
  await task.getByLabel("Confirm before closing task", { exact: true }).check();
  const close = task.getByRole("button", { name: "Close", exact: true });
  await close.click();
  const dialog = page.getByRole("dialog", {
    name: "Close this task?",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(task).toHaveAttribute("inert", "");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(task).toBeVisible();
  await expect(task).not.toHaveAttribute("inert", "");
  await expect(task.getByLabel("Task note", { exact: true })).toHaveValue(
    "Keep this task note after cancelling.",
  );
  await expect(close).toBeFocused();
  await close.click();
  await dialog.getByRole("button", { name: "Close task", exact: true }).click();
  await expect(task).toBeHidden();
  await expect(opener).toBeFocused();
});

test("session bootstrap sets the final route title and focuses its heading", async ({
  page,
}) => {
  await app(page);
  await expect(page).toHaveTitle("Foundation review · Shuffify");
  await expect(
    page.getByRole("heading", { name: "Foundation review", exact: true }),
  ).toBeFocused();
});

test("keyboard navigation gives a visible focus indicator to shared controls", async ({
  page,
}) => {
  await app(page);
  await page.getByLabel("Collection name", { exact: true }).focus();
  await page.keyboard.press("Tab");
  const indicator = await page.evaluate(() => {
    const target = document.activeElement;
    if (!(target instanceof HTMLElement)) return null;
    const style = getComputedStyle(target);
    return {
      visible: target.matches(":focus-visible"),
      width: Number.parseFloat(style.outlineWidth),
      style: style.outlineStyle,
      color: style.outlineColor,
    };
  });
  expect(indicator?.visible).toBe(true);
  expect(indicator?.width).toBeGreaterThanOrEqual(2);
  expect(indicator?.style).toBe("solid");
  expect(indicator?.color).not.toBe("rgba(0, 0, 0, 0)");
});

test("closing a task whose opener was removed restores the section heading", async ({
  page,
}) => {
  await app(page);
  await page.getByRole("button", { name: "Open task", exact: true }).click();
  const task = page.getByRole("region", { name: "Playlist task", exact: true });
  await task
    .getByRole("button", { name: "Remove task opener", exact: true })
    .click();
  await task.getByRole("button", { name: "Close", exact: true }).click();
  await expect(task).toBeHidden();
  await expect(
    page.getByRole("button", { name: "Open task", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Foundation review", exact: true }),
  ).toBeFocused();
});

test("repeated Escape cannot reenter an asynchronous close decision", async ({
  page,
}) => {
  await app(page);
  const opener = page.getByRole("button", { name: "Open task", exact: true });
  await opener.click();
  const task = page.getByRole("region", { name: "Playlist task", exact: true });
  await task.getByLabel("Delay close approval", { exact: true }).check();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  await expect(task).toBeHidden();
  await expect(
    page.getByText("Close decisions: 1", { exact: true }),
  ).toBeVisible();
  await expect(opener).toBeFocused();
});

test("a native modal owns Escape without dismissing its underlying task", async ({
  page,
}) => {
  await app(page);
  const opener = page.getByRole("button", { name: "Open task", exact: true });
  await opener.click();
  const task = page.getByRole("region", { name: "Playlist task", exact: true });
  const note = task.getByLabel("Task note", { exact: true });
  await note.fill("Retained while the native modal owns Escape.");
  await note.focus();
  await page.evaluate(() => {
    const dialog = document.createElement("dialog");
    dialog.setAttribute("aria-label", "Native task decision");
    const button = document.createElement("button");
    button.textContent = "Native decision action";
    dialog.append(button);
    document.body.append(dialog);
    dialog.showModal();
  });
  const dialog = page.getByRole("dialog", {
    name: "Native task decision",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Native decision action", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(task).toBeVisible();
  await expect(note).toHaveValue(
    "Retained while the native modal owns Escape.",
  );
  await expect(note).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(task).toBeHidden();
  await expect(opener).toBeFocused();
});

test("a handled Escape preserves its task until a later unhandled Escape", async ({
  page,
}) => {
  await app(page);
  const opener = page.getByRole("button", { name: "Open task", exact: true });
  await opener.click();
  const task = page.getByRole("region", { name: "Playlist task", exact: true });
  const note = task.getByLabel("Task note", { exact: true });
  await note.focus();
  await note.evaluate((input) => {
    input.addEventListener(
      "keydown",
      (event) => {
        if (event instanceof KeyboardEvent && event.key === "Escape")
          event.preventDefault();
      },
      { once: true },
    );
  });
  await page.keyboard.press("Escape");
  await expect(task).toBeVisible();
  await expect(note).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(task).toBeHidden();
  await expect(opener).toBeFocused();
});
