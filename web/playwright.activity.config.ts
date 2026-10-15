import { defineConfig } from "@playwright/test";
import { delimiter, dirname } from "node:path";
const fixtures = process.env.ACTIVITY_FIXTURE_BUILD === "1";
export default defineConfig({
  testDir: "./tests/activity-browser",
  testMatch: fixtures ? "**/fixture.spec.ts" : "**/activity.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  expect: { timeout: 5000 },
  retries: 0,
  reporter: "list",
  outputDir: "./test-results/activity",
  use: {
    baseURL: "http://127.0.0.1:4177",
    browserName: "chromium",
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "America/New_York",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "activity-360", use: { viewport: { width: 360, height: 800 } } },
    {
      name: "activity-desktop",
      use: { viewport: { width: 1440, height: 1000 } },
    },
  ],
  webServer: {
    command: `npm run ${fixtures ? "build:fixtures" : "build"} && npm run preview -- --port 4177`,
    url: "http://127.0.0.1:4177/activity",
    reuseExistingServer: false,
    timeout: 120000,
    env: {
      CONTACT_ADDRESS: "support@example.invalid",
      PATH: `${dirname(process.execPath)}${delimiter}${process.env.PATH ?? ""}`,
    },
  },
});
