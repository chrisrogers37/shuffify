# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Job Executor Service** - Split monolithic 969-line `job_executor_service.py` into focused `executors/` package
  - `base_executor.py`: Lifecycle, token management, dispatch, shared utilities
  - `raid_executor.py`: Raid-specific operations
  - `shuffle_executor.py`: Shuffle-specific operations with extracted auto-snapshot
  - `rotate_executor.py`: Rotation modes and pairing logic
  - Public API unchanged: `JobExecutorService.execute()` and `JobExecutorService.execute_now()`
- **Route Auth Standardization** - Migrated 16 routes across playlists, shuffle, workshop, and settings to use `@require_auth_and_db` decorator
  - Eliminates manual `require_auth()` + None check boilerplate
  - Ensures consistent 401/503 error responses across all API routes
- **JSON Validation Helper** - Added `validate_json()` helper to standardize Pydantic validation across 11 routes
  - Consistent error message format: `"Validation error: {message}"`
  - Replaces 4 different validation patterns (error_count, errors[0], bare Exception, no handling)
- **Template Decomposition** - Extracted reusable Jinja2 macros for glass cards, form fields, and empty states
  - Created `shuffify/templates/macros/cards.html`, `forms.html`, and `states.html`
  - Applied macros to `settings.html`, `dashboard.html`, and `schedules.html`
  - Extracted shared `showNotification()` function to `static/js/notifications.js`
- **Spotify API Error Handling** - Extracted ~200 lines of error handling code from `api.py` into new `error_handling.py` module
  - Moved retry constants, backoff calculation, error classification, and `api_error_handler` decorator
  - No behavior changes; pure structural extraction for improved readability
- **Sentry SDK** - Updated sentry-sdk pin from 1.45.1 to 2.x (>=2.53.0,<3.0) in production requirements
- **DB Commit Standardization** - Replaced 11 manual commit patterns across 6 service files with the shared `safe_commit()` helper for consistent error handling, rollback, and logging
- **Landing Page Dark Theme** - Complete visual overhaul of the landing page with dark background (`#0a0a0f`), neon green accents, and typographic wordmark
  - Replaced green gradient wall with dark foundation (`dark-base`, `dark-surface`, `dark-card` color tokens)
  - Replaced emoji `🎵 Shuffify` with split-color typographic wordmark (white "Shuff" + green "ify" with neon text glow)
  - Restyled all cards from `bg-white/10` to `bg-dark-card` with neon green hover glow effects
  - Converted dev mode banner from large yellow card to slim amber top bar
  - Updated hero pattern SVG from white to green-tinted strokes
  - Added neon glow CSS utilities (`neon-glow-sm`, `neon-glow`, `neon-glow-lg`, `neon-text-glow`)
  - Section alternation using `dark-base`/`dark-surface` for visual separation
  - Step number circles now neon green with dark text (was white with green text)
- **Animated Playlist Shuffle Hero** - Added animated playlist visualization to the landing page hero section
  - Two-column hero layout: text content left, animated playlist card right (stacks on mobile)
  - Mock playlist card with 8 tracks that continuously shuffle with smooth CSS transitions
  - Card border glow pulse, staggered track movement, directional easing (bounce down, smooth up)
  - Respects `prefers-reduced-motion`: renders static tracks when reduced motion is preferred
  - Pauses animation when tab is hidden to save CPU
  - Form left-aligned on desktop (`lg:mx-0`), centered on mobile
- **Landing Page Section Redesign** - Redesigned all below-hero sections with glassmorphism cards, timeline, and scroll-triggered animations
  - How It Works: replaced numbered circles with SVG icon circles connected by a neon gradient timeline
  - Perfect For / Features: replaced all emoji with inline SVG icons, glassmorphism card treatments (`bg-white/5 backdrop-blur-xl`)
  - Features expanded from 2 to 4 cards (7 Algorithms, Instant Undo, Playlist Workshop, Scheduled Shuffles)
  - Testimonial: added decorative quotation mark and neon green left accent bar
  - Trust Indicators: neon gradient divider, green icon glows, 2-column mobile layout
  - Scroll reveal animation system with staggered delays (CSS transitions + IntersectionObserver)
  - Updated consent card disclosure to accurately reflect data storage practices
- **Mobile Responsive + Performance Fixes** - Responsive layout, accessibility, and animation performance improvements
  - Responsive title/subtitle/description sizing (`text-4xl md:text-5xl lg:text-6xl` etc.)
  - Mobile card padding reduction (`p-4 md:p-6`) across all glassmorphism cards
  - Viewport-based playlist animation: 5 tracks on mobile vs 8 on desktop, longer shuffle intervals
  - Mobile-optimized track item sizing (smaller art, font, and padding below 768px)
  - Consent checkbox wrapped in `<label>` with 44px minimum touch target
  - `prefers-reduced-motion` CSS media query disables all animations and transitions globally
  - GPU acceleration hints (`will-change`) with automatic cleanup after scroll-reveal completes
  - Compositor-safe hover effects: `.cta-button`, `.glass-card`, `.step-circle` box-shadow animations replaced with pseudo-element opacity technique
  - IntersectionObserver `unobserve()` cleanup in base.html to free memory after animation

### Fixed
- **Shuffle/Save "unexpected error" on production** - Registered full `SpotifyError` exception hierarchy in global error handlers
  - `SpotifyAPIError`, `SpotifyTokenExpiredError`, `SpotifyRateLimitError`, `SpotifyNotFoundError`, `SpotifyAuthError` now return proper user-facing messages instead of generic "An unexpected error occurred"
- **Silent playlist update failures** - Removed error-swallowing `try/except` in `SpotifyClient.update_playlist_tracks()` that caught `SpotifyAPIError` and returned `False`, masking the real error from the service layer
- **Blank algorithm labels in dropdowns** - Added global CSS rule for `select option` elements to fix white-on-white text in native OS dropdowns (Windows Chrome/Edge)

