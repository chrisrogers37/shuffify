import { defineConfig } from "@playwright/test";
import base from "./playwright.workshop-tools.config";
const fixtures = process.env.WORKSHOP_TOOLS_FIXTURE_BUILD === "1";
export default defineConfig({
  ...base,
  testDir: "./tests",
  testMatch: fixtures
    ? [
        "workshop-tools-browser/fixture.spec.ts",
        "workshop-browser/fixture.spec.ts",
      ]
    : [
        "workshop-tools-browser/tools.spec.ts",
        "workshop-browser/workshop.spec.ts",
      ],
  outputDir: `test-results/workshop-tools-regression-${fixtures ? "fixtures" : "production"}`,
});
