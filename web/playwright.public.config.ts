import { defineConfig } from "@playwright/test";
import { delimiter, dirname } from "node:path";
export default defineConfig({
  testDir: "./tests/public-browser",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  expect: { timeout: 5000 },
  retries: 0,
  reporter: "list",
  outputDir: "./test-results/public",
  use: {
    baseURL: "http://127.0.0.1:4174",
    browserName: "chromium",
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "America/New_York",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "public-360", use: { viewport: { width: 360, height: 800 } } },
    {
      name: "public-desktop",
      use: { viewport: { width: 1440, height: 1000 } },
    },
  ],
  webServer: {
    command:
      "npm run build && npm run test:static && npm run preview -- --port 4174",
    url: "http://127.0.0.1:4174/",
    reuseExistingServer: false,
    timeout: 120000,
    env: {
      CONTACT_ADDRESS: "support@example.invalid",
      PATH: `${dirname(process.execPath)}${delimiter}${process.env.PATH ?? ""}`,
    },
  },
});
