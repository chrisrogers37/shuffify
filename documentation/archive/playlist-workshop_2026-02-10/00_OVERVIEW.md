# Playlist Workshop Enhancement Suite — Overview

**Session:** playlist-workshop
**Date:** 2026-02-10
**Scope:** Full system — UX-focused product enhancement

---

## User's Stated Goals

**Core problem:** Spotify's playlist reordering UX is painful at scale — drag one song at a time, scroll endlessly. Playlist curators and power users need better tools.

**Vision:** Transform Shuffify from a "shuffle tool" into a **playlist workshop** — an interactive workspace for crafting perfect playlists with intelligent tools, external sources, and automation.

**Constraints:**
- Spotify audio features API deprecated (2025)
- 25-user whitelist cap (Spotify dev mode)
- No public deployment path

---

## Phase Summary

| Phase | Title | PR Title | Impact | Effort | Risk | Est. Days | Status |
|-------|-------|----------|--------|--------|------|-----------|--------|
| 01 | Workshop Core | `feat: Add Playlist Workshop with drag-and-drop and shuffle preview` | High | Medium | Low | 3-5 | ✅ COMPLETED (PR #43) |
| 02 | Track Management | `feat: Add track search and delete to Workshop` | High | Low-Med | Low | 2-3 | ✅ COMPLETED (PR #44) |
| 03 | Playlist Merging | `feat: Add source playlist panel for cross-playlist merging` | High | Medium | Low | 3-4 | ✅ COMPLETED (PR #45) |
| 04 | External Raiding | `feat: Add external playlist loading via URL and search` | High | Medium | Med | 3-4 | ✅ COMPLETED (PR #46) |
| 05 | User Database | `feat: Add SQLite persistence with user/session/source models` | Medium | High | Med | 4-6 | ✅ COMPLETED (PR #47) |
| 06 | Scheduled Ops | `feat: Add APScheduler for automated playlist operations` | Medium | High | High | 5-7 | ✅ COMPLETED (PR #48) |

**Total estimated effort:** 20-29 engineering days

---

## Dependency Graph

```
Phase 1: Workshop Core
    ├── Phase 2: Track Management (builds on workshop UI)
    ├── Phase 3: Playlist Merging (builds on workshop UI)
    │       └── Phase 4: External Raiding (extends source panel from Phase 3)
    └── Phase 5: User Database (persists workshop sessions)
                └── Phase 6: Scheduled Operations (requires DB for job configs + token storage)
```

### Sequential Dependencies (MUST complete in order)
- Phase 1 → Phase 2 (search/delete adds to workshop)
- Phase 1 → Phase 3 (source panel added to workshop layout)
- Phase 3 → Phase 4 (external sources extend source panel pattern)
- Phase 5 → Phase 6 (scheduler needs database for job persistence + refresh tokens)

### Parallel Opportunities (touch disjoint files)
- **Phase 2 and Phase 3** can run in parallel after Phase 1 merges:
  - Phase 2 modifies: `spotify/api.py` (search), workshop JS (delete buttons, search panel)
  - Phase 3 modifies: workshop template (source panel layout), routes (user-playlists endpoint)
  - Minimal overlap — coordinate on workshop.html layout only
- **Phase 5** can start in parallel with Phase 4 (Phase 5 touches `__init__.py`, `config.py`, new model files; Phase 4 touches `spotify/api.py`, `spotify/url_parser.py`, routes)

### Recommended Execution Order
1. Phase 1 (foundation — everyone depends on this)
2. Phase 2 + Phase 3 in parallel
3. Phase 4 (after Phase 3 merges)
4. Phase 5 (can overlap with Phase 4)
5. Phase 6 (after Phase 5 merges)

---

## Phase Docs

| File | Description |
|------|-------------|
| [`01_workshop-core.md`](./01_workshop-core.md) | Interactive track list with drag-and-drop, shuffle preview, commit-to-Spotify |
| [`02_track-management.md`](./02_track-management.md) | Spotify catalog search + track deletion within workshop |
| [`03_playlist-merging.md`](./03_playlist-merging.md) | Source panel for loading user's playlists, cherry-picking tracks, cross-list drag |
| [`04_external-playlist-raiding.md`](./04_external-playlist-raiding.md) | Load any public playlist by URL or search, extends source panel |
| [`05_user-database.md`](./05_user-database.md) | SQLite + Flask-SQLAlchemy for User, WorkshopSession, UpstreamSource persistence |
| [`06_scheduled-operations.md`](./06_scheduled-operations.md) | APScheduler for automated raid/shuffle operations on a configurable schedule |

---

## Architecture Impact

### New Files Created (across all phases)
```
shuffify/
├── templates/
│   ├── workshop.html           (Phase 1)
│   └── schedules.html          (Phase 6)
├── models/
│   └── db.py                   (Phase 5 — SQLAlchemy models)
├── services/
│   ├── search_service.py       (Phase 2)
│   ├── user_service.py         (Phase 5)
│   ├── workshop_session_service.py  (Phase 5)
│   ├── upstream_source_service.py   (Phase 5)
│   ├── scheduler_service.py    (Phase 6)
│   ├── job_executor_service.py (Phase 6)
│   └── token_service.py        (Phase 6)
├── spotify/
│   └── url_parser.py           (Phase 4)
├── schemas/
│   └── schedule_requests.py    (Phase 6)
└── scheduler.py                (Phase 6)

tests/
├── test_workshop.py            (Phase 1)
├── spotify/
│   ├── test_api_search.py      (Phase 2)
│   └── test_url_parser.py      (Phase 4)
├── services/
│   ├── test_search_service.py  (Phase 2)
│   ├── test_user_service.py    (Phase 5)
│   ├── test_workshop_session_service.py  (Phase 5)
│   ├── test_upstream_source_service.py   (Phase 5)
│   ├── test_scheduler_service.py  (Phase 6)
│   └── test_job_executor_service.py  (Phase 6)
└── models/
    └── test_db_models.py       (Phase 5)

migrations/                     (Phase 5 — Alembic)
```

### Key Modified Files
- `shuffify/routes.py` — Phases 1, 2, 3, 4, 5, 6 (new route sections)
- `shuffify/templates/dashboard.html` — Phases 1, 6 (Workshop button, Schedules link)
- `shuffify/spotify/api.py` — Phases 2, 4 (search_tracks, search_playlists methods)
- `shuffify/spotify/cache.py` — Phases 2, 4 (search result caching)
- `shuffify/schemas/requests.py` — Phase 1 (WorkshopCommitRequest)
- `shuffify/__init__.py` — Phase 5 (SQLAlchemy init)
- `config.py` — Phases 5, 6 (DATABASE_URL, scheduler settings)
- `requirements/base.txt` — Phases 5, 6 (Flask-SQLAlchemy, Flask-Migrate, APScheduler)

### New Dependencies
| Phase | Package | Purpose |
|-------|---------|---------|
| 1 | SortableJS (CDN) | Drag-and-drop track reordering |
| 5 | Flask-SQLAlchemy | ORM for SQLite database |
| 5 | Flask-Migrate | Alembic-based schema migrations |
| 6 | APScheduler | Background job scheduler |
| 6 | cryptography (Fernet) | Refresh token encryption |

---

## Key Design Decisions

1. **Workshop is a staging area** — All changes accumulate client-side in JavaScript. Nothing touches Spotify until the user clicks "Save to Spotify." This enables preview-before-commit for all operations.

2. **SortableJS via CDN** — No build process needed. 28KB library with mobile touch support, matching the existing Tailwind CDN pattern.

3. **SQLite over PostgreSQL** — 25-user cap makes SQLite the pragmatic choice. Single file, zero config, sufficient performance. SQLAlchemy abstracts the dialect for future migration if needed.

4. **APScheduler over Celery** — In-process scheduler is sufficient for 25 users with max 5 jobs each (125 total). Avoids the operational complexity of a separate worker process + message broker.

5. **Refresh token storage for background jobs** — Phase 6 requires Fernet-encrypted refresh tokens in the database so scheduled jobs can obtain fresh access tokens without user interaction.

6. **Graceful degradation** — Database features (Phases 5-6) are additive. Core shuffle/undo features continue to work without the database. If SQLite is unavailable, only persistence features return errors.

---

## Remaining Work & Future Ideas

These came up during design but are explicitly out of scope for this session:

- **Virtual scrolling** for 1000+ track playlists (performance optimization)
- **Collaborative workshop** — share a staging URL with another user
- **Playlist templates** — save a track arrangement as a reusable template
- **Algorithm A/B comparison** — preview multiple algorithms side-by-side
- **localStorage backup** — persist unsaved workshop state across page refreshes
- **Webhook notifications** — notify users when scheduled jobs complete (email/Slack)
- **Timezone-aware scheduling** — store user timezone preference for schedule display
