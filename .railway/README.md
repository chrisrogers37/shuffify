# Service configuration

Install the pinned authoring dependencies with `npm ci --prefix .railway` and check the file with `npm run --prefix .railway typecheck`. Railway IaC requires CLI 5.42.1 or newer. Verify the linked project and environment before running `railway config plan`; inspect the complete proposed change before applying it.

The configuration declares API, worker, and Redis resources. It does not create or connect a Railway PostgreSQL resource. Both application services require the owner-set shared `DATABASE_URL`; the API's pre-deploy migration receives that same variable. Set it to the direct, unpooled Neon connection URL so the worker can use PostgreSQL `LISTEN` and scheduler advisory locks. There is no SQLite fallback or database autoprovisioning in this Railway layout.

Create or select a dedicated nonproduction branch in the existing Neon project `shuffify-db` for staging; a backup branch is not staging. Staging is never production: apply staging migrations through the API pre-deploy step and keep its `DATABASE_URL` pointed at that nonproduction branch. Verify the branch identity from Neon metadata before any migration. Production migrations run only during the planned cutover window; use the production branch URL only for that window. Do not copy a production URL into staging.

The existing live Railway PostgreSQL resource must be retained until its later retirement/cutover. This IaC definition no longer declares that resource, so a plan may propose deleting it. Do not apply a plan that removes it; review the full plan and keep the live database until the owner schedules its retirement.

The split API also requires the owner-set shared `PUBLIC_ORIGIN`. Set it to `https://shuffify-v2.vercel.app` for the current deployment. Change it to a future custom domain only as part of that domain's cutover. The private worker does not need `PUBLIC_ORIGIN`.

Both application services declare the same owner-selected GitHub repository and branch. Before running `railway config plan` or `railway config apply`, set `SHUFFIFY_RAILWAY_GITHUB_REPO` to the exact `owner/repository` slug and `SHUFFIFY_RAILWAY_GITHUB_BRANCH` to the exact branch name in the authoring shell. Both nonsecret inputs are required; missing, partial, or invalid inputs stop configuration evaluation. There is no default branch. These inputs are not container variables or shared credentials.

Use the same source inputs for planning and applying. To retain existing Git connections, use their currently connected repository and branch. Different values intentionally propose a source change; inspect that diff before applying. A separately connected source is not a substitute for declaring it in the managed configuration.

The worker starts one replica and uses its private control listener for health checks. Serving standby workers remain healthy without claiming scheduler leadership; authenticated control reports the actual memory scheduler leader and registrations separately. New Railway environments provide [IPv4 and IPv6 private addresses](https://docs.railway.com/networking/private-networking/how-it-works), so the configured IPv4 listener is reachable there. This configuration does not target older IPv6-only environments. Give only the API a public Railway domain. Vercel forwards browser API and authentication requests to that API.

Set these shared variable names in Railway before provisioning the services: `DATABASE_URL`, `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`, `WORKER_CONTROL_TOKEN`. Set `PUBLIC_ORIGIN` for the API as well. Values remain in the platform. The configuration references the owner-set database and Redis variables and connects the API to the worker's private hostname. Keep both services in the same environment.

In staging, the API's distinct pre-deploy command checks/upgrades the staging database before its process starts; a failure blocks that deployment. In production, run the API deployment and its pre-deploy migration only during the planned cutover window. Container startup never migrates. An empty development database first needs an explicit, one-time `flask create-schema` invocation with migration-process settings; upgrading never silently stamps an existing database.

Production database migration, old-host shutdown, backups, DNS cutover, and rollback are separate operational steps. Use a new isolated database for a preview deployment.
