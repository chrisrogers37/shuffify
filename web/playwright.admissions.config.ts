import { defineConfig } from "@playwright/test";
import { dirname, delimiter } from "node:path";
export default defineConfig({
  testDir: "tests/admission-browser",
  workers: 1,
  retries: 0,
  timeout: 30000,
  expect: { timeout: 6000 },
  reporter: "list",
  outputDir: "test-results/admissions",
  use: {
    baseURL: "http://127.0.0.1:4197",
    browserName: "chromium",
    colorScheme: "dark",
    reducedMotion: "reduce",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "admissions-360", use: { viewport: { width: 360, height: 800 } } },
    {
      name: "admissions-1440",
      use: { viewport: { width: 1440, height: 1000 } },
    },
  ],
  webServer: {
    command: "npm run build && npm run preview -- --port 4197",
    url: "http://127.0.0.1:4197/library",
    reuseExistingServer: false,
    timeout: 120000,
    env: {
      CONTACT_ADDRESS: "support@example.invalid",
      PATH: `${dirname(process.execPath)}${delimiter}${process.env.PATH ?? ""}`,
    },
  },
});
