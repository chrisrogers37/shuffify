import { defineConfig } from "@playwright/test";
import { dirname, delimiter } from "node:path";
const fixtures = process.env.WORKSHOP_TOOLS_FIXTURE_BUILD === "1";
export default defineConfig({
  testDir: "./tests/workshop-tools-browser",
  testMatch: fixtures ? "**/fixture.spec.ts" : "**/tools.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 35000,
  expect: { timeout: 6000 },
  retries: 0,
  reporter: "list",
  outputDir: `test-results/workshop-tools-${fixtures ? "fixtures" : "production"}`,
  use: {
    baseURL: "http://127.0.0.1:4194",
    browserName: "chromium",
    colorScheme: "dark",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "on",
  },
  projects: [
    { name: "tools-360", use: { viewport: { width: 360, height: 800 } } },
    { name: "tools-desktop", use: { viewport: { width: 1440, height: 1000 } } },
  ],
  webServer: {
    command: `npm run ${fixtures ? "build:fixtures" : "build"} && npm run preview -- --port 4194`,
    url: "http://127.0.0.1:4194/workshop",
    reuseExistingServer: false,
    timeout: 120000,
    env: {
      CONTACT_ADDRESS: "support@example.invalid",
      PATH: `${dirname(process.execPath)}${delimiter}${process.env.PATH ?? ""}`,
    },
  },
});