### Security
- **pip CVE remediation** - Upgraded minimum pip to >=26.0 in Dockerfile (CVE-2025-8869, CVE-2026-1703)
- **HSTS Header** - Production responses now include `Strict-Transport-Security: max-age=31536000; includeSubDomains`
  - Only applied when `DEBUG = False`; development on HTTP is unaffected
- **Security Response Headers** - All responses now include `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`
- **Health Endpoint Hardened** - `/health` no longer exposes which subsystem is degraded
  - Returns only `{"status": "healthy"}` or `{"status": "degraded"}` without `checks` object
- **Credential Rotation** - Rotated all exposed credentials (Spotify API, Neon DB, Flask SECRET_KEY) found in git history
- **Git History Cleanup** - Removed `.env` from git history using `git filter-repo`
- **Credential Rotation Guide** - Added `documentation/guides/credential-rotation.md` documenting rotation procedures, side effects, and verification steps
- **Auth Endpoint Rate Limiting** - Added rate limiting to `/login` (10 req/min) and `/callback` (20 req/min)
  - Uses Flask-Limiter with Redis storage backend (in-memory fallback)
  - Returns standard JSON error response on 429 Too Many Requests
  - Added `Flask-Limiter>=3.5.0` dependency
- **Test Database Isolation** - Test fixtures now clear `DATABASE_URL` before app creation to prevent tests from hitting production databases
- **Dependency CVE Remediation** - Updated production dependencies with known vulnerabilities
  - `cryptography` >=43.0.1 to >=46.0.5 (CVE-2024-12797, CVE-2026-26007)
  - Added floor pins for transitive deps: `werkzeug>=3.1.5`, `urllib3>=2.6.3`, `marshmallow>=3.26.2,<4.0`, `pyasn1>=0.6.2`
  - Added `wheel>=0.46.2` to dev dependencies (CVE-2026-24049)

### Changed
- **Batch Dependency Updates** - Updated 5 development and production dependencies
  - `gunicorn` 25.0.3 → 25.1.0 (control interface, ASGI stable)
  - `flake8` 6.1.0 → 7.3.0 (Python 3.14 support, new pyflakes/pycodestyle)
  - `pytest` 8.3.5 → 9.0.2 (terminal progress, CLI reference docs)
  - `isort` 5.13.2 → 7.0.0 (Python 3.14 support, noqa fix)
  - `ipython` 8.38.0 → 9.10.0 (autoreload debug, history fixes)
- **Schema Tests, Dead Code & Dependency Updates** - Final tech debt cleanup phase
  - Added 37 new schema validation tests: `test_settings_requests.py` (21 tests), `test_snapshot_requests.py` (16 tests)
  - Removed dead `count_sources_for_target()` method from `upstream_source_service.py`
  - Updated dependencies: Flask >=3.1.3, gunicorn 25.0.3, pytest-cov 7.0.0
  - Added transitive floor pins: certifi >=2026.1.4, MarkupSafe >=3.0.3, PyYAML >=6.0.3
- **Route Infrastructure Cleanup** - Extracted `@require_auth_and_db` decorator and `log_activity()` helper to reduce auth/DB/logging boilerplate across 10 route modules
- **Service Layer Deduplication** - Extracted shared CRUD utilities to `shuffify/services/base.py`
  - `safe_commit()` wraps db commit/rollback/log pattern (replaced 9 occurrences across 5 services)
  - `get_user_or_raise()` standardizes User lookup by spotify_id (replaced 8 occurrences across 2 services)
  - `get_owned_entity()` standardizes entity fetch + ownership check (replaced 3 occurrences across 3 services)
- **Missing Route Tests** - Added 68 new route-level tests covering 5 previously untested route modules
  - `tests/routes/test_core_routes.py` (14 tests): /, /login, /callback, /logout, /terms, /privacy
  - `tests/routes/test_playlists_routes.py` (12 tests): refresh-playlists, get-playlist, get-stats, user-playlists API
  - `tests/routes/test_shuffle_routes.py` (6 tests): shuffle execution, undo with state revert
  - `tests/routes/test_upstream_sources_routes.py` (13 tests): upstream source CRUD with `@require_auth_and_db`
  - `tests/routes/test_schedules_routes.py` (23 tests): schedule CRUD, toggle, manual run, history, rotation status
- **Complex Function Decomposition** - Decomposed 7 overly complex functions into focused private helpers across 4 files
  - `job_executor_service.py`: Split `execute()`, `_execute_raid()`, `_execute_rotate()` into 8 helper methods; centralized `_batch_add_tracks()` replaces 5 inline batch loops
  - `spotify/api.py`: Split 87-line `api_error_handler` decorator into `_classify_error()`, `_should_retry()`, `_get_retry_delay()`, `_raise_final_error()`
  - `routes/workshop.py`: Split `workshop_load_external_playlist()` and `workshop_commit()` into 4 focused helpers
  - `raid_sync_service.py`: Split `raid_now()` into `_execute_raid_via_scheduler()` and `_execute_raid_inline()`, reusing shared helpers from job_executor_service
- **Dev dependency updates** - Updated pytest 7.4.4 to 8.3.5, pytest-cov 4.1.0 to 6.0.0
  - Conservative upgrade within 8.x line to avoid pytest 9.0 breaking changes
- **Test isolation fix** - Guarded `load_dotenv()` in `config.py` to prevent `.env` from leaking production `DATABASE_URL` into test fixtures
  - Eliminates 124 test errors caused by attempted Neon PostgreSQL connections during tests

### Added
- **Dependabot configuration** - Automated weekly dependency monitoring via `.github/dependabot.yml`
  - Grouped updates for dev and production dependencies to reduce PR noise
