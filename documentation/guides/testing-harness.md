# Local Test Harness

`scripts/harness.sh` runs the real app locally the way production is shaped:
PostgreSQL, Redis, CSRF on, a separate operation worker with an in-memory
APScheduler scheduler, and migrations
applied by `flask db upgrade` before the app starts, then verified at head when
it does. A new database is first built from the models by
`flask create-schema`. Everything it talks to is on this machine.

It exists so changes can be proven end to end, without ever touching a real
Spotify account or production. Spotify is a local fake (see
[The fake Spotify](#the-fake-spotify)), and behaviour tests drive the running
app through its flows against it (see [Behaviour tests](#behaviour-tests)).
A headless-browser pass over every page is one of them (see
[The browser pass](#the-browser-pass)).

## Commands

```bash
scripts/harness.sh setup          # idempotent: everything below, then (re)start and check the app
scripts/harness.sh setup --quick  # same, without lint and tests
scripts/harness.sh e2e            # behaviour tests against the running app (pytest args pass through)
scripts/harness.sh down           # stop the app and services (data is kept)
scripts/harness.sh status         # what is running, and any blocked connections
scripts/harness.sh reset-db       # drop and re-create the database, then setup --quick
```

After changing code or migrations, run `setup --quick` again: it always
restarts the app, so the schema check and the network guard's log cover the
code that is running now.

`setup` does, in order:

1. Creates `.venv/` with Python 3.12 (what CI uses) and installs
   `requirements/dev.txt`. Skipped when the requirements have not changed, or
   when `HARNESS_PYTHON` names an interpreter the caller manages.
2. Generates `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, and `WORKER_CONTROL_TOKEN` into
   `.harness/secrets.env`, once per checkout. They are never printed, and
   `.harness/` is gitignored.
3. Starts PostgreSQL on `127.0.0.1:54329` and Redis on `127.0.0.1:63799`, with
   their data under `.harness/`. PostgreSQL uses trust authentication on a
   loopback-only listener: a throwaway database needs no password.
4. Builds a new database from the models with `flask create-schema`, which
   stamps it at the migration head and leaves a database that already has a
   revision as it is, then applies migrations with `flask db upgrade`. Both
   run with `SHUFFIFY_MIGRATION_STEP` set for those processes only.
5. Unless `--quick`: runs `ruff check .` and `scripts/check-migration-chain.py`,
   and runs `pytest tests/ -m "not integration"` under `env -i` with
   CI's backend-job values and `FLASK_SKIP_DOTENV=1`, so other shell exports
   and Flask dotenv auto-loading cannot add settings to the suite.
6. Starts the fake Spotify on `http://127.0.0.1:8900`, a fresh Gunicorn
   gthread API on `http://127.0.0.1:8000`, and a separate operation worker,
   stopping this checkout's earlier processes. Worker health/control binds only
   to `127.0.0.1:8001`; `HARNESS_WORKER_CONTROL_PORT` selects another local port.
   API and worker share the private control token and explicit UTC timezone. Both use `127.0.0.1` rather than `localhost`: Spotify refuses
   `localhost` redirect URIs, and so does the fake. It
   then checks:
   - `/health` reports the database healthy;
   - the logged-out pages return 200;
   - a whole login works through the fake: `/login`, the fake's consent page
     as the seeded user `alice`, `/callback` exchanging the code, then a
     dashboard that greets her;
   - the fake Spotify recorded no call that departs from Spotify's
     documentation;
   - the app logged its schema as verified at Alembic head;
   - worker health confirms readiness and scheduler leadership;
   - the API did not start APScheduler;
   - the network guard blocked nothing.

The API, worker and fake keep running after `setup`. Their logs are
`.harness/app.log`, `.harness/worker.log` and `.harness/fake-spotify.log`.
Shutdown gives the worker up to 30 minutes to drain its current operation.
The worker stops before a database reset. Schedule definitions live in the
database; live registrations remain in worker memory.

## Safety

Processes the harness starts (the app, the migration step and the fake
Spotify) cannot reach Spotify or production at all.

- **Clean environment.** Harness processes start from `env -i` plus an
  explicit list of variables, excluding other shell exports such as real
  Spotify credentials, a production `DATABASE_URL`, or a Sentry DSN.
  `FLASK_SKIP_DOTENV=1` prevents Flask's CLI and `app.run()` from loading
  `.env` or `.flaskenv` files in the checkout or its ancestors. `config.py`
  also skips its own `load_dotenv()` when `APP_CONFIG=harness`.
- **`HarnessConfig` refuses unsafe settings** (`config.py`,
  `HarnessConfig.assert_safe`). The app will not start unless:
  - every host libpq would be handed for `DATABASE_URL` is on localhost, and
    `REDIS_URL` is on localhost. The database check reads the connection
    parameters libpq actually receives (SQLAlchemy's connect arguments, joined
    as psycopg2 joins them, parsed by libpq's own parser), not the URL text:
    the parsers disagree in ways that name another host, such as a `#` before
    the query, `?dsn=`, a key with a stray space, or a second host in a list.
    A `service` definition, and the `PGHOSTADDR`/`PGSERVICE` environment
    variables, are refused for the same reason;
  - `SENTRY_DSN` is empty;
  - the Spotify client credentials are the harness fakes (never registered
    with Spotify, so no real token can be minted from them);
  - `SPOTIFY_BASE_URL` points Spotify at this machine;
  - `SHUFFIFY_ALLOW_SCHEMA_DRIFT` is unset.

  The refusal is a `HarnessSafetyError`, not a `ValueError`, because
  `create_app()` logs and continues past a `ValueError` for non-production
  configs.
- **Network guard** (`harness/netguard/sitecustomize.py`). Every Python
  process the harness starts loads it at interpreter start. A socket may open
  only to a Unix socket or to a loopback address on one of the harness's own
  ports. Forward-resolving any hostname other than `localhost` is refused.
  - Loopback alone is not allowed through. Some environments route outbound
    HTTPS through a local proxy on `127.0.0.1`, so that rule would let a
    request to Spotify out.
  - A refusal raises `NetguardViolation`. It is deliberately not an `OSError`,
    so HTTP clients fail at once instead of retrying.
  - Every refusal is appended to `.harness/netguard-violations.log`, and
    `setup` fails if that file is not empty. A later `setup` shows any entries
    left from the previous run and keeps them in
    `netguard-violations.previous.log` before starting a fresh log.
  - A guard that fails to load stops the process instead of letting it run
    unguarded (Python's `site` would otherwise print the error and carry on).
    `setup` also starts a harness process and confirms the guard is active
    before it starts the app.
  - It wraps `connect`, `connect_ex`, `sendto` and `sendmsg` (the last two
    name a destination per call), and forward resolution (`getaddrinfo`,
    `gethostbyname`, `gethostbyname_ex`). Reverse lookups are left alone:
    they connect to nothing, and wrapping them would make
    `socket.getfqdn()` raise something other than `OSError`.
  - The guard covers sockets opened from Python (requests, urllib3,
    redis-py): every path to Spotify. psycopg2's libpq connects from C and is
    covered by `HarnessConfig`'s database check instead.
- **External services are vetted before contact.** With
  `HARNESS_SERVICES=external` the script starts and probes nothing; the first
  connection is the migration step, a harness process, so `HarnessConfig`
  checks both URLs before it. The services must already be up (CI's service
  containers have health checks). The script also unsets every `PG*`
  variable, so its own `psql`/`createdb` calls cannot be redirected.
- **Spotify is pointed at this machine** (`shuffify/spotify/endpoints.py`).
  The harness sets `SPOTIFY_BASE_URL=http://127.0.0.1:8900`. That one setting
  moves the three Spotify hosts the app uses to that origin, with their paths
  unchanged:
  - the Web API (`/v1/...`);
  - OAuth (`/authorize`, `/api/token`, `/api/revoke`);
  - the public pages the playlist scraper reads (`/embed/playlist/...`,
    `/playlist/...`).

  The CSP `form-action` follows it, so the login form can follow the OAuth
  redirect there.

  The fake Spotify listens there. Safeguards around the setting:
  - It is resolved once, at import.
  - It accepts only a loopback origin, because the token endpoint receives the
    client secret.
  - Production refuses to start with it set at all
    (`config.validate_required_env_vars`).
  - `HarnessConfig` refuses to start unless the client resolved to a local
    origin.
  - `setup` logs in through the local `/authorize`.
  - With it unset, every URL is byte-for-byte the production one
    (`tests/test_spotify_endpoints.py`).
- **The scheduler runs only inside harness processes.** Those processes can
  reach nothing but local services. It is disabled for the migration step.

## The fake Spotify

`harness/fake_spotify/` is a small Flask app standing in for all three Spotify
hosts at one origin. It answers every call shuffify makes:

| Spotify host | Paths |
|---|---|
| `accounts.spotify.com` | `/authorize` (a page to pick a seeded user), `/api/token` |
| `api.spotify.com` | `/v1/me`, `/v1/me/playlists` (GET, POST), `/v1/playlists/{id}` (GET, PUT), `/v1/playlists/{id}/items` (GET, POST, PUT, DELETE), `/v1/tracks/{id}`, `/v1/audio-features`, `/v1/search` |
| `open.spotify.com` | `/embed/playlist/{id}`, `/playlist/{id}` (the scraper's pages) |

Its rule: **shuffify decides which calls it serves, Spotify's documentation
decides how each behaves.** It was never written from what shuffify expects,
because a fake built that way agrees with the app's mistakes.

- **Shapes.** `scripts/extract-spotify-shapes.py` pulls the structure of
  those operations (query parameters, request bodies and responses: field
  names, types, nullability, required fields, enums and bounds; no
  descriptions or values), their scopes and whether each is deprecated, out
  of Spotify's published OpenAPI schema into
  `harness/fake_spotify/shapes.json`, which is committed. A test fails if
  shuffify calls a deprecated operation other than `/audio-features`.
  - The fake models a Development Mode app, so the fields Spotify removed
    for those apps are left out (`DEV_MODE_REMOVED` in `shapes.py`).
  - Where Spotify is known to differ from its schema, `shapes.py` corrects
    the loaded shapes, each with a comment saying how that is known.
  - Regenerate with `python scripts/extract-spotify-shapes.py`; `--check`
    reports drift. Both need network access to `developer.spotify.com`,
    Spotify's documentation site. Nothing is sent to Spotify's API.
- **Checked on every call.** The fake checks each `/v1` request against the
  documented parameters and body before serving it, and each response
  against the documented shape for its status after.
  - A request that breaks a documented constraint (a missing required field,
    a `limit` out of range, a value outside an enum) gets the `400` Spotify
    would give.
  - Anything undocumented on either side is recorded, logged, and listed at
    `GET /_fake/violations`: a parameter or body field the client sends that
    Spotify does not read, a body that is not JSON, a call to an endpoint
    the fake does not serve (such as one Spotify removed), or a response the
    fake got wrong. Both test suites and `scripts/harness.sh` fail unless
    that list is empty.
- **Behaviour.** It follows the reference, including the parts that catch
  code out:
  - pages default to 20 items (playlists) or 5 (search), with at most 50 or 10;
  - another user's playlist returns metadata only, and its items are a 403;
  - writes take at most 100 URIs;
  - removing a URI removes every occurrence;
  - Change Playlist Details answers `200` with no body;
  - a write needs the modify scope for the playlist's visibility
    (`playlist-modify-public` or `-private`), and a creation the one for the
    new playlist's; without `playlist-read-private`, `/me/playlists` lists
    public playlists only. Missing scopes are a 403. The scope the fake asks
    for must be one the schema lists for that operation;
  - redirect URIs follow Spotify's rule: HTTPS, or HTTP on a
    loopback IP literal, and never `localhost`, even when registered;
  - `/audio-features` is a 403, as it is for Development Mode apps;
  - every href and `next` link stays on the fake's own origin, and images are
    `data:` URIs, so nothing a client follows leaves the machine.
- **Profile.** It models a Spotify Development Mode app: the field changes
  for those apps apply, and the endpoints removed for them
  (`POST /users/{id}/playlists`, `GET /tracks`) are not served. A call to one
  is recorded as a violation.
- **The real client is held to it.** `tests/test_fake_spotify_client_contract.py`
  runs shuffify's own `SpotifyAuthManager`, `SpotifyAPI` and scraper, unmocked,
  against the fake on a loopback socket, with every Spotify URL shuffify
  builds (listed in `tests/spotify_urls.py`) repointed there. A test fails on
  a retry backoff, because it means the client misread a documented
  response; on anything the fake recorded; and on a lookup of any other
  host or a connection to anywhere but the fake. A test in
  `tests/test_spotify_endpoints.py` scans every shuffify module and class
  for a Spotify URL missing from that list.
- **Seeded state** (`seed.json`). Seven users, `alice` to `grace`, which is
  more than Spotify's five-user cap for Development Mode apps. A generated
  catalogue of 384 tracks. Playlists chosen to exercise code paths:

  | Playlist | Owner | Exercises |
  |---|---|---|
  | Morning Mix | alice | the basic shuffle, 40 tracks |
  | Long Haul | alice | 250 tracks: several pages, three write batches |
  | Morning Mix [Archive] | alice | an empty archive pair |
  | Old Faithful | alice | the legacy `tracks`/`track` response shape |
  | On Repeat | alice | duplicate tracks |
  | Road Trip | bob, collaborative | alice can edit it |
  | Bob's Finds | bob, public, alice follows | a raid source she cannot edit; the scraper reads its embed page |
  | Late Night, Focus, Sunday, ... | others | public playlists for search and raids |

  IDs are stable 22-character base62 strings derived from names, so a database
  that outlives the fake still points at the same playlists.
- **Tokens** are signed and stateless, so a refresh token the app stored
  before a restart of the fake still works after it.
- **Logging in by hand.** Open `http://127.0.0.1:8000`, log in, and pick a
  user on the fake's consent page. Automated clients add `fake_user=<id>` to
  the authorize URL instead.
- **Inspecting and resetting.** Outside Spotify's API, `/_fake/playlists`,
  `/_fake/playlists/{id}` and `/_fake/users` show the state,
  `/_fake/violations` what it recorded, and `POST /_fake/reset` restores
  the seed (keeping the record of violations) and clears any armed faults.
  State is in memory, so restarting the fake also resets it; playlists
  created since are gone.
- **Faults** (`faults.py`). Spotify documents how it answers when it is
  busy, out of quota or failing, but no account can be made to answer that
  way on demand. A test arms one with `POST /_fake/faults`:

  ```json
  {"operation": "POST /playlists/{playlist_id}/items", "kind": "quota",
   "playlist_id": "<id>", "skip": 0, "times": 1}
  ```

  | `kind` | Answer |
  |---|---|
  | `rate_limit` | 429, with `Retry-After: <retry_after>` when given (Spotify "normally" sends it) |
  | `quota` | 429 with `{"error": {"status": 429, "message": "Too many requests", "reason": "QUOTA_EXCEEDED"}}`, Spotify's spent-quota answer |
  | `server_error` | 500, 502 or 503 (`status`, default 503), before the operation runs, so nothing is applied |
  | `slow` | served normally, `delay` seconds late (at most 60), or as soon as it is released. A write is still applied after a client that gave up on it |

  `operation` is one of the operations in `shapes.json`, and `playlist_id`
  narrows a playlist operation to one playlist. The fault lets `skip`
  matching calls through, answers the next `times` of them, then the fake
  serves normally again. The fault comes after the request checks, so a
  request Spotify would refuse still gets its 400. `GET /_fake/faults` lists
  each fault with how many calls it saw and answered; `DELETE /_fake/faults`
  and `/_fake/reset` clear them. `POST /_fake/faults/release` ends the wait
  of every armed slow fault: held calls are answered at once and its later
  calls are not held (they still count as answered). A test that needs a
  write in flight arms a generous `delay` as a ceiling, acts while the call
  is held, then releases it instead of waiting the delay out; a slow fault
  armed after a release holds again. The published schema's error object
  predates the quota `reason`, so `shapes.py` adds it to the 429's error
  object only.
- **Isolation.** It runs under the network guard, allowed only its own port,
  and starts from an empty environment: it gets none of the app's settings
  or secrets.
- **Not modelled** (each refused with a 400 that says so, where a client
  could send it): reordering by `range_start`/`insert_before` (shuffify
  always sends the full order); `collaborative` on a write; searching for
  types other than tracks and playlists, or several at once; `fields`
  filters with dots or `!` exclusions. Also not modelled yet: faults on the
  OAuth and public-page hosts (faults cover `/v1` operations), the
  form-encoded client credentials Spotify also accepts (shuffify sends HTTP
  Basic), and how Spotify's real login page looks.

## Scenarios and behaviour labels

The fake follows Spotify's documentation; it has not been checked against
real Spotify. Two pieces track how far that goes.

**Labels** (`harness/fake_spotify/behaviours.json`). Every behaviour the fake
models is listed with where it comes from and a label: `docs-only` (from
Spotify's references, changelogs and announcements alone) or
`checked-live` (confirmed against real Spotify too, saying how), such as the
public embed page, which the live integration tests read, and `limit=0`
being refused. The fault
answers (rate limit, spent quota, 5xx) are listed too, `docs-only`: Spotify
cannot be made to give them on demand. Each behaviour names the scenario a
live replay checks it with, or says why none can (a scope check needs a
second token; a login is not a Web API call).

**Scenarios** (`harness/scenarios/<id>.json`). A scenario is a behaviour as
Web API calls and the answers Spotify should give, with no real ID in it:
playlists are roles the target binds (`own`, a playlist of the user's the
scenario may write; `foreign`, another user's public playlist, read only)
and tracks are `{track1}`, `{track1.id}`, ... . A step names a call, an
optional query and body, and the expected answer: the status, and as
needed the items' track URIs in order, fields present and equal, fields
absent, list lengths, or no body. `harness/scenarios/__init__.py` documents
the format and runs a scenario against a target; `harness/scenarios/fake.py`
is the target for the fake (in process, as alice, each `own` role a new
private playlist). `tests/test_scenarios.py` replays every scenario against
the fake, fails if the fake recorded anything, and keeps the labels and the
scenarios in step.

Live replay compares these same scenarios with real Spotify through a target
that supplies authentication and playlist bindings. The runner's `validate`
check rejects writes to playlists without an `own` role and calls that create
playlists before making any request. A live target must bind `own` roles only
to explicitly allowlisted test playlists. Mark a behaviour `checked-live` only
when a live replay confirms it; a fake-only replay does not establish Spotify's
response. This repository supplies the fake target; a live target must be
provided separately.

## Behaviour tests

`scripts/harness.sh e2e` runs `tests/e2e/` against the app and fake that
`setup` left running. The tests run as a harness process: an empty
environment, the network guard on, and only the harness's own ports
reachable. A plain `pytest tests/` collects none of them, since only `e2e`
sets the URLs they need. Extra arguments go to pytest
(`scripts/harness.sh e2e -k raid -x`).

Each test logs in through the fake as a seeded user, as the browser would:
`/login`, the fake's consent page, `/callback` (which must land on a dashboard
greeting that user), then the CSRF token from the page in the header the app's
JavaScript sends. It then calls the app's own routes and checks Spotify's side
of the outcome in the fake's state (`/_fake/playlists/{id}`), not in what the
app says about itself. The Workshop page edits in the browser and commits the
result, so its tests stage the edit themselves, as the page's JavaScript does,
and go through the app for the rest: search, loading another playlist, and the
commit. Covered:

| Flow | What is checked |
|---|---|
| Login | the app lists exactly the playlists alice can edit (owned or collaborative) |
| Each shuffle algorithm, on 250 tracks | a new order of the same tracks; undo restores the old one |
| Several shuffles, then undo each | each undo walks back one order |
| Shuffling a playlist alice cannot edit | refused with a 403, and the playlist untouched |
| Workshop track operations (remove, move, add a search result) | exactly the staged order; undo restores it |
| Workshop merge from another of her playlists | the merged order, with the source untouched |
| Workshop, another user's playlist | the app answers in restricted mode: its name and a search, no tracks |
| Raid: link a staging playlist, watch Bob's Finds, raid, keep two, dismiss one | the private `[Raids]` playlist, the staged and remaining tracks, a target with the kept ones, and a second raid, for as many as the source has, that takes exactly the tracks not yet seen |
| A scheduled shuffle (cron, every minute) | the worker’s in-memory scheduler enqueues a durable routine operation and the worker reorders the playlist (up to two minutes); the test then deletes the schedule through the app |
| A scheduled shuffle run while Spotify rate-limits its first write | the client waits out `Retry-After`, the playlist is reordered, and the run succeeds |
| A scheduled shuffle whose second write batch Spotify refuses for quota | the run fails as `failed_rolled_back` and the playlist is exactly as it was, not cut to the first 100 tracks |

**Every test starts from the seed.** Before and after each one, failed or
not, the fixture empties the app's tables (all but `alembic_version`, so a raid
does not remember the last test's dismissed tracks), clears the app's Spotify
cache and rate-limit counters in Redis, then resets the fake. It does so only
in a process the network guard is loaded into (which `e2e` starts), and only
after `HarnessConfig.assert_safe()`, the check the harness app passes before it
starts. It connects through the very URLs that check read; the database's
reaches libpq exactly as the check parsed it. A test fails if the fake
recorded any call that departs from Spotify's documentation while it ran.
Whether the tests pass or not, `e2e` then checks the network guard's log and
the fake's record, as `setup` does: it fails if either recorded anything, and
otherwise exits with pytest's status.

### The browser pass

`tests/e2e/test_pages.py` opens every page in headless Chromium (Playwright),
at phone (360x800, the most common Android width), folding-phone cover
screen (280x653, the narrowest in common use) and desktop (1440x900) sizes. It visits the logged-out pages (landing, terms, privacy, a missing
page), then logs in as alice the way a person does: the landing page's
consent box and button, then the fake's consent page. As alice it visits the
dashboard, schedules and activity before anything is made (the activity
already lists her log-ins), then makes a shuffle and a schedule and visits the dashboard, the Workshop hub, a
playlist's Workshop, schedules, settings, activity and a missing page. Each
visit must:

- answer 200 (404 for the missing page) at the URL asked for, with no
  redirect, by the server or by a script;
- log no console error and throw no uncaught exception (Chromium reports a
  CSP violation as a console error). The one exception is the line Chromium
  logs for the missing page's own 404;
- have every request it makes succeed;
- at both phone widths, not scroll sideways.

Each test collects every problem before failing, so one run reports them all.
A full-page screenshot of every visit goes to `.harness/screenshots/`
(`<page>-<phone|fold|desktop>.png`), and CI keeps them as the `page-screenshots`
artifact.

The browser reaches nothing off this machine:

- its resolver refuses every hostname and every IP address but `127.0.0.1`;
- it uses no proxy, whatever the environment says (`--proxy-server=direct://`),
  sends no non-proxied WebRTC UDP, and pages have no `RTCPeerConnection` at
  all (WebRTC could otherwise reach a TURN server over TCP);
- within `127.0.0.1`, a route handler lets only the app's and the fake's
  requests through and aborts and reports any other; a page's WebSockets are
  reported without being opened, and any that open anyway (a worker's) are
  reported too; service workers are blocked.

No page loads a script from another origin: the React app bundles its
dependencies, SortableJS included. `tests/test_harness_vendor.py`, in the unit
suite, parses the frontend entry and requires every script to come from the
app's own origin.

## CI

The `harness` job in `.github/workflows/ci.yml` runs
`scripts/harness.sh setup --quick` against PostgreSQL 16 and Redis 7 service
containers (`HARNESS_SERVICES=external`) on every pull request. It proves:
- `flask create-schema` builds a new PostgreSQL database from the models,
  and `flask db upgrade` runs on it;
- the app boots on it with the schema gate, Redis sessions and the scheduler
  live;
- a login through the fake Spotify reaches the dashboard, and every call the
  app made on the way matched Spotify's documentation;
- every behaviour test passes (`scripts/harness.sh e2e`), the browser pass
  included, whose screenshots it keeps as the `page-screenshots` artifact.

The backend job already covers lint and tests, including both fake Spotify
suites, so this job skips them.

## Options

| Variable | Default | Meaning |
|---|---|---|
| `HARNESS_SERVICES` | `local` | `external` uses `DATABASE_URL`/`REDIS_URL` as given (they must be on localhost) |
| `HARNESS_PYTHON` | `.venv/bin/python` | An interpreter that already has the dependencies |
| `HARNESS_APP_PORT` | `8000` | Port the app listens on |
| `HARNESS_FAKE_SPOTIFY_PORT` | `8900` | Origin `SPOTIFY_BASE_URL` points at, where the fake Spotify listens |

## Requirements

Linux with bash 4.4 or later (the script uses `mapfile`, `setsid` and
`runuser`; macOS's bash 3.2 will not run it). Python 3.12 (falls back to
`python3` with a warning), and PostgreSQL server binaries (`initdb`,
`pg_ctl`) plus `redis-server` for local mode. The browser pass needs
Playwright's headless Chromium: `python -m playwright install --only-shell chromium`, or
`PLAYWRIGHT_BROWSERS_PATH` naming a directory that already has it. `e2e` passes
that, and `XDG_CACHE_HOME`, through to the tests. As root, the script runs
PostgreSQL as the `postgres` user, since PostgreSQL refuses to run as root.

The local PostgreSQL trusts every connection on `127.0.0.1`, so any user on
the machine can reach it as a superuser. That suits a single-user container
or laptop; do not run the harness on a shared host. Its Unix socket is
restricted to the `postgres` user.
