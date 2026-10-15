import { defineConfig } from "@playwright/test";
import { dirname, delimiter } from "node:path";
export default defineConfig({
  testDir: "./tests/source-lifecycle-browser",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  retries: 0,
  reporter: "list",
  outputDir: "test-results/source-lifecycle",
  use: {
    baseURL: "http://127.0.0.1:55736",
    browserName: "chromium",
    colorScheme: "dark",
    trace: "retain-on-failure",
    video: "on",
  },
  projects: [
    { name: "sources-360", use: { viewport: { width: 360, height: 800 } } },
    { name: "sources-1440", use: { viewport: { width: 1440, height: 1000 } } },
  ],
  webServer: {
    command: "npm run build:fixtures && npm run preview -- --port 55736",
    url: "http://127.0.0.1:55736",
    reuseExistingServer: false,
    timeout: 120000,
    env: {
      CONTACT_ADDRESS: "support@example.invalid",
      PATH: `${dirname(process.execPath)}${delimiter}${process.env.PATH ?? ""}`,
    },
  },
});
