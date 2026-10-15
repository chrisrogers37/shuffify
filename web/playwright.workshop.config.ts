import { defineConfig } from "@playwright/test";
import { dirname, delimiter } from "node:path";
const fixtures = process.env.WORKSHOP_FIXTURE_BUILD === "1";
export default defineConfig({
  testDir: "./tests/workshop-browser",
  testMatch: fixtures ? "**/fixture.spec.ts" : "**/workshop.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  expect: { timeout: 6000 },
  retries: 0,
  reporter: "list",
  outputDir: "test-results/workshop",
  use: {
    baseURL: "http://127.0.0.1:4193",
    browserName: "chromium",
    colorScheme: "dark",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "workshop-360", use: { viewport: { width: 360, height: 800 } } },
    {
      name: "workshop-desktop",
      use: { viewport: { width: 1440, height: 1000 } },
    },
  ],
  webServer: {
    command: `npm run ${fixtures ? "build:fixtures" : "build"} && npm run preview -- --port 4193`,
    url: "http://127.0.0.1:4193/workshop",
    reuseExistingServer: false,
    timeout: 120000,
    env: {
      CONTACT_ADDRESS: "support@example.invalid",
      PATH: `${dirname(process.execPath)}${delimiter}${process.env.PATH ?? ""}`,
    },
  },
});