- **Scheduled Rotation Job Type** - New `rotate` job type for automated track cycling between paired playlists
  - Three rotation modes: Archive Oldest, Refresh from Archive, and Swap
  - Configurable rotation count (tracks per execution, default 5)
  - Auto-snapshot before rotation with `AUTO_PRE_ROTATE` snapshot type
  - `playlist_remove_items()` added to Spotify API wrapper
  - Rotation status endpoint and workshop sidebar Schedules tab panel
  - Rotation mode selector in schedule creation modal
- **Smart Raid Panel** - One-click playlist watching and raid management in the workshop sidebar
  - "Watch Playlist" one-click operation: registers source + auto-creates raid schedule
  - Source management: view, add, and remove watched playlists with type badges
  - Schedule control: toggle raid schedule on/off, view last run status and interval
  - "Raid Now" button for immediate on-demand track sync
  - New `RaidSyncService` orchestration layer composing UpstreamSourceService + SchedulerService
  - 5 REST endpoints for raid status, watch/unwatch, raid-now, and schedule toggle
  - 3 new activity types: `RAID_WATCH_ADD`, `RAID_WATCH_REMOVE`, `RAID_SYNC_NOW`
- **Workshop Powertools Sidebar** - Collapsible tabbed sidebar on the workshop page
  - Four tab placeholders: Snapshots, Archive, Raids, Schedules
  - Smooth slide-in/out animation with localStorage state persistence
  - Responsive design: overlays on mobile with backdrop, side panel on desktop
  - Self-contained `workshopSidebar` JavaScript namespace (no impact on existing workshop JS)
  - Foundation for Phases 2-5 of the Workshop Powertools enhancement suite
- **Archive Playlist Pairing** - Link production playlists to archive companions for track removal recovery
  - New `PlaylistPair` database model with unique constraint per user+playlist
  - Alembic migration for `playlist_pairs` table
  - `PlaylistPairService` with CRUD, archive/unarchive, and batch Spotify API calls (100-track batches)
  - 6 REST endpoints for pair management, track archiving, and archive browsing
  - Pydantic validation schemas with cross-field mode validation (create-new vs. link-existing)
  - Workshop sidebar Archive tab with pair creation, archive track list, and restore buttons
  - Auto-queue removed tracks for archiving on commit (best-effort, non-blocking)
  - Archive queue cleared on undo to prevent archiving reverted removals
  - 4 new activity types: `ARCHIVE_TRACKS`, `UNARCHIVE_TRACKS`, `PAIR_CREATE`, `PAIR_DELETE`
- **Snapshot Browser Panel** - Workshop sidebar panel for browsing, creating, restoring, and deleting playlist snapshots
  - Chronological timeline with color-coded type badges (manual, pre-shuffle, pre-raid, pre-commit, scheduled)
  - Manual snapshot creation with optional description
  - Restore confirmation modal with track count diff preview
  - Delete confirmation modal with safety prompt
  - Auto-refresh after snapshot operations and playlist commits
  - Empty state, loading state, and error state handling
- **Personalized Dashboard** - Dashboard now shows personalized welcome messaging, quick stats, and recent activity
  - New `DashboardService` aggregates activity, stats, and schedule data into a single dashboard payload
  - "Welcome back" messaging distinguishes returning users from first-time visitors
  - Quick stats cards show total shuffles, active schedules, scheduled runs, and snapshots saved
  - Collapsible activity feed shows recent actions and "since your last visit" summary
  - Recent scheduled job execution results displayed in activity section
  - Onboarding hint for new users with no activity
  - All dashboard data is non-blocking: failures degrade gracefully to the existing playlist grid
- **Activity Log** - Unified audit trail for all user actions
  - New `ActivityLog` model with user_id, activity_type, description, playlist context, and JSON metadata
  - New `ActivityLogService` with `log()`, `get_recent()`, `get_activity_since()`, and `get_activity_summary()` methods
  - New `ActivityType` enum with 17 activity types covering shuffles, workshop, schedules, snapshots, and auth
  - Non-blocking activity logging hooked into shuffle, workshop commit, workshop sessions, upstream sources, schedule CRUD, job execution, login, and logout
  - Composite index on (user_id, created_at) for efficient recent activity queries
- **Playlist Snapshots** - Persistent point-in-time capture of playlist track orderings
  - New `PlaylistSnapshot` database model with automatic retention management
  - Auto-snapshot before shuffle, workshop commit, and scheduled job execution
  - Manual snapshot creation via API endpoint
  - Snapshot restoration with pre-restore auto-snapshot for undo safety
  - Snapshot listing, viewing, and deletion with ownership checks
  - `SnapshotType` enum for categorizing snapshot triggers
  - Respects `UserSettings.auto_snapshot_enabled` and `max_snapshots_per_playlist`
  - New API endpoints: `GET/POST /playlist/<id>/snapshots`, `GET/DELETE /snapshots/<id>`, `POST /snapshots/<id>/restore`
  - Alembic migration for `playlist_snapshots` table with composite index
- **User Settings** - Persistent user preferences with settings page
  - New `UserSettings` model with default algorithm, theme, snapshot, and notification preferences
  - Settings page accessible from dashboard with gear icon
  - Auto-creates default settings for new users on first login
  - Extensible `extra` JSON field for future preferences
  - Pydantic validation for settings updates
  - Full test coverage for service and routes
- **Login History Tracking** - New `LoginHistory` model records every sign-in event
  - Captures IP address, user agent, session ID, and login type
  - `LoginHistoryService` with `record_login()`, `record_logout()`, `get_recent_logins()`, and `get_login_stats()`
  - Login events recorded automatically during OAuth callback
  - Logout timestamps recorded during explicit logout
  - Cascade delete ensures login history is removed when user is deleted
- **PostgreSQL Support** - Production database support for Neon and Railway
  - Added `psycopg2-binary` (development) and `psycopg2` (production) drivers
  - Automatic `postgres://` to `postgresql://` URL conversion for managed providers
  - SSL and connection pooling configuration for Neon/Railway
