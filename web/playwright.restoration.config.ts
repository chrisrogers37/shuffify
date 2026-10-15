import { defineConfig } from "@playwright/test";
import { delimiter, dirname } from "node:path";
export default defineConfig({
  testDir: "./tests/restoration-browser",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  expect: { timeout: 5000 },
  retries: 0,
  reporter: "list",
  outputDir: "./test-results/restoration",
  use: {
    baseURL: "http://127.0.0.1:55746",
    browserName: "chromium",
    colorScheme: "dark",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "restoration-360", use: { viewport: { width: 360, height: 800 } } },
    {
      name: "restoration-desktop",
      use: { viewport: { width: 1440, height: 1000 } },
    },
  ],
  webServer: {
    command: "npm run build && npm run preview -- --port 55746",
    url: "http://127.0.0.1:55746/activity",
    reuseExistingServer: false,
    timeout: 120000,
    env: {
      CONTACT_ADDRESS: "support@example.invalid",
      PATH: `${dirname(process.execPath)}${delimiter}${process.env.PATH ?? ""}`,
    },
  },
});
