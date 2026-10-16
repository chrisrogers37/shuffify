import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { createRailwayContext } from "railway/iac";
import definition from "../railway.ts";

const plan = await definition(
  createRailwayContext({ environment: "verification" }),
);
const resources = new Map(
  plan.resources.map((resource) => [resource.name, resource]),
);
const api = resources.get("api");
const worker = resources.get("worker");

test("the API starts only the bounded Gunicorn service and upgrades in a distinct pre-deploy process", () => {
  assert.equal(
    api.deploy.startCommand,
    "gunicorn --config gunicorn.conf.py run:app",
  );
  assert.deepEqual(api.deploy.preDeployCommand, ["sh scripts/pre-deploy.sh"]);
  assert.equal(api.deploy.numReplicas, 1);
  assert.equal(api.deploy.healthcheckPath, "/health");
  assert.equal(api.variables.PORT.value, "8000");
  assert.equal(api.variables.SHUFFIFY_MIGRATION_STEP, undefined);
  assert.equal(api.source, undefined);
});

test("one private worker starts the real command processor and receives time to drain", () => {
  assert.equal(worker.deploy.startCommand, "python -m shuffify.worker");
  assert.equal(worker.deploy.numReplicas, 1);
  assert.equal(worker.deploy.drainingSeconds, 1800);
  assert.equal(worker.deploy.healthcheckPath, "/health");
  assert.equal(
    worker.variables.PORT.value,
    worker.variables.WORKER_CONTROL_PORT.value,
  );
  assert.equal(worker.variables.WORKER_CONTROL_HOST.value, "0.0.0.0");
  assert.equal(worker.networking?.serviceDomains, undefined);
  assert.equal(worker.source, undefined);
});

test("both processes share durable infrastructure and reference credentials without putting values in the graph", () => {
  for (const service of [api, worker]) {
    assert.equal(
      service.variables.DATABASE_URL.resource,
      resources.get("postgres").address,
    );
    assert.equal(
      service.variables.REDIS_URL.resource,
      resources.get("redis").address,
    );
    assert.equal(service.variables.APP_CONFIG.value, "production");
    for (const name of [
      "SECRET_KEY",
      "TOKEN_ENCRYPTION_KEY",
      "SPOTIFY_CLIENT_ID",
      "SPOTIFY_CLIENT_SECRET",
      "SPOTIFY_REDIRECT_URI",
      "WORKER_CONTROL_TOKEN",
    ]) {
      assert.deepEqual(service.variables[name], {
        type: "sharedReference",
        name,
      });
    }
  }
  assert.equal(
    api.variables.WORKER_CONTROL_URL.value,
    "http://${{worker.RAILWAY_PRIVATE_DOMAIN}}:5001",
  );
  assert.equal(resources.get("postgres").engine, "postgres");
  assert.equal(resources.get("redis").engine, "redis");
});


test("native CLI empty-context evaluation preserves the full authored graph", () => {
  const url = new URL("../railway.ts", import.meta.url).href;
  const child = spawnSync(process.execPath, [
    "--experimental-strip-types", "--disable-warning=ExperimentalWarning",
    "--input-type=module", "-e",
    `const {default: configuration} = await import(${JSON.stringify(url)}); process.stdout.write(JSON.stringify(await configuration({})));`,
  ], { encoding: "utf8" });
  assert.equal(child.status, 0, child.stderr);
  assert.deepEqual(JSON.parse(child.stdout), JSON.parse(JSON.stringify(plan)));
});

test("an injected shared-reference context is preserved", async () => {
  const original = createRailwayContext({environment: "verification"});
  const names = ["SECRET_KEY", "TOKEN_ENCRYPTION_KEY", "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI", "WORKER_CONTROL_TOKEN"];
  const shared = Object.fromEntries(names.map(name => [name, {
    type: "sharedReference", name: `provided_${name}`,
  }]));
  const injected = await definition({...original, shared});
  for (const service of injected.resources.filter(resource => ["api", "worker"].includes(resource.name))) {
    for (const name of names) assert.equal(service.variables[name], shared[name]);
  }
});
