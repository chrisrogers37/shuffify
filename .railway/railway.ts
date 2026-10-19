import { createRailwayContext, defineRailway, github, postgres, project, redis, service } from "railway/iac";

function authoringEnvironment() {
  return (globalThis as typeof globalThis & {
    process: { env: Record<string, string | undefined> };
  }).process.env;
}

function configuredGitSource() {
  const env = authoringEnvironment();
  const repo = env.SHUFFIFY_RAILWAY_GITHUB_REPO;
  const branch = env.SHUFFIFY_RAILWAY_GITHUB_BRANCH;
  if (!repo || !branch) {
    throw new Error("Set both SHUFFIFY_RAILWAY_GITHUB_REPO and SHUFFIFY_RAILWAY_GITHUB_BRANCH before evaluating Railway configuration.");
  }
  const [owner, repository, ...extra] = repo.split("/");
  if (
    extra.length || !owner || owner.length > 39 ||
    !/^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$/.test(owner) ||
    !repository || repository.length > 100 ||
    !/^[A-Za-z0-9._-]+$/.test(repository) || [".", ".."].includes(repository)
  ) {
    throw new Error("SHUFFIFY_RAILWAY_GITHUB_REPO must be a GitHub owner/repository slug, without a URL or credentials.");
  }
  if (
    branch === "HEAD" || branch.startsWith("-") || branch.startsWith("/") ||
    branch.endsWith("/") || branch.endsWith(".") || branch.includes("//") ||
    branch.includes("..") || branch.includes("@{") ||
    /[\u0000-\u0020\u007f~^:?*\[\\]/u.test(branch) ||
    branch.split("/").some(part => part.startsWith(".") || part.endsWith(".lock"))
  ) {
    throw new Error("SHUFFIFY_RAILWAY_GITHUB_BRANCH must be a valid Git branch name.");
  }
  return github(repo, { branch });
}

// The pre-Neon Railway PostgreSQL holds rollback data and no service reads it.
// It stays declared, wired to nothing, so a plan never deletes it by omission;
// retiring it takes SHUFFIFY_RAILWAY_RETIRE_POSTGRES=1.
function retainedDatabases() {
  const retire = authoringEnvironment().SHUFFIFY_RAILWAY_RETIRE_POSTGRES;
  if (retire === "1") return [];
  if (retire) {
    throw new Error("SHUFFIFY_RAILWAY_RETIRE_POSTGRES must be 1 to retire the Railway PostgreSQL resource, or unset to keep it.");
  }
  return [postgres("postgres")];
}

export default defineRailway((rawContext) => {
  const source = configuredGitSource();
  // Native CLI evaluation supplies {}; preserve complete SDK-injected contexts.
  const ctx = rawContext.shared ? rawContext : createRailwayContext(rawContext);
  const cache = redis("redis");
  const shared = {
    APP_CONFIG: "production",
    DATABASE_URL: ctx.shared.DATABASE_URL,
    REDIS_URL: cache.env.REDIS_URL,
    SECRET_KEY: ctx.shared.SECRET_KEY,
    TOKEN_ENCRYPTION_KEY: ctx.shared.TOKEN_ENCRYPTION_KEY,
    SPOTIFY_CLIENT_ID: ctx.shared.SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET: ctx.shared.SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI: ctx.shared.SPOTIFY_REDIRECT_URI,
    WORKER_CONTROL_TOKEN: ctx.shared.WORKER_CONTROL_TOKEN,
    SCHEDULER_ENABLED: "true",
    SCHEDULER_TIMEZONE: "UTC",
  };
  const worker = service("worker", {
    source,
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "python -m shuffify.worker",
    replicas: 1,
    healthcheck: "/health",
    healthcheckTimeout: 120,
    deploy: {
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
      drainingSeconds: 1800,
    },
    env: {
      ...shared,
      SHUFFIFY_PROCESS_ROLE: "worker",
      PORT: "5001",
      WORKER_CONTROL_HOST: "0.0.0.0",
      WORKER_CONTROL_PORT: "5001",
    },
  });
  const api = service("api", {
    source,
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    start: "gunicorn --config gunicorn.conf.py run:app",
    preDeploy: "sh scripts/pre-deploy.sh",
    replicas: 1,
    healthcheck: "/health",
    healthcheckTimeout: 120,
    deploy: {
      preDeployTimeoutSeconds: 300,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
    },
    env: {
      ...shared,
      PUBLIC_ORIGIN: ctx.shared.PUBLIC_ORIGIN,
      SHUFFIFY_PROCESS_ROLE: "api",
      PORT: "8000",
      WORKER_CONTROL_URL: "http://${{worker.RAILWAY_PRIVATE_DOMAIN}}:5001",
    },
  });
  return project("shuffify-v2", { resources: [cache, worker, api, ...retainedDatabases()] });
});