- **Alembic Migrations** - Database schema management via Flask-Migrate
  - Initial migration capturing all 5 tables
  - Automatic migration execution on app startup (non-test environments)
  - `db.create_all()` preserved for test fixtures using in-memory SQLite
- **Database Health Check** - Enhanced `/health` endpoint reports database connectivity
- **Docker PostgreSQL** - Added PostgreSQL service to `docker-compose.yml`
- **User Dimension Table Enhancement** - Enriched User model with login tracking and Spotify profile fields
  - New fields: `last_login_at`, `login_count`, `is_active`, `country`, `spotify_product`, `spotify_uri`
  - `upsert_from_spotify()` now returns `UpsertResult` with `is_new` flag for create/update distinction
  - Login count auto-increments on each OAuth login
  - `is_new_user` flag stored in Flask session for future onboarding flows
  - Alembic migration for schema changes

### Fixed
- **Dockerfile Python Version** - Upgraded base image from `python:3.10-slim` to `python:3.12-slim`
  - Fixes `ImportError: cannot import name 'StrEnum' from 'enum'` crash on startup (StrEnum requires Python 3.11+)

### Documentation
- **Production Database Setup Guide** - New guide at `documentation/production-database-setup.md`
  - Setup instructions for Neon, Railway, Docker Compose, and generic PostgreSQL
  - Required environment variables, verification steps, migration details, and troubleshooting

### Changed
- **Database Config** - `config.py` uses `_resolve_database_url()` helper for DATABASE_URL resolution
- **App Factory** - Uses Alembic `upgrade()` instead of `db.create_all()` for non-test environments
- **Dockerfile** - Added `libpq-dev`, `--preload` for gunicorn, updated HEALTHCHECK to `/health`
- **Routes Split into Modules** - Split monolithic `routes.py` (1509 lines) into `routes/` package
  - 6 feature modules: core, playlists, shuffle, workshop, upstream_sources, schedules
  - Single Blueprint preserved — zero template changes needed
- **Dependency Updates** - Updated pinned dependencies to latest within-major versions
  - base: spotipy 2.25.1→2.25.2, python-dotenv 1.1.1→1.2.1
  - dev: pytest 7.4.0→7.4.4, pytest-mock 3.11.1→3.15.1, isort 5.12.0→5.13.2, mypy 1.5.1→1.19.1, bandit 1.7.5→1.9.3, ipython 8.14.0→8.38.0
  - prod: sentry-sdk 1.29.2→1.45.1

### Added
- **Test Coverage for Untested Modules** - 80 new tests for 3 previously untested modules
  - `schedule_requests.py`: 30 tests covering create/update validation, edge cases
  - `scheduler.py`: 27 tests covering schedule parsing, init, job management, events
  - `shuffle_algorithms/utils.py`: 23 tests covering utility functions
  - Total test count: 690 → 770
- **Environment Example File** - Added `.env.example` with all required/optional configuration variables
- **Schedule Enums** - Extracted hardcoded string literals to `StrEnum` classes
  - `JobType`, `ScheduleType`, `IntervalValue` in `shuffify/enums.py`
  - Single source of truth across schemas, models, services, and scheduler
- **Tech Debt Remediation Plans** - Post-Workshop cleanup planning documentation
  - Master inventory of 8 tech debt items with severity scoring and dependency matrix
  - Phase 01: Missing test coverage for schedule schemas, scheduler, algorithm utils
  - Phase 02: Extract job type and schedule value string literals to enums
  - Phase 03: Split monolithic routes.py (1509 lines) into feature-based Blueprint modules
  - Phase 04: Dependency updates and .env.example creation
- **Scheduled Operations** - Automated playlist management via APScheduler
  - Configure recurring raid, shuffle, or combined operations
  - Background scheduler runs jobs without user interaction
  - Encrypted refresh token storage (Fernet) for secure background API access
  - New `/schedules` page for managing scheduled operations
  - Create, edit, toggle, delete, and manually trigger schedules
  - Execution history tracking per schedule
  - Max 5 schedules per user
  - Graceful handling of expired tokens, deleted playlists, rate limits
  - New models: `Schedule`, `JobExecution` (SQLite via Flask-SQLAlchemy)
  - New services: `TokenService`, `SchedulerService`, `JobExecutorService`
  - New routes: `GET /schedules`, `POST /schedules/create`, `PUT /schedules/<id>`,
    `DELETE /schedules/<id>`, `POST /schedules/<id>/toggle`,
    `POST /schedules/<id>/run`, `GET /schedules/<id>/history`
  - Pydantic validation: `ScheduleCreateRequest`, `ScheduleUpdateRequest`
  - Dashboard header now includes "Schedules" navigation link
  - New dependency: `APScheduler>=3.10`

### Changed
- **Algorithm validation uses registry as single source of truth** - Removed hardcoded `VALID_ALGORITHMS` set from `ShuffleService` and hardcoded algorithm list from `ShuffleRequest` validator; both now query the registry dynamically
- **Algorithm params mapping is declarative** - Replaced 8-branch `elif` chain in `get_algorithm_params()` with a dict-based mapping
- **SpotifyClient exposes skip_cache parameter** - Added `skip_cache` to `SpotifyClient.get_user_playlists()` facade; `PlaylistService` no longer reaches through to private `_api` attribute
- **Shared shuffle algorithm utilities** - Extracted `extract_uris`, `split_keep_first`, and `split_into_sections` into `shuffle_algorithms/utils.py`; 5 algorithm files now use shared functions instead of duplicated code

### Removed
- **Duplicate algorithm registration** - Removed redundant `ShuffleRegistry.register()` calls that duplicated the `_algorithms` class dict
- **Template debug artifacts** - Removed debug comments, commented-out debug div, orphaned JS debug references, and verbose `console.log` state tracing from `base.html` and `dashboard.html`
- **Unused Playlist methods** - Removed `get_track()`, `get_track_with_features()`, and `get_tracks_with_features()` from Playlist model (no callers in codebase)
- **Duplicate TTL class constants** - Removed `DEFAULT_TTL`, `PLAYLIST_TTL`, `USER_TTL`, `AUDIO_FEATURES_TTL` from `SpotifyCache`; `config.py` is the single source of truth

