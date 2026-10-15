import { defineConfig } from "@playwright/test";
import { delimiter, dirname } from "node:path";

export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 5000 },
  retries: 0,
  reporter: "list",
  outputDir: "./test-results",
  use: {
    baseURL: "http://127.0.0.1:4173",
    browserName: "chromium",
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "America/New_York",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "foundation-360", use: { viewport: { width: 360, height: 800 } } },
    {
      name: "foundation-desktop",
      use: { viewport: { width: 1440, height: 1000 } },
    },
  ],
  webServer: {
    command: "npm run build:fixtures && npm run preview -- --port 4173",
    url: "http://127.0.0.1:4173/__foundation/app",
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      PATH: `${dirname(process.execPath)}${delimiter}${process.env.PATH ?? ""}`,
    },
  },
});
