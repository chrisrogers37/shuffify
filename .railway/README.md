# Service configuration

Install the pinned authoring dependencies with `npm ci --prefix .railway` and check the file with `npm run --prefix .railway typecheck`. Railway IaC requires CLI 5.42.1 or newer. Verify the linked project and environment before running `railway config plan`; inspect the complete proposed change before applying it.

The configuration declares API, worker, PostgreSQL, and Redis resources. Both application services declare the same owner-selected GitHub repository and branch. Before running `railway config plan` or `railway config apply`, set `SHUFFIFY_RAILWAY_GITHUB_REPO` to the exact `owner/repository` slug and `SHUFFIFY_RAILWAY_GITHUB_BRANCH` to the exact branch name in the authoring shell. Both nonsecret inputs are required; missing, partial, or invalid inputs stop configuration evaluation. There is no default branch. These inputs are not container variables or shared credentials.

Use the same source inputs for planning and applying. To retain existing Git connections, use their currently connected repository and branch. Different values intentionally propose a source change; inspect that diff before applying. A separately connected source is not a substitute for declaring it in the managed configuration.

The worker starts one replica and uses its private control listener for health checks. Serving standby workers remain healthy without claiming scheduler leadership; authenticated control reports the actual memory scheduler leader and registrations separately. New Railway environments provide [IPv4 and IPv6 private addresses](https://docs.railway.com/networking/private-networking/how-it-works), so the configured IPv4 listener is reachable there. This configuration does not target older IPv6-only environments. Give only the API a public Railway domain. Vercel forwards browser API and authentication requests to that API.

Set these shared variable names in Railway before provisioning the services: `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`, `WORKER_CONTROL_TOKEN`. Values remain in the platform. The configuration references database/cache variables directly and connects the API to the worker's private hostname. Keep both services in the same environment.

Run the schema upgrade once before deploying the worker. The API's distinct pre-deploy command also checks/upgrades the database before its process starts. A failed pre-deploy command blocks the API deployment. Container startup never migrates. An empty development database first needs an explicit, one-time `flask create-schema` invocation with migration-process settings; upgrading never silently stamps an existing database.

Production database migration, old-host shutdown, backups, DNS cutover, and rollback are separate operational steps. Use a new isolated database for a preview deployment.
