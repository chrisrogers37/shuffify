# API surface and setup dependencies

OAuth lives at `/login`, `/callback`, and `/logout`; `/health` answers 200 when
the API can query its database and 503 when it cannot, and Railway's deploy
healthcheck reads it. Feature JSON lives under `/api`, uses the success/error
envelope, and sends `Cache-Control: no-store`. The React frontend serves every
product page; Flask has no HTML page routes.

## Page contexts

The contexts are `/api/session`, `/api/settings`, `/api/activity`, `/api/library`,
`/api/routines`, and `/api/workshop/<playlist_id>`. Settings accepts GET and POST
with the same response type. Library refresh is `GET /api/library?refresh=1`.
The TypeScript contracts in `web/src/api/contracts.ts` generate the JSON schemas
in `web/schemas/`; Python and Node validate the same examples.

Session responses omit CSRF tokens while signed out and consume an authored
notice once. Context return targets use `playlist`, `routines`, `routine`, or
`library`. Activity origin is null when no origin was recorded. Workshop
returns missing or inaccessible playlists as JSON 404s.

## Routine and shared setup writes

Routine creation and edits use `POST /api/playlist/<playlist_id>/routines/review`
and `POST .../routines/activate`. The activation uses the normalized definition,
intent, dependency revision and original request key returned by review. Exact
pause/resume uses `PUT /api/routines/<schedule_id>/state` with the desired enabled
state and current schedule revision. Exact schedule deletion, history,
latest-results, run-context and queued Run now remain available.

### Routine limits

A signed-in account can save **20 routines**. A target playlist can have **6 routines
across all owners**, enough for one of each supported operation. Paused routines
occupy a slot in both totals. Deleting a routine frees its slots. An edit at
capacity reuses the routine's slot. `create_another` consumes a new slot.

The interval, daily and weekly presets keep their timing. Advanced cron must
select one distinct minute in each matching hour. Lists, ranges and steps use
APScheduler's minute-field rules. For example, `0 * * * *` runs hourly and
`0,5 9 * * mon` is refused because its Monday fires are five minutes apart.
If a timezone transition puts two advanced slots less than 60 elapsed minutes
apart, the worker skips the later slot, including during restart recovery.
Daily and weekly presets retain their original behavior in a repeated local
hour. New reviews record an explicit advanced timing choice, so even an
advanced expression identical to a preset keeps the elapsed-time floor.
Older rows used one representation for both choices. A stored expression
matching a daily, weekly or calendar-day preset keeps the preset's behavior.

Existing rows keep their saved definitions. The worker omits sub-hour cron from
registration and skips new callbacks for it. An enabled routine with valid
timing continues firing if its owner or target already exceeds a count limit.
An edit or resume requires compliant timing and projected totals within both
limits. Correct the cron in an edit and delete excess routines as needed.
State-only pause and deletion are available throughout. An exact activation
replay still returns the original receipt and current row state.

The write checks run after the activation receipt and revision checks, under
the destination playlist's configuration lock. On creation PostgreSQL also locks
the owner row through commit, so two creates for different targets share the last-slot
check. A cadence refusal returns HTTP 422. A count refusal returns HTTP 409
with `routine_user_limit` or `routine_target_limit`. The same checks apply to
raid auto-watch creation, source-list edits during watch or unwatch, and setup
saves that edit a routine's source list. A refused write also rolls back any
staged source changes.

### Shared setup

Shared sources, collection and archive settings use `GET .../setup/context`,
`POST .../setup/impact`, then `PUT .../setup/settings`. Save sends the original
strict change list and the review's signed acknowledgment. Impact's expanded
change summaries are for display, not a replacement request body. Saving shared
settings never starts or schedules a routine. Creating a Spotify asset is a
separate queued request to `POST .../setup/spotify-playlists`; linking it requires
a later explicit settings Save.

The older schedule create/update/toggle and implicit raid-schedule endpoints are
not registered. Neither are the playlist detail/audio-statistics reads,
name-based playlist search, direct watch/watch-search, pending promote/unpromote
mutators or the finalize-all alias. Workshop ordered reads use the Workshop
context/current-state; pending staging stays local until reviewed target Save and
exact-ID `/pending-raids/finalize` cleanup.

Direct `POST /api/playlist/<playlist_id>/pair/unarchive` and
`POST /api/playlist/<playlist_id>/pending-raids/dismiss-all` remain supported queued
commands. Visible inbox controls capture exact pending IDs for `/dismiss`, so
new arrivals are not silently included in an earlier selection.

`tests/routine_flow.py` sets up ordinary harness data through the reviewed HTTP
flows and reads exact routine rows. The unavailable-source compatibility fixture
is confined to `tests/e2e/legacy_fixtures.py`: it checks the local fake-only harness
guard before any I/O and requires an owned paused routine before inserting a
previously saved legacy source. It is not an application endpoint.
