import { defineConfig } from "@playwright/test";
import { dirname, delimiter } from "node:path";
const fixtures = process.env.WORKSHOP_SOURCES_FIXTURE_BUILD === "1";
export default defineConfig({
  testDir: "./tests/workshop-sources-browser",
  testMatch: fixtures ? "**/fixture.spec.ts" : "**/sources.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 35000,
  expect: { timeout: 6000 },
  retries: 0,
  reporter: "list",
  outputDir: `test-results/workshop-sources-${fixtures ? "fixture" : "production"}`,
  use: {
    baseURL: "http://127.0.0.1:4195",
    browserName: "chromium",
    colorScheme: "dark",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "on",
  },
  projects: [
    { name: "sources-360", use: { viewport: { width: 360, height: 800 } } },
    {
      name: "sources-desktop",
      use: { viewport: { width: 1440, height: 1000 } },
    },
  ],
  webServer: {
    command: `npm run ${fixtures ? "build:fixtures" : "build"} && npm run preview -- --port 4195`,
    url: "http://127.0.0.1:4195/workshop",
    reuseExistingServer: false,
    timeout: 120000,
    env: {
      CONTACT_ADDRESS: "support@example.invalid",
      PATH: `${dirname(process.execPath)}${delimiter}${process.env.PATH ?? ""}`,
    },
  },
});
