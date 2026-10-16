# API surface and setup dependencies

OAuth lives at `/login`, `/callback`, and `/logout`; `/health` reports service
health. Feature JSON lives under `/api`, uses the success/error envelope, and
sends `Cache-Control: no-store`. HTML page routes remain available alongside the
API contexts.

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
