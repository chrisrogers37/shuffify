import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { createWorkshopFixtures } from "../../src/fixtures/workshop";
import { createWorkshopToolsFixtures } from "../../src/fixtures/workshop-tools";
import { createWorkshopSourcesFixtures } from "../../src/fixtures/workshop-sources";
import signed from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
export { checks, open, close, panel } from "../workshop-tools-browser/helpers";
export async function start(
  page: Page,
  scenario = "workshop-sources",
  path = "/workshop/target",
  mutateRoutines?: (body: any) => void,
) {
  const base = createWorkshopFixtures("workshop-happy"),
    tools = createWorkshopToolsFixtures("workshop-tools", base),
    sources = createWorkshopSourcesFixtures(scenario),
    calls: {
      path: string;
      method: string;
      body: string | null;
      key: string | null;
      time: number;
      status: number | null;
    }[] = [];
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      path = url.pathname + url.search,
      method = req.method(),
      init = {
        method,
        body: req.postData(),
        headers: new Headers(req.headers()),
      };
    const call = {
      path,
      method,
      body: init.body,
      key: req.headers()["idempotency-key"] ?? null,
      time: Date.now(),
      status: null as number | null,
    };
    calls.push(call);
    const response =
      (await sources(path, method, init)) ??
      (await tools(path, method, init)) ??
      (await base(path, method, init)) ??
      new Response(
        JSON.stringify(
          url.pathname === "/api/session"
            ? signed
            : {
                success: false,
                category: "error",
                error_code: "missing_fixture",
                message: `Missing fixture ${path}`,
              },
        ),
        {
          status: url.pathname === "/api/session" ? 200 : 404,
          headers: { "Content-Type": "application/json" },
        },
      );
    call.status = response.status;
    let text = await response.text();
    if (
      mutateRoutines &&
      url.pathname === "/api/playlist/target/routines/context"
    ) {
      const parsed = JSON.parse(text);
      mutateRoutines(parsed);
      text = JSON.stringify(parsed);
    }
    await route.fulfill({
      status: response.status,
      body: text,
      headers: (() => {
        const headers: Record<string, string> = {};
        response.headers.forEach((value, key) => {
          headers[key] = value;
        });
        return headers;
      })(),
    });
  });
  await page.goto(path);
  await expect(
    new URL(path, "http://fixture.invalid").searchParams.has("setup")
      ? page
          .locator("section.sh-task-panel:not([hidden])")
          .getByRole("heading", { level: 2 })
          .first()
      : page.getByRole("heading", { name: "Evening Collection", exact: true }),
  ).toBeVisible();
  return calls;
}
