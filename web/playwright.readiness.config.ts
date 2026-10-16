import { defineConfig } from "@playwright/test";
import { delimiter, dirname } from "node:path";
export default defineConfig({
  testDir: "./tests/readiness-browser",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  expect: { timeout: 5000 },
  retries: 0,
  reporter: "list",
  outputDir: "./test-results/readiness",
  use: {
    baseURL: "http://127.0.0.1:4288",
    browserName: "chromium",
    colorScheme: "dark",
    locale: "en-US",
    timezoneId: "America/New_York",
    reducedMotion: "reduce",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "readiness-360", use: { viewport: { width: 360, height: 800 } } },
    { name: "readiness-desktop", use: { viewport: { width: 1440, height: 1000 } } },
  ],
  webServer: {
    command: "npm run build && node scripts/build-vercel-output.mjs && npm run preview -- --port 4288",
    url: "http://127.0.0.1:4288/",
    reuseExistingServer: false,
    timeout: 120000,
    env: {
      CONTACT_ADDRESS: "support@example.invalid",
      RAILWAY_API_URL: "https://api.example.invalid",
      PATH: `${dirname(process.execPath)}${delimiter}${process.env.PATH ?? ""}`,
    },
  },
});