### Added
- **Playlist Workshop** - Dedicated `/workshop/<playlist_id>` page for interactive track management
  - Visual track list with album art thumbnails, artist names, and duration
  - Drag-and-drop reordering via SortableJS
  - Shuffle preview runs algorithm on client-provided tracks without any Spotify API call
  - "Save to Spotify" button commits staged changes with state tracking for undo
  - "Undo Changes" reverts to last saved order before committing
  - Dashboard playlist cards now include "Workshop" button for quick access
- **Track Management in Workshop** - Add and remove tracks within the Playlist Workshop
  - Delete button (X) on each track row to remove from working copy
  - Search Spotify panel in workshop sidebar to find new tracks
  - Add button (+) on search results to append track to working playlist
  - Search results cached in Redis for 120 seconds to reduce API calls
  - New `POST /workshop/search` endpoint with Pydantic validation
  - New `SpotifyAPI.search_tracks()` method wrapping spotipy search
  - New `SpotifyCache` search result caching (get/set with query normalization)
  - All changes are client-side staging until "Save to Spotify" is clicked
- **Source Playlists Panel** - Collapsible panel in Workshop for cross-playlist track merging
  - Dropdown to select from user's editable playlists (excluding current)
  - Browse source playlist tracks with album art, artist names, and duration
  - Click "+" button to cherry-pick individual tracks into the working playlist
  - Drag tracks from source panel directly into the main track list (SortableJS cross-list groups)
  - Visual duplicate detection: yellow warning icon on tracks already in the working playlist
  - Confirm dialog before adding duplicate tracks
  - New API endpoint: `GET /api/user-playlists` returns lightweight playlist list for AJAX consumers
  - New routes: `GET /workshop/<id>`, `POST /workshop/<id>/preview-shuffle`, `POST /workshop/<id>/commit`
  - Pydantic validation for commit request (`WorkshopCommitRequest` schema)
- **External Playlist Raiding** - Load any public Spotify playlist in the Workshop source panel
  - Paste a Spotify playlist URL, URI, or bare ID to load tracks instantly
  - Search for playlists by name using Spotify's search API
  - Reuses Phase 3's source panel for cherry-pick/drag-to-add UX
  - Session-based "Recently Loaded" history (up to 10 playlists)
  - New utility: `shuffify/spotify/url_parser.py` for parsing Spotify URL formats
  - New API method: `SpotifyAPI.search_playlists()` with Redis caching
  - New routes: `POST /workshop/load-external-playlist`, `POST /workshop/search-playlists`
  - Pydantic validation for external playlist requests (`ExternalPlaylistRequest` schema)
- **User Database & Persistence** - SQLite database with Flask-SQLAlchemy for persistent storage
  - User model: stores Spotify user ID, display name, email, profile image; auto-upserted on login
  - WorkshopSession model: save/load named workshop arrangements across browser sessions
  - UpstreamSource model: persist external playlist source configurations for scheduled operations
  - New routes: workshop session CRUD (`/workshop/<id>/sessions`), upstream source CRUD
  - Flask-Migrate (Alembic) integration for database schema versioning
  - Graceful degradation: core shuffle/undo features work without database; only persistence returns errors
  - New services: UserService, WorkshopSessionService, UpstreamSourceService
  - New dependencies: Flask-SQLAlchemy>=3.1.0, Flask-Migrate>=4.0.0
- **Error handler test coverage** - 14 new tests verifying all service exception handlers return correct HTTP status codes, JSON structure, and error categories
- **Playlist model test coverage** - 20 new tests covering construction, validation, track operations, feature statistics, and serialization
- **Refresh Playlists Button** - Re-fetch playlists from Spotify without losing undo state
  - New `POST /refresh-playlists` endpoint with Redis cache bypass
  - Dashboard UI button with spinning icon animation during refresh
  - Preserves `session['playlist_states']` (undo/redo history) across refreshes
- **Artist Spacing Algorithm** - New shuffle algorithm that prevents same artist appearing back-to-back
  - Configurable `min_spacing` parameter (1-10 tracks between same artist)
  - Uses max-heap priority scheduling to optimally distribute artists
  - Graceful fallback when perfect spacing is impossible (e.g., one dominant artist)
  - 19 new tests covering spacing enforcement, edge cases, and fallback behavior
- **Album Sequence Algorithm** - Shuffle album order while keeping album tracks together
  - Preserves internal track order within each album by default
  - Optional `shuffle_within_albums` parameter to also randomize intra-album order
  - Useful for listening to full albums in random order
  - 22 new tests covering album grouping, internal ordering, and edge cases
- **Tempo Gradient Algorithm** - Sort tracks by BPM for DJ-style transitions (hidden)
  - Supports ascending (building energy) and descending (winding down) directions
  - Requires Spotify Audio Features API (deprecated Nov 2024, hidden from UI)
  - Code ready to unhide when extended API access is granted
  - 21 new tests covering tempo sorting, partial features, and edge cases
- **Redis Session Storage** - Migrated from filesystem to Redis-based sessions
  - Added `redis>=5.0.0` dependency for session and caching support
  - Configurable via `REDIS_URL` environment variable
  - Automatic fallback to filesystem sessions if Redis unavailable
  - Session keys prefixed with `shuffify:session:` for namespacing
  - Graceful degradation with logging when Redis connection fails
- **Redis API Caching** - Implemented caching layer for Spotify API responses
  - `SpotifyCache` class in `shuffify/spotify/cache.py` for centralized cache management
  - Configurable TTLs per data type: playlists (60s), user data (600s), audio features (24h)
  - Automatic cache invalidation after playlist modifications
  - `skip_cache` parameter for bypassing cache when fresh data needed
  - Partial cache support for audio features (fetch only uncached tracks)
  - 45 new tests for cache functionality and API caching integration
