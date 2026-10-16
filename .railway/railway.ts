import { createRailwayContext, defineRailway, postgres, project, redis, service } from "railway/iac";

export default defineRailway((rawContext) => {
  // Native CLI evaluation supplies {}; preserve complete SDK-injected contexts.
  const ctx = rawContext.shared ? rawContext : createRailwayContext(rawContext);
  const database = postgres("postgres");
  const cache = redis("redis");
  const shared = {
    APP_CONFIG: "production",
    DATABASE_URL: database.env.DATABASE_URL,
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
      SHUFFIFY_PROCESS_ROLE: "api",
      PORT: "8000",
      WORKER_CONTROL_URL: "http://${{worker.RAILWAY_PRIVATE_DOMAIN}}:5001",
    },
  });
  return project("shuffify-v2", { resources: [database, cache, worker, api] });
});
