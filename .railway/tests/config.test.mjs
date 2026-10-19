import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { createRailwayContext } from "railway/iac";
import definition from "../railway.ts";

const repoInput = "SHUFFIFY_RAILWAY_GITHUB_REPO";
const branchInput = "SHUFFIFY_RAILWAY_GITHUB_BRANCH";
const authoringInputs = [repoInput, branchInput];
const sourceInputs = {
  [repoInput]: "example-owner/example-repo",
  [branchInput]: "release/reviewed-source",
};

function authoringEnvironment(inputs) {
  const env = { ...process.env };
  for (const name of authoringInputs) delete env[name];
  for (const [name, value] of Object.entries(inputs)) {
    if (value !== undefined) env[name] = value;
  }
  return env;
}

async function evaluate(inputs = sourceInputs, context = createRailwayContext({ environment: "verification" })) {
  const previous = Object.fromEntries(authoringInputs.map((name) => [name, process.env[name]]));
  for (const name of authoringInputs) {
    if (inputs[name] === undefined) delete process.env[name];
    else process.env[name] = inputs[name];
  }
  try {
    return await definition(context);
  } finally {
    for (const name of authoringInputs) {
      if (previous[name] === undefined) delete process.env[name];
      else process.env[name] = previous[name];
    }
  }
}

function nativeEvaluation(inputs) {
  const url = new URL("../railway.ts", import.meta.url).href;
  return spawnSync(process.execPath, [
    "--experimental-strip-types", "--disable-warning=ExperimentalWarning",
    "--input-type=module", "-e",
    `const {default: configuration} = await import(${JSON.stringify(url)}); process.stdout.write(JSON.stringify(await configuration({})));`,
  ], { encoding: "utf8", env: authoringEnvironment(inputs) });
}

const plan = await evaluate();
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
});

test("both processes share durable infrastructure and reference credentials without putting values in the graph", () => {
  for (const service of [api, worker]) {
    assert.deepEqual(service.variables.DATABASE_URL, {
      type: "sharedReference",
      name: "DATABASE_URL",
    });
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
  assert.equal(resources.get("redis").engine, "redis");
});

test("the project declares only the cache and the two services, so no plan recreates the retired Railway PostgreSQL", () => {
  assert.deepEqual(plan.resources.map((resource) => resource.name).sort(), ["api", "redis", "worker"]);
});

test("split API requires the owner-supplied browser origin", () => {
  assert.deepEqual(api.variables.PUBLIC_ORIGIN, {
    type: "sharedReference",
    name: "PUBLIC_ORIGIN",
  });
  assert.equal(worker.variables.PUBLIC_ORIGIN, undefined);
});


test("both services declare the exact owner-supplied repository and branch without leaking authoring inputs into containers", () => {
  const expected = { type: "github", repo: sourceInputs[repoInput], branch: sourceInputs[branchInput] };
  for (const service of [api, worker]) {
    assert.equal(service.kind, "github");
    assert.deepEqual(service.source, expected);
    assert.equal(service.variables[repoInput], undefined);
    assert.equal(service.variables[branchInput], undefined);
  }
});

test("both source inputs are required together and no empty or partial pair yields a plan", async () => {
  for (const inputs of [
    {}, { [repoInput]: sourceInputs[repoInput] }, { [branchInput]: sourceInputs[branchInput] },
    { ...sourceInputs, [repoInput]: "" }, { ...sourceInputs, [branchInput]: "" },
  ]) {
    await assert.rejects(evaluate(inputs), /SHUFFIFY_RAILWAY_GITHUB_(?:REPO|BRANCH)/);
  }
});

test("malformed repository inputs refuse evaluation rather than becoming another source", async () => {
  for (const repo of [
    "https://github.com/example-owner/example-repo", "https://dummy-token@github.com/example-owner/example-repo",
    "example-owner", "example-owner/", "/example-repo", "example-owner/example-repo/extra",
    " example-owner/example-repo", "example-owner/example-repo\n", "example owner/example-repo",
    "bad_owner/example-repo", "-owner/example-repo", "owner-/example-repo", "owner--name/example-repo",
    `${"a".repeat(40)}/example-repo`, `example-owner/${"a".repeat(101)}`, "example-owner/.", "example-owner/..",
  ]) {
    await assert.rejects(evaluate({ ...sourceInputs, [repoInput]: repo }), (error) => {
      assert.match(error.message, /SHUFFIFY_RAILWAY_GITHUB_REPO/);
      assert.ok(!error.message.includes("dummy-token"));
      return true;
    });
  }
});

test("invalid Git branch syntax refuses evaluation without falling back to main", async () => {
  for (const branch of [
    " main", "main ", "feature name", "feature\nname", "feature\u007fname", "HEAD", "-main", "/main", "main/",
    "feature//topic", ".main", "feature/.hidden", "feature..topic", "feature@{topic", "main.",
    "main.lock", "feature.lock/topic", "feature~topic", "feature^topic", "feature:topic",
    "feature?topic", "feature*topic", "feature[topic", "feature\\topic",
  ]) {
    await assert.rejects(evaluate({ ...sourceInputs, [branchInput]: branch }), /SHUFFIFY_RAILWAY_GITHUB_BRANCH/);
  }
});

test("legitimate slash-containing and Unicode Git branch names survive exact SDK evaluation", async () => {
  for (const branch of ["@", "feature/ui", "release/v2.1", "feature/-nested", "topic/@name", "feature/naïve", "foo./bar", "foo.LOCK/bar"]) {
    const graph = await evaluate({ ...sourceInputs, [branchInput]: branch });
    for (const service of graph.resources.filter(resource => ["api", "worker"].includes(resource.name))) {
      assert.deepEqual(service.source, { type: "github", repo: sourceInputs[repoInput], branch });
    }
  }
});

test("native CLI empty-context evaluation preserves the full authored graph and exact source", () => {
  const child = nativeEvaluation(sourceInputs);
  assert.equal(child.status, 0, child.stderr);
  assert.deepEqual(JSON.parse(child.stdout), JSON.parse(JSON.stringify(plan)));
});

test("native CLI evaluation emits no graph when owner source inputs are missing or partial", () => {
  for (const inputs of [{}, { [repoInput]: sourceInputs[repoInput] }, { [branchInput]: sourceInputs[branchInput] }]) {
    const child = nativeEvaluation(inputs);
    assert.notEqual(child.status, 0);
    assert.equal(child.stdout, "");
    assert.match(child.stderr, /SHUFFIFY_RAILWAY_GITHUB_(?:REPO|BRANCH)/);
  }
});

test("an injected shared-reference context is preserved", async () => {
  const original = createRailwayContext({environment: "verification"});
  const names = ["DATABASE_URL", "SECRET_KEY", "TOKEN_ENCRYPTION_KEY", "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI", "WORKER_CONTROL_TOKEN"];
  const shared = Object.fromEntries(names.map(name => [name, {
    type: "sharedReference", name: `provided_${name}`,
  }]));
  shared.PUBLIC_ORIGIN = { type: "sharedReference", name: "provided_PUBLIC_ORIGIN" };
  const injected = await evaluate(sourceInputs, {...original, shared});
  for (const service of injected.resources.filter(resource => ["api", "worker"].includes(resource.name))) {
    for (const name of names) assert.equal(service.variables[name], shared[name]);
  }
  assert.equal(injected.resources.find(resource => resource.name === "api").variables.PUBLIC_ORIGIN, shared.PUBLIC_ORIGIN);
});