- **Flask 3.x Upgrade** - Upgraded from Flask 2.3.3 to Flask 3.1.x
  - Updated Flask-Session from 0.5.0 to 0.8.0 for Flask 3.x compatibility
  - Replaced deprecated `FLASK_ENV` config with `CONFIG_NAME` (removed in Flask 3.0)
  - Updated `datetime.utcnow()` to `datetime.now(timezone.utc)` (deprecated in Python 3.12)
  - All 303 tests passing with Flask 3.x
- **Spotify API Retry Logic** - Added exponential backoff for transient errors
  - Automatic retry on rate limits (429) with Retry-After header support
  - Automatic retry on server errors (500, 502, 503, 504)
  - Automatic retry on network errors (ConnectionError, Timeout)
  - Configurable max retries (4) with exponential backoff (2s, 4s, 8s, 16s)
  - 12 new unit tests covering all retry scenarios

### Changed
- **ShuffleRegistry** - Extended to support 7 algorithms (6 visible, 1 hidden)
  - `_hidden_algorithms` mechanism hides Tempo Gradient pending Spotify API access
  - New algorithms registered with defined display order in UI
- **Pydantic Schemas** - Updated to validate new algorithm parameters
  - Added `min_spacing`, `shuffle_within_albums`, `direction` fields
  - Extended `validate_algorithm_name` and `get_algorithm_params` for 7 algorithms
- **PlaylistService** - Added `skip_cache` parameter to `get_user_playlists()`
- **SpotifyAPI** - Now supports optional caching via `cache` parameter
  - Methods accept `skip_cache` parameter to bypass cache when needed
  - Cache automatically invalidated after playlist updates
- **SpotifyClient** - Updated to pass cache to internal SpotifyAPI
  - Optional `cache` parameter in constructor for caching support
- **App Factory** - Enhanced with Redis initialization and helper functions
  - `get_redis_client()` to access the global Redis connection
  - `get_spotify_cache()` to obtain configured SpotifyCache instance

### Security
- **Critical Dependency Updates** - Fixed multiple security vulnerabilities across dependencies
  - `cryptography` 41.0.7 → 46.0.4 (fixes CVE-2023-50782, CVE-2024-0727, PYSEC-2024-225, GHSA-h4gh-qq45-vh27)
  - `python-jose` 3.3.0 → 3.5.0 (fixes PYSEC-2024-232, PYSEC-2024-233)
  - `black` 23.7.0 → 26.1.0 (fixes PYSEC-2024-48)
  - `safety` 2.3.5 → 3.x (resolves dependency conflict, newer security scanning)
- **Docker Build Security** - Upgraded pip, setuptools, and wheel in Dockerfile
  - Addresses CVE-2025-8869 (pip), CVE-2024-6345 (setuptools), CVE-2026-24049 (wheel)
  - Build tools upgraded before installing project dependencies

### Added
- **Spotify Module Refactoring** (Phase 3) - Split SpotifyClient into modular components
  - `SpotifyCredentials` - Immutable dataclass for OAuth credentials, enabling dependency injection
  - `SpotifyAuthManager` - Dedicated class for OAuth flow, token exchange, and refresh
  - `SpotifyAPI` - Dedicated class for all Spotify Web API data operations
  - `TokenInfo` - Type-safe container for token data with validation and expiration checking
  - Comprehensive exception hierarchy (`SpotifyError`, `SpotifyAuthError`, `SpotifyTokenError`, etc.)
- **Comprehensive Test Suite** - 479 tests total, all passing
  - 32 new Spotify module tests (credentials, auth, API)
  - 99 new algorithm unit tests (BasicShuffle, BalancedShuffle, PercentageShuffle, StratifiedShuffle)
  - 12 integration tests covering full application flow
- **Pydantic Validation Layer** (Phase 2) - Type-safe request validation using Pydantic v2
  - `ShuffleRequest` schema with algorithm parameter validation
  - `PlaylistQueryParams` for query parameter validation
  - Algorithm-specific parameter schemas with constraints
  - `parse_shuffle_request()` utility for form data parsing
- **Global Error Handlers** - Centralized exception handling for all routes
  - Consistent JSON error responses across all endpoints
  - Handlers for `ValidationError`, `AuthenticationError`, `PlaylistError`, etc.
  - HTTP status code handlers (400, 401, 404, 500)

### Changed
- **SpotifyClient Refactored** - Now a facade that delegates to SpotifyAuthManager and SpotifyAPI
  - Maintains full backward compatibility with existing code
  - Hidden Flask dependency removed (credentials now explicitly passed)
  - Token refresh bug fixed (was using disabled cache_handler)
- **Routes Simplified** - Removed try/except boilerplate, delegating to global handlers
- **ShuffleService Refactored** - Removed manual parameter parsing (now handled by Pydantic)
- **Dependencies** - Added `pydantic>=2.0.0` to requirements

### Technical Improvements
- **Modularity Score** - Increased from 7.5/10 to 8.5/10 (Phase 3 completion)
- **Test Coverage** - 479 total tests (up from 139), all passing
- **Code Quality** - Clean separation of auth, API, and facade concerns
- **Dependency Injection** - SpotifyCredentials enables proper DI patterns
- **Token Management** - Proper token refresh with auto-refresh capability

---

## [Future]

### Planned Features
- ~~A "Refresh Playlists" button to re-fetch playlists from Spotify without losing the current undo/redo state.~~ (Completed)
- Implement Facebook and Apple authentication flows to provide more login options.
- **Tempo Gradient Algorithm** - Unhide when Spotify Audio Features API access is restored

