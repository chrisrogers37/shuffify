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

## Removal candidates and test dependencies

A route-reference audit of the page templates and scripts identified the 20
handlers below without a direct current UI caller. These handlers remain
registered. The table records E2E dependencies so removal does not silently
replace application setup with direct database writes. Dynamic request builders
and future consumers require a fresh reference check before any deletion.

| Route | Handler | E2E dependency under `tests/e2e/` |
| --- | --- | --- |
| `POST /api/playlist/<playlist_id>/pair/unarchive` | `unarchive_tracks` | No direct positive E2E caller found |
| `GET /api/playlist/<playlist_id>` | `get_playlist` | No direct positive E2E caller found |
| `GET /api/playlist/<playlist_id>/stats` | `get_playlist_stats` | No direct positive E2E caller found |
| `POST /api/playlist/<playlist_id>/raid-watch` | `raid_watch` | test_raid, test_live_operation_outcomes, test_routine_activation, test_guided_routines, test_playlist_setup, test_workshop_source_recovery |
| `POST /api/playlist/<playlist_id>/raid-watch-search` | `raid_watch_search` | Prerequisite lock coverage in test_routine_activation |
| `POST /api/playlist/<playlist_id>/raid-schedule-toggle` | `raid_schedule_toggle` | No direct positive E2E caller found |
| `POST /api/playlist/<playlist_id>/drip-schedule-toggle` | `drip_schedule_toggle` | No direct positive E2E caller found |
| `POST /api/playlist/<playlist_id>/raid-schedule-create` | `raid_schedule_create` | No direct positive E2E caller found |
| `PUT /api/playlist/<playlist_id>/raid-schedule` | `raid_schedule_update` | No direct positive E2E caller found |
| `GET /api/playlist/<playlist_id>/raid-schedule/history` | `raid_schedule_history` | No direct positive E2E caller found |
| `POST /api/playlist/<playlist_id>/pending-raids/promote` | `pending_raids_promote` | No direct positive E2E caller found |
| `POST /api/playlist/<playlist_id>/pending-raids/unpromote` | `pending_raids_unpromote` | No direct positive E2E caller found |
| `POST /api/playlist/<playlist_id>/pending-raids/promote-all` | `pending_raids_promote_all` | No direct positive E2E caller found |
| `POST /api/playlist/<playlist_id>/pending-raids/dismiss-all` | `pending_raids_dismiss_all` | Negative request assertion in test_compatibility_and_dialogs |
| `GET /api/playlist/<playlist_id>/schedules` | `playlist_schedules` | Read assertions in test_pages, test_compatibility_and_dialogs, test_live_operation_outcomes, test_guided_routines, test_playlist_setup |
| `POST /api/schedules/create` | `create_schedule` | test_scheduler, test_faults, test_pages, test_live_operation_outcomes, test_playlist_setup; helper imported by test_guided_routines |
| `PUT /api/schedules/<int:schedule_id>` | `update_schedule` | Lock coverage in test_routine_activation |
| `POST /api/schedules/<int:schedule_id>/toggle` | `toggle_schedule` | Setup in test_live_operation_outcomes and test_playlist_setup; lock coverage in test_routine_activation |
| `GET /api/playlist/<playlist_id>/rotation-status` | `rotation_status` | No direct positive E2E caller found |
| `POST /api/workshop/search-playlists` | `workshop_search_playlists` | No direct positive E2E caller found |

The `/api/playlist/<playlist_id>/pending-raids/finalize-all` alias shares the
finalize handler and is used by source-recovery setup in
`test_workshop_source_recovery`.

Setup replacements use the playlist setup context, impact review, and save flow,
then routine context, review, activation, and state changes. Equivalent lock and
recovery coverage must exercise those flows before removing their setup routes.
Route deletion belongs in a separate change with the setup migration verified.
