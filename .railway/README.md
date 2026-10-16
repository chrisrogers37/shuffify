# Service configuration

Install the pinned authoring dependencies with `npm ci --prefix .railway` and check the file with `npm run --prefix .railway typecheck`. Railway IaC requires CLI 5.42.1 or newer. Verify the linked project and environment before running `railway config plan`; inspect the complete proposed change before applying it.

The configuration declares API, worker, PostgreSQL, and Redis resources. It manages service settings without declaring a Git source. Connect the reviewed repository branch separately. The worker starts one replica and uses its private control listener for health checks. Serving standby workers remain healthy without claiming scheduler leadership; authenticated control reports the actual memory scheduler leader and registrations separately. New Railway environments provide [IPv4 and IPv6 private addresses](https://docs.railway.com/networking/private-networking/how-it-works), so the configured IPv4 listener is reachable there. This configuration does not target older IPv6-only environments. Give only the API a public Railway domain. Vercel forwards browser API and authentication requests to that API.

Set these shared variable names in Railway before provisioning the services: `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`, `WORKER_CONTROL_TOKEN`. Values remain in the platform. The configuration references database/cache variables directly and connects the API to the worker's private hostname. Keep both services in the same environment.

Run the schema upgrade once before deploying the worker. The API's distinct pre-deploy command also checks/upgrades the database before its process starts. A failed pre-deploy command blocks the API deployment. Container startup never migrates. An empty development database first needs an explicit, one-time `flask create-schema` invocation with migration-process settings; upgrading never silently stamps an existing database.

Production database migration, old-host shutdown, backups, DNS cutover, and rollback are separate operational steps. Use a new isolated database for a preview deployment.