### Planned Infrastructure Improvements
- ~~**Session Security**: Migration from filesystem sessions to Redis or database-backed sessions~~ (Completed)
- ~~**Caching Strategy**: Implement Redis caching for Spotify API responses~~ (Completed)
- **CI/CD Pipeline**: Automated testing and deployment pipeline
- **Database Integration**: Lightweight database for user preferences and analytics

### [2.3.6] - 2025-08-31

#### Fixed
- **Facebook OAuth Support**: Resolved critical issue preventing Facebook-authenticated Spotify accounts from logging in
  - Enhanced OAuth error handling to detect and report authentication failures
  - Added comprehensive token validation to prevent crashes from malformed tokens
  - Improved session management with proper cleanup on authentication errors
  - Removed invalid `user-read-birthdate` scope that was causing "illegal scope" errors
  - Added detailed logging throughout the OAuth flow for better debugging

#### Changed
- **Landing Page Updates**: Updated to reflect development mode status
  - Added prominent development mode notice with contact information for user whitelisting
  - Removed misleading "Trusted by Music Lovers" social proof section
  - Updated testimonial section to "Why I Built This" with more authentic messaging
  - Changed "Enjoy" step to "Reorder" in "How It Works" section for clarity
  - Updated testimonial text to better reflect the app's purpose
  - Improved layout with proper right-alignment for testimonial attribution

#### Added
- **Enhanced Error Handling**: Comprehensive OAuth error detection and user-friendly error messages
- **Development Mode Communication**: Clear messaging about app status and whitelisting requirements
- **Improved Logging**: Detailed debug information for OAuth troubleshooting

#### Removed
- **Development Tools**: Removed `/debug/oauth` endpoint and `test_oauth.py` script (development-only tools)
- **Invalid OAuth Scope**: Removed `user-read-birthdate` scope that was causing authentication failures
- **Misleading Content**: Removed fake social proof metrics and inappropriate testimonials

#### Technical Improvements
- **Session Configuration**: Updated session security settings for better OAuth compatibility
- **Token Validation**: Added robust token structure validation before API calls
- **Error Recovery**: Improved session cleanup on authentication failures

### [2.3.5] - 2025-08-31

#### Added
- **Comprehensive UX Review**: Complete frontend landing page analysis and renovation plan
- **Enhanced Legal Consent**: Redesigned consent form with "Quick & Secure" messaging and improved visual appeal
- **Dynamic CTA Button**: Progressive enhancement with dynamic subtext that changes based on consent checkbox state
- **Social Proof Section**: Added "Trusted by Music Lovers" with realistic stats (1K+ playlists, 100+ users) and user testimonial
- **How It Works Section**: Clear 3-step process explanation (Connect, Choose, Enjoy) positioned for optimal user flow
- **Use Cases Section**: Four targeted cards for different user types (Curated Collections, Tastemaker Playlists, New Perspectives, Playlist Maintenance)
- **Trust Indicators**: Added security and privacy badges (Secure OAuth, No Data Stored, Instant Results, Free Forever)
- **User Testimonial**: Added specific testimonial from playlist curator with 5-star rating
- **Logout Functionality**: Added logout button to dashboard for better session management and user switching
- **Accessibility Improvements**: Added skip links, ARIA labels, focus states, and screen reader support
- **Scroll Animations**: Added intersection observer for smooth scroll-triggered animations
- **Custom Scrollbar**: Enhanced scrollbar styling for better visual consistency

#### Changed
- **Hero Section Copy**: Updated to target "tastemakers" and "reorder carefully curated Spotify playlists" instead of generic shuffling
- **CTA Button Text**: Changed from "Connect with Spotify" to "Start Reordering Now" with dynamic subtext
- **Section Ordering**: Moved "How It Works" above "Trusted by Music Lovers" for better information architecture
- **Spacing Optimization**: Reduced excessive padding between sections for better visual flow and scrolling experience
- **Feature Descriptions**: Updated to emphasize "reordering" instead of "shuffling" for clarity
- **Color Scheme**: Enhanced legal links with Spotify green color for better brand consistency
- **Responsive Design**: Improved spacing and layout across all screen sizes

#### Fixed
- **Legal Consent UX**: Transformed required consent from friction point to positive security feature
- **Information Architecture**: Reordered sections for more logical user journey
- **Visual Hierarchy**: Improved spacing and typography for better content digestion
- **Mobile Experience**: Optimized touch targets and spacing for mobile devices
- **Session Management**: Added proper logout functionality for user switching

#### Technical Improvements
- **Tailwind Config**: Extended with custom animations (fade-in, slide-up, scale-in)
- **Global CSS**: Added accessibility styles, focus states, and custom scrollbar
- **JavaScript**: Added dynamic CTA updates and scroll-triggered animations
- **Template Structure**: Improved semantic HTML with proper ARIA labels and skip links

### [2.3.4] - 2025-08-31

#### Security
- **Environment variable validation**: Added fail-fast validation for required environment variables in production
- **Dependency security updates**: Updated non-breaking packages to latest versions for security improvements
- **Security scanning tools**: Added safety and bandit to development environment for vulnerability detection

#### Added
- **Environment validation**: Added startup validation for SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET
- **Development security tools**: Added safety==2.3.5 and bandit==1.7.5 for automated security scanning
- **Improved error handling**: Better logging and error messages for missing environment variables

#### Changed
- **Package updates**: Updated spotipy (2.23.0 → 2.25.1), requests (2.31.0 → 2.32.5), python-dotenv (1.0.0 → 1.1.1), gunicorn (21.2.0 → 23.0.0), numpy (1.24.3 → >=1.26.0)
- **Conservative approach**: Kept Flask 2.3.3 for compatibility while updating other packages
- **Production safety**: Application now fails fast in production with missing environment variables

#### Notes
- **Flask 3.x consideration**: Flask was kept at 2.3.3 for compatibility. A future major version upgrade to Flask 3.x is planned with comprehensive testing and migration guide.

### [2.3.3] - 2025-08-31

#### Security
- **Fixed critical security vulnerability**: Changed session directory permissions from `chmod 777` to `chmod 755` with proper ownership (`nobody:nogroup`)
- **Added Docker security hardening**: Implemented proper file permissions and ownership for production containers

#### Added
- **Health check endpoint**: Added `/health` route returning JSON status for monitoring and container orchestration
- **Docker health checks**: Added `HEALTHCHECK` directive with curl-based monitoring
- **Container monitoring**: Added curl dependency for health check functionality

#### Fixed
- **Tailwind CSS configuration**: Fixed content paths from `./app/templates/**/*.html` to `./shuffify/templates/**/*.html` for proper asset optimization
- **Build optimization**: Ensured Tailwind classes are properly purged in production builds

#### Changed
- **Dockerfile improvements**: Enhanced container security and operational readiness
- **Infrastructure documentation**: Updated `dev_guides/infrastructure_critiques.md` to track completed fixes

### [2.3.2] - 2025-01-27

#### Fixed
- Fixed shuffle algorithm inheritance issues - all algorithms now properly inherit from ShuffleAlgorithm base class
- Corrected Balanced Shuffle description in global README to accurately reflect playlist position-based shuffling (not artist/genre-based)
- Fixed CHANGELOG contradictions - moved unimplemented "Refresh Playlists" and "Logout" features to "Planned Features" section

#### Changed
- Enhanced shuffle algorithm documentation with detailed examples, use cases, and comparison table
- Updated shuffle algorithms README to reflect current implementations without audio features
- Improved algorithm descriptions and parameter documentation

#### Added
- Comprehensive infrastructure critiques and recommendations document in `dev_guides/infrastructure_critiques.md`
- Algorithm comparison table to help users choose appropriate shuffle methods
- Detailed use cases for all shuffle algorithms

### [2.3.1] - 2025-06-22

#### Fixed
- Resolved a critical bug preventing the multi-level "Undo" feature from working correctly. The session state is now managed robustly, allowing users to undo multiple shuffles in a row.
- Addressed a frontend issue where the "Undo" button would incorrectly disappear after a single use.
- Fixed a CSS regression where legal links on the index page were incorrectly styled.
- Replaced the hover-to-open mechanic on playlist tiles with a more stable click-to-open system to improve UX and prevent visual bugs.

### [2.3.0] - 2025-06-22

### Added
- Terms of Service and Privacy Policy pages for Spotify compliance
- Legal consent checkbox on login page
- Required user agreement before Spotify authentication
- Legal document routes and templates

### Changed
- Updated login flow to require explicit legal consent
- Enhanced UI for compliance with Spotify Developer Policy

## [2.2.4] - 2025-01-27

### Changed
- UI updates for Spotify compliance
- Removed follower count display from playlist cards
- Updated menu dropdown functionality
- Improved playlist model to handle follower visibility

### Fixed
- Spotify API compliance issues with follower data display

## [2.2.3] - 2025-01-26

### Changed
- Simplified feature logic for better performance
- Removed complex feature calculations that were causing issues

## [2.2.2] - 2025-01-25

### Added
- Playlist class implementation for better data management
- Improved playlist data handling and structure

### Changed
- Refactored playlist handling to use dedicated class
- Enhanced playlist model architecture

## [2.2.1] - 2025-01-24

### Changed
- Consolidated code structure for better maintainability
- Improved code organization and efficiency

## [2.2.0] - 2025-01-23

### Added
- Enhanced vibe-based shuffle method with improved audio feature analysis
- Better audio feature weighting and transition calculations

### Changed
- Improved vibe shuffle algorithm performance
- Enhanced audio feature processing

## [2.1.0] - 2025-01-22

### Added
- Stratified shuffle method for section-based shuffling
- New shuffle algorithm for maintaining playlist structure

### Changed
- Updated algorithm descriptions and documentation
- Improved algorithm package structure

## [2.0.3] - 2025-01-21

### Changed
- Updated text descriptors throughout the application
- Improved user interface text and descriptions
- Enhanced algorithm documentation

## [2.0.2] - 2025-01-20

### Changed
- Updated algorithm README to reflect current functionality
- Improved documentation for available shuffle methods

## [2.0.1] - 2024-04-21

### Changed
- Updated README.md to reflect current project structure
- Removed references to deprecated features (Vibe Shuffle, Docker directory)
- Updated documentation for current shuffle algorithms
- Improved project structure documentation
- Updated development workflow instructions

## [2.0.0] - 2024-04-20

### Added
- Multiple shuffle algorithms:
  - Basic Shuffle: Standard random shuffle with fixed start option
  - Balanced Shuffle: Ensures fair representation from all playlist parts
  - Percentage Shuffle: Allows shuffling specific portions of playlists
- Decorative music note patterns in background
- Smooth hover and transition effects
- Improved form input styling
- Better visual feedback for interactive elements
- Undo functionality for shuffle operations
- Detailed algorithm documentation
- Requirements management structure (base.txt, dev.txt, prod.txt)

### Changed
- Completely redesigned UI with modern glassmorphism effects
- Extended gradient background across all pages
- Improved visual hierarchy and spacing
- Enhanced playlist card interactions
- Updated color scheme for better contrast and readability
- Streamlined navigation by removing unnecessary elements
- Restructured project organization
- Improved error handling and logging
- Enhanced algorithm parameter validation

### Fixed
- Inconsistent styling between landing and dashboard pages
- Full-width background coverage issues
- Visual hierarchy in playlist cards
- Form input contrast and accessibility
- Default values for algorithm parameters
- Session handling and caching issues

### Removed
- Temporarily hidden Vibe Shuffle algorithm for future development
- Redundant configuration files
- Unused dependencies

## [1.0.0] - 2024-04-19

### Added
- Initial stable release
- Core playlist shuffling functionality
- Basic UI with Tailwind CSS
- Spotify integration 