# Future Features Readiness Assessment

**Date:** January 2026
**Last Updated:** February 10, 2026
**Project:** Shuffify v2.4.x
**Scope:** Readiness assessment for planned features

---

## Executive Summary

This document assesses Shuffify's readiness to implement the planned future features. **Update (Feb 2026):** The service layer has been fully extracted, Redis sessions and caching are in place, and Pydantic validation is implemented. The primary remaining blocker is database persistence. UI enhancements are the most ready, while notification systems and automations still require background job infrastructure.

---

## Feature Overview

| Feature | Readiness | Blocking Dependencies |
|---------|-----------|----------------------|
| A. Database Persistence | 5/10 | ~~Service layer~~ ✅ → ORM setup only |
| B. User Logins | 3/10 | Database, user model, password hashing |
| C1. Playlist Re-ordering Automations | 5/10 | Database, background job system |
| C2. Playlist Raiding | 4/10 | C1 + change detection system |
| D. Notification System (SMS/Telegram) | 2/10 | Database, job system, external service APIs |
| E. Enhanced Snappy UI | 7/10 | WebSocket infrastructure |
| F. Live Playlist Preview | 6/10 | Preview endpoint, WebSocket; ~~caching~~ ✅ |
| G. Playlist Growth Features | 3/10 | Analytics, external integrations |

---

## Feature A: Database Persistence

### Current State
- **Storage:** Redis-based sessions (primary) with filesystem fallback (`.flask_session/`)
- **Caching:** Redis-based Spotify API response caching (60s playlists, 10min user, 24hr audio features)
- **Lifetime:** 1 hour session timeout (`PERMANENT_SESSION_LIFETIME = 3600`)
- **Data Lost on Session Expiry:** Undo history (stored in `session['playlist_states']`), shuffle state
- **Models:** Single `Playlist` dataclass (in-memory only, loaded per request from Spotify API or cache)

### Requirements
1. Database selection (PostgreSQL recommended)
2. ORM setup (SQLAlchemy)
3. Migration system (Alembic)
4. Service layer for data access
5. Session redesign (keep OAuth in session, move state to DB)

### Proposed Schema

```sql
-- Core tables
CREATE TABLE users (
    id VARCHAR(255) PRIMARY KEY,  -- Spotify user ID
    email VARCHAR(255),
    display_name VARCHAR(255),
    created_at TIMESTAMP,
    last_login_at TIMESTAMP,
    preferences JSONB DEFAULT '{}'
);

CREATE TABLE playlist_snapshots (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) REFERENCES users(id),
    playlist_id VARCHAR(255) NOT NULL,
    track_uris TEXT[] NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    is_original BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_snapshots_user_playlist
ON playlist_snapshots(user_id, playlist_id);

-- Enables unlimited undo history
-- Query: SELECT * FROM playlist_snapshots
--        WHERE user_id = ? AND playlist_id = ?
--        ORDER BY created_at DESC LIMIT 10;
```

### Implementation Roadmap

```
Phase 1: Infrastructure (1-2 days) — PENDING
├── Add SQLAlchemy to requirements
├── Create database configuration
├── Set up Alembic migrations
└── Create base repository pattern

Phase 2: Models (1-2 days) — PENDING
├── Create User model
├── Create PlaylistSnapshot model
├── Create migration scripts
└── Add model validation

Phase 3: Service Integration (2-3 days) — PENDING
├── Create UserRepository
├── Create SnapshotRepository
├── Migrate playlist state from session['playlist_states'] to DB
└── Update StateService to use repositories

Phase 4: Migration (1 day) — PENDING
├── Write data migration script
├── Test with existing sessions
└── Deploy with backward compatibility
```

### Readiness Score: 5/10 *(up from 2/10, service layer now exists)*

**Blocking Issues:**
- ~~No service layer (business logic in routes)~~ ✅ RESOLVED
- No repository pattern
- ~~Session manipulation scattered~~ ✅ RESOLVED (StateService manages all state via `session['playlist_states']`)

**What Exists:** Service layer (AuthService, PlaylistService, ShuffleService, StateService), Redis sessions, Redis caching, Pydantic validation

**Remaining Work:** ORM setup (SQLAlchemy + Alembic), repository pattern, database schema

**Effort Estimate:** 3-5 days *(reduced from 5-8, service layer done)*

---

## Feature B: User Logins (Beyond Spotify OAuth)

### Current State
- **Authentication:** Spotify OAuth only (via SpotifyAuthManager with 10 scopes)
- **Auth Service:** AuthService exists (`shuffify/services/auth_service.py`) — handles OAuth flow, token validation, client creation
- **Identity:** Spotify user ID is the user identity (no local user model)
- **Session:** OAuth tokens stored in Redis-backed Flask session (key: `spotify_token`)
- **No local accounts:** Users can't have Shuffify-specific credentials

### Use Cases
1. **Admin accounts:** Manage multiple Spotify accounts
2. **API access:** Programmatic access without browser OAuth
3. **Multi-service:** Connect Spotify AND Apple Music to one account
4. **Persistent preferences:** Settings survive token expiry

### Requirements
1. Database (from Feature A)
2. User model with local credentials
3. Password hashing (bcrypt/argon2)
4. Session redesign
5. Account linking (Spotify → Shuffify account)

### Proposed Architecture

```python
# models/user.py
@dataclass
class User:
    id: str  # UUID
    email: str
    password_hash: Optional[str]  # None for OAuth-only
    display_name: str
    created_at: datetime
    preferences: dict

    # Linked services
    spotify_id: Optional[str]
    spotify_token: Optional[dict]  # Encrypted
    apple_music_id: Optional[str]  # Future

# Auth flow options:
# 1. Login with email/password → Create session → Link Spotify
# 2. Login with Spotify → Auto-create Shuffify account
# 3. API key authentication for programmatic access
```

### Implementation Roadmap

```
Phase 1: User Model (Requires Feature A) — PENDING
├── Create User database model
├── Password hashing utilities (bcrypt/argon2)
├── User repository
└── Account creation service

Phase 2: Local Auth (2-3 days) — PENDING
├── Email/password registration endpoint
├── Login endpoint
├── Session management (extend existing AuthService)
├── Password reset flow

Phase 3: Account Linking (2 days) — PENDING
├── Link Spotify to existing local account
├── Unlink service
├── Multiple services per account
└── Service token encryption at rest

Phase 4: UI Updates (1-2 days) — PENDING
├── Registration page
├── Login page
├── Account settings page
└── Service linking UI
```

### Readiness Score: 3/10

**Blocking Issues:**
- Requires Feature A (database) — no user model exists
- ~~No auth service abstraction~~ ✅ RESOLVED (AuthService exists in `services/auth_service.py`)
- OAuth tokens not encrypted at rest (stored in Redis session)
- No password hashing infrastructure

**What Exists:** AuthService (OAuth flow, token validation), SpotifyAuthManager (token management with 10 scopes), Redis sessions

**Effort Estimate:** 7-10 days (after Feature A)

---

## Feature C1: Playlist Re-ordering Automations

### Current State
- **Manual only:** User must click "Shuffle" each time
- **No scheduling:** No cron or timer support
- **No triggers:** No event-based automation
- **No persistence:** Automation rules can't be saved

### Use Cases
1. **Daily shuffle:** "Shuffle my 'Daily Mix' every morning at 7 AM"
2. **After additions:** "Re-shuffle when new songs are added"
3. **Conditional:** "Shuffle only if playlist has >50 songs"

### Requirements
1. Database (Feature A) for storing rules
2. Background job system (Celery/RQ)
3. Scheduler (celery-beat or APScheduler)
4. Trigger system (webhooks, polling)
5. Automation engine

### Proposed Schema

```sql
CREATE TABLE automations (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) REFERENCES users(id),
    name VARCHAR(255) NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,

    -- Trigger configuration
    trigger_type VARCHAR(50) NOT NULL,  -- 'schedule', 'playlist_change'
    trigger_config JSONB NOT NULL,

    -- Action configuration
    action_type VARCHAR(50) NOT NULL,   -- 'shuffle'
    action_config JSONB NOT NULL,       -- {algorithm, params}

    -- Metadata
    created_at TIMESTAMP,
    last_run_at TIMESTAMP,
    run_count INTEGER DEFAULT 0
);

CREATE TABLE automation_logs (
    id SERIAL PRIMARY KEY,
    automation_id INTEGER REFERENCES automations(id),
    status VARCHAR(20),  -- 'success', 'failed', 'skipped'
    message TEXT,
    executed_at TIMESTAMP
);
```

### Proposed Architecture

```python
# automations/triggers/schedule.py
class ScheduleTrigger:
    name = "Schedule"
    parameters = {
        'cron': {'type': 'cron', 'description': 'Cron expression'},
        'timezone': {'type': 'timezone', 'default': 'UTC'}
    }

    def register(self, automation_id: int, config: dict):
        # Register with Celery beat
        schedule_task(
            task='automations.execute',
            args=[automation_id],
            cron=config['cron'],
            timezone=config['timezone']
        )

# automations/actions/shuffle.py
class ShuffleAction:
    name = "Shuffle Playlist"
    parameters = {
        'playlist_id': {'type': 'playlist_select'},
        'algorithm': {'type': 'algorithm_select'},
        'algorithm_params': {'type': 'dynamic'}  # Based on algorithm
    }

    def execute(self, user: User, config: dict) -> ActionResult:
        shuffle_service = ShuffleService(user.spotify_token)
        result = shuffle_service.execute(
            config['playlist_id'],
            config['algorithm'],
            config['algorithm_params']
        )
        return ActionResult(success=result.success)
```

### Implementation Roadmap

```
Phase 1: Infrastructure (3-4 days) — PENDING
├── Add Celery + Redis to stack (Redis already in use for sessions/caching)
├── Configure Celery beat scheduler
├── Create task execution framework
└── Set up monitoring (Flower)

Phase 2: Core System (3-4 days) — PENDING
├── Automation model (database required)
├── Trigger protocol + implementations
├── Action protocol + implementations
├── Automation engine (see 03_extensibility_evaluation.md Section 4)

Phase 3: UI (2-3 days) — PENDING
├── Automation list page
├── Create/edit automation form
├── Execution logs view
└── Enable/disable toggle

Phase 4: Triggers (2 days per trigger) — PENDING
├── Schedule trigger (cron)
├── Playlist change trigger (webhook/polling)
├── Manual trigger (on-demand)
```

### Readiness Score: 5/10 *(up from 4/10, service layer done)*

**Blocking Issues:**
- Requires Feature A (database)
- No background job infrastructure (Celery not yet added)
- No scheduler
- ~~Service layer needed~~ ✅ RESOLVED

**What Exists:** ShuffleService (algorithm orchestration), Redis (can serve as Celery broker), service layer for all core operations

**Effort Estimate:** 8-12 days (after Feature A) *(reduced, service layer done)*

---

## Feature C2: Playlist Raiding

### Description
"Point at playlists and artists, find new additions, seed them into your playlists"

### Current State
- **Manual discovery:** No automated song discovery
- **No monitoring:** Can't watch playlists/artists for changes
- **No cross-playlist:** Can't copy songs between playlists

### Use Cases
1. **Artist monitoring:** "When Artist X releases new music, add to my playlist"
2. **Playlist following:** "Mirror new songs from 'Discover Weekly' to my archive"
3. **Curator following:** "Copy new additions from curator playlists I follow"
4. **Genre discovery:** "Find songs similar to my top tracks and suggest them"

### Requirements
1. All requirements from C1 (automations)
2. Spotify API for:
   - Artist new releases (`/artists/{id}/albums?include_groups=single,album`)
   - Playlist snapshots for change detection
3. Change detection system
4. Song matching/deduplication

### Proposed Schema

```sql
CREATE TABLE raid_sources (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) REFERENCES users(id),
    source_type VARCHAR(20) NOT NULL,  -- 'playlist', 'artist'
    source_id VARCHAR(255) NOT NULL,   -- Spotify ID
    source_name VARCHAR(255),
    last_checked_at TIMESTAMP,
    last_snapshot TEXT[]  -- Track URIs for change detection
);

CREATE TABLE raid_rules (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) REFERENCES users(id),
    name VARCHAR(255) NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,

    -- Sources (multiple)
    source_ids INTEGER[] NOT NULL,  -- References raid_sources

    -- Target
    target_playlist_id VARCHAR(255) NOT NULL,

    -- Options
    position VARCHAR(20) DEFAULT 'end',  -- 'start', 'end', 'random'
    deduplicate BOOLEAN DEFAULT TRUE,
    max_per_run INTEGER DEFAULT 10,

    -- Filters (optional)
    filters JSONB DEFAULT '{}'  -- {min_energy: 0.5, genres: ['rock']}
);
```

### Proposed Architecture

```python
# raiding/sources.py
class PlaylistSource:
    def get_new_tracks(self, last_snapshot: List[str]) -> List[Track]:
        current = spotify.get_playlist_tracks(self.source_id)
        current_uris = {t['uri'] for t in current}
        previous_uris = set(last_snapshot)
        new_uris = current_uris - previous_uris
        return [t for t in current if t['uri'] in new_uris]

class ArtistSource:
    def get_new_tracks(self, last_checked: datetime) -> List[Track]:
        albums = spotify.get_artist_albums(
            self.source_id,
            include_groups='single,album',
            limit=10
        )
        new_albums = [a for a in albums if a['release_date'] > last_checked]
        tracks = []
        for album in new_albums:
            tracks.extend(spotify.get_album_tracks(album['id']))
        return tracks

# raiding/engine.py
class RaidEngine:
    def execute_raid(self, rule: RaidRule) -> RaidResult:
        new_tracks = []

        # Gather from all sources
        for source in rule.sources:
            new_tracks.extend(source.get_new_tracks())

        # Deduplicate against target
        if rule.deduplicate:
            target_tracks = spotify.get_playlist_tracks(rule.target_playlist_id)
            target_uris = {t['uri'] for t in target_tracks}
            new_tracks = [t for t in new_tracks if t['uri'] not in target_uris]

        # Apply filters
        new_tracks = self.apply_filters(new_tracks, rule.filters)

        # Limit
        new_tracks = new_tracks[:rule.max_per_run]

        # Add to target
        if new_tracks:
            uris = [t['uri'] for t in new_tracks]
            spotify.add_to_playlist(rule.target_playlist_id, uris, rule.position)

        return RaidResult(added=len(new_tracks), tracks=new_tracks)
```

### Implementation Roadmap

```
Phase 1: Source System (2-3 days) — PENDING
├── Source protocol (PlaylistSource, ArtistSource)
├── PlaylistSource implementation (snapshot-based change detection)
├── ArtistSource implementation (release date comparison)
└── Change detection logic

Phase 2: Raid Engine (2-3 days) — PENDING
├── RaidRule model (database required)
├── Deduplication logic (compare target playlist URIs)
├── Filter system (audio features, genre, etc.)
└── Execution engine

Phase 3: Integration (2 days) — PENDING
├── Connect to automation system (Feature C1)
├── Scheduled raid execution via Celery
├── Notification on raid results (Feature D)

Phase 4: UI (2-3 days) — PENDING
├── Source management page
├── Raid rule creation form
├── Execution history view
└── "Raid Now" button
```

### Readiness Score: 4/10

**Blocking Issues:**
- Requires Feature C1 (automation infrastructure), plus:
- Need change detection system (Spotify playlist snapshot comparison)
- Need efficient Spotify API usage (rate limits — current retry logic helps)
- SpotifyAPI already has `get_playlist_tracks()` and caching for reads

**Effort Estimate:** 8-11 days (after C1)

---

## Feature D: Notification System (SMS/Telegram)

### Current State
- **No notifications:** Users must check the app
- **No external integrations:** No SMS, email, or messaging
- **Flash messages only:** Ephemeral, browser-only

### Use Cases
1. **Raid notifications:** "3 new songs added to your playlist"
2. **Automation status:** "Daily shuffle completed"
3. **Error alerts:** "Automation failed: token expired"
4. **New release alerts:** "Artist X released new album"

### Requirements
1. Database (Feature A) for user notification preferences
2. Background jobs for async delivery
3. External service integrations:
   - Telegram Bot API
   - Twilio for SMS
   - Email (SMTP or SendGrid)
4. Notification channel abstraction
5. Template system for messages

### External Service Setup

```python
# Telegram
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
# User provides chat_id after /start to bot

# Twilio (SMS)
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_FROM_NUMBER = os.getenv('TWILIO_FROM_NUMBER')

# Email
SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')
```

### Proposed Schema

```sql
CREATE TABLE notification_channels (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) REFERENCES users(id),
    channel_type VARCHAR(20) NOT NULL,  -- 'telegram', 'sms', 'email'
    config JSONB NOT NULL,  -- {chat_id: '123'} or {phone: '+1...'}
    verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP
);

CREATE TABLE notifications (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) REFERENCES users(id),
    channel_id INTEGER REFERENCES notification_channels(id),
    notification_type VARCHAR(50),  -- 'raid_complete', 'automation_failed'
    message TEXT,
    status VARCHAR(20),  -- 'pending', 'sent', 'failed'
    sent_at TIMESTAMP,
    error_message TEXT
);
```

### Implementation Roadmap

```
Phase 1: Channel System (2-3 days) — PENDING
├── NotificationChannel protocol (see 03_extensibility_evaluation.md Section 3)
├── Telegram implementation (Telegram Bot API)
├── SMS implementation (Twilio)
├── Email implementation (SMTP/SendGrid)
└── Channel registry (same pattern as ShuffleRegistry)

Phase 2: Delivery System (2 days) — PENDING
├── Notification queue (Celery task)
├── Retry logic (can adapt SpotifyAPI's existing retry pattern)
├── Rate limiting per channel
└── Failure handling and logging

Phase 3: Templates (1-2 days) — PENDING
├── Message templates per notification type
├── Variable substitution (Jinja2 can be reused)
├── Localization (future)

Phase 4: UI (2-3 days) — PENDING
├── Add notification channel page
├── Verify channel (Telegram: /verify code)
├── Notification preferences
└── Test notification button

Phase 5: Integration (1-2 days) — PENDING
├── Connect to automation system (Feature C1)
├── Connect to raid system (Feature C2)
├── Error notification hooks
```

### Readiness Score: 2/10

**Blocking Issues:**
- Requires Feature A (database for channel config), C1 (background jobs for async delivery)
- No external service integrations yet
- Need third-party accounts (Telegram Bot, Twilio, SendGrid)

**What Exists:** Protocol + Registry pattern proven (ShuffleRegistry), Redis available for message queuing

**Effort Estimate:** 8-12 days (after C1)

---

## Feature E: Enhanced Snappy UI

### Current State
- **Tailwind CSS:** Modern, responsive design
- **AJAX operations:** No full page reloads for shuffle/undo
- **Animations:** Fade-in, slide-up effects
- **Accessibility:** Skip links, focus states, ARIA

### Areas for Enhancement
1. **Instant feedback:** Optimistic UI updates
2. **Skeleton loading:** Better perceived performance
3. **Real-time updates:** WebSocket for live changes
4. **Keyboard navigation:** Full keyboard support
5. **Mobile experience:** Touch gestures, swipe actions

### Current Frontend Stack
- Jinja2 templates
- Tailwind CSS (CDN)
- Vanilla JavaScript
- No build process

### Proposed Enhancements

```javascript
// 1. Optimistic UI Updates
async function handleShuffle(playlistId) {
    // Show optimistic state immediately
    showLoadingState(playlistId);
    updateUIOptimistically(playlistId, 'shuffling');

    try {
        const result = await shufflePlaylist(playlistId);
        // Confirm with actual data
        updatePlaylistUI(playlistId, result.playlist);
    } catch (error) {
        // Rollback optimistic update
        rollbackUI(playlistId);
        showError(error.message);
    }
}

// 2. Skeleton Loading
function showSkeletonLoader(container) {
    container.innerHTML = `
        <div class="animate-pulse space-y-4">
            <div class="h-4 bg-gray-700 rounded w-3/4"></div>
            <div class="h-4 bg-gray-700 rounded w-1/2"></div>
            <div class="h-4 bg-gray-700 rounded w-5/6"></div>
        </div>
    `;
}

// 3. Keyboard Navigation
document.addEventListener('keydown', (e) => {
    if (e.key === 's' && e.ctrlKey) {
        e.preventDefault();
        shuffleCurrentPlaylist();
    }
    if (e.key === 'z' && e.ctrlKey) {
        e.preventDefault();
        undoCurrentPlaylist();
    }
});
```

### Implementation Roadmap

```
Phase 1: Quick Wins (1-2 days) — PENDING (partially started)
├── Skeleton loaders for playlist loading
├── Optimistic UI updates
├── Better loading spinners
└── Toast notifications (replace flash)
    ✅ Refresh playlists button done (POST /refresh-playlists)
    ✅ Response caching with skip_cache bypass done

Phase 2: Keyboard & Accessibility (1-2 days) — PENDING
├── Full keyboard navigation
├── ARIA live regions for updates
├── Focus management
└── Screen reader improvements

Phase 3: Performance (2-3 days) — PENDING
├── Virtual scrolling for long playlists
├── Image lazy loading
├── ✅ Response caching (COMPLETED — Redis caching layer)
└── Service worker (offline support)

Phase 4: Advanced (3-4 days) — PENDING
├── WebSocket for real-time updates
├── Drag-and-drop track reordering
├── Touch gestures for mobile
└── Progressive Web App (PWA)
```

### Readiness Score: 7/10 *(up from 6/10, refresh button + caching done)*

**Strengths:**
- Good CSS foundation (Tailwind CSS via CDN)
- AJAX already working for shuffle/undo operations
- Modern browser targets
- ✅ Refresh playlists button implemented (Feb 2026) — `POST /refresh-playlists`
- ✅ Response caching with skip_cache bypass via SpotifyCache

**Gaps:**
- No build process (could add Vite for JS bundling/minification)
- No WebSocket infrastructure (needed for real-time updates)
- Limited JavaScript architecture (vanilla JS, no framework, no module system)
- 3 monolithic templates (944 lines total) — no component decomposition

**Effort Estimate:** 6-10 days total *(reduced, some quick wins done)*

---

## Feature F: Live Playlist Preview

### Description
"Live-view of Spotify songs in a playlist, can deploy shuffle algorithms and see songs and order before clicking save"

### Current State
- **Immediate commit:** Shuffle goes directly to Spotify
- **No preview:** Can't see result before saving
- **No comparison:** Can't compare before/after

### Use Cases
1. **Preview shuffle:** See new order without committing
2. **Compare algorithms:** Try different algorithms, pick best
3. **Manual adjust:** Drag tracks before saving
4. **A/B view:** Side-by-side before/after

### Requirements
1. Client-side shuffle execution (or server preview endpoint)
2. Caching layer for playlist data
3. WebSocket for real-time preview updates
4. Diff visualization (what moved where)

### Proposed Architecture

```javascript
// Client-side preview
class PlaylistPreviewer {
    constructor(playlistId) {
        this.playlistId = playlistId;
        this.originalOrder = [];
        this.previewOrder = [];
    }

    async loadPlaylist() {
        const data = await fetch(`/playlist/${this.playlistId}`);
        this.originalOrder = data.tracks;
        this.previewOrder = [...this.originalOrder];
        this.render();
    }

    async previewShuffle(algorithm, params) {
        // Option 1: Server-side preview
        const preview = await fetch(`/shuffle/${this.playlistId}/preview`, {
            method: 'POST',
            body: JSON.stringify({ algorithm, params })
        });
        this.previewOrder = preview.tracks;

        // Option 2: Client-side shuffle (requires algorithm in JS)
        // this.previewOrder = algorithms[algorithm].shuffle(this.originalOrder, params);

        this.renderPreview();
    }

    renderPreview() {
        // Show side-by-side or diff view
        this.renderDiff(this.originalOrder, this.previewOrder);
    }

    async commitPreview() {
        await fetch(`/shuffle/${this.playlistId}`, {
            method: 'POST',
            body: JSON.stringify({ track_uris: this.previewOrder.map(t => t.uri) })
        });
    }

    discardPreview() {
        this.previewOrder = [...this.originalOrder];
        this.render();
    }
}
```

### Server-Side Preview Endpoint (Proposed)

```python
# Proposed addition to shuffify/routes.py
# Uses existing service layer — no new infrastructure needed

@main.route('/shuffle/<playlist_id>/preview', methods=['POST'])
def preview_shuffle(playlist_id):
    """Preview shuffle without committing to Spotify."""
    client = require_auth()
    if client is None:
        return json_error("Not authenticated", 401)

    # Use existing PlaylistService to load tracks
    playlist_service = PlaylistService(client)
    playlist = playlist_service.get_playlist(playlist_id)

    # Use existing ShuffleService to execute algorithm
    shuffle_service = ShuffleService()
    algorithm_name = request.json.get('algorithm')
    params = request.json.get('params', {})

    # Execute shuffle (returns URIs without Spotify side effects)
    shuffled_uris = shuffle_service.execute(
        playlist, algorithm_name, **params
    )

    # Return preview without updating Spotify
    return json_success({
        'original': playlist.get_track_uris(),
        'preview': shuffled_uris,
        'diff': compute_diff(playlist.tracks, shuffled_uris)
    })
```

**Note:** The existing `ShuffleService.execute()` and algorithm `shuffle()` methods return URIs without side effects. The current `/shuffle/<id>` route separately calls `PlaylistService.update_playlist_tracks()` to commit to Spotify. A preview endpoint would simply skip that commit step.

### Implementation Roadmap

```
Phase 1: Preview Endpoint (1-2 days) — PENDING
├── /shuffle/<id>/preview endpoint (new route in routes.py)
├── Return preview without committing to Spotify
└── Compute diff information (track position changes)

Phase 2: Preview UI (2-3 days) — PENDING
├── Preview mode toggle in dashboard
├── Side-by-side view (before/after)
├── Diff highlighting (moved tracks)
└── Commit/discard buttons

Phase 3: Enhanced Preview (2-3 days) — PENDING
├── Multiple algorithm comparison
├── Drag-and-drop manual adjustment
├── Undo within preview
└── Save as preset

Phase 4: Real-time (2 days) — PENDING
├── WebSocket for instant preview
├── Parameter slider with live update
└── Performance optimization
```

### Readiness Score: 6/10 *(up from 5/10, caching layer done)*

**Strengths:**
- Algorithm architecture supports preview — `shuffle()` returns URIs without side effects
- ShuffleService.execute() could be adapted to return preview without calling Spotify
- AJAX infrastructure exists (shuffle/undo already use fetch)
- ✅ Redis caching layer available for playlist data (60s TTL for playlists)

**Gaps:**
- No preview endpoint (current `POST /shuffle/<id>` commits immediately to Spotify)
- No diff computation logic
- No WebSocket infrastructure

**Effort Estimate:** 6-9 days *(reduced, caching in place)*

---

## Feature G: Automatic Playlist Growth

### Description
"Ways to automatically push playlists for growth"

### Interpretation
This could mean:
1. **Social sharing:** Auto-post to social media
2. **Collaboration:** Invite others to contribute
3. **Playlist promotion:** Submit to Spotify curators
4. **SEO optimization:** Better descriptions for discovery

### Current State
- **No sharing:** No social integration
- **No collaboration tools:** Just single-user playlists
- **No promotion:** No curator submission
- **No analytics:** No visibility into playlist performance

### Potential Features

```
1. Social Sharing
├── Share to Twitter/Facebook
├── Generate shareable link
├── Embed player widget
└── QR code generation

2. Collaboration
├── Invite collaborators
├── Suggestion queue (non-collaborators can suggest)
├── Voting on suggestions
└── Activity feed

3. Analytics
├── Follower count tracking
├── Play count estimation
├── Growth trends
└── Comparison with similar playlists

4. Promotion
├── Curator submission helper
├── Description optimization tips
├── Cover image suggestions
└── Playlist naming suggestions
```

### External Dependencies
- Social media APIs (Twitter, Facebook)
- Spotify follower data (limited in API)
- Curator databases (third-party or manual)

### Readiness Score: 3/10

**Blocking Issues:**
- Requires database for analytics storage
- Social APIs require separate OAuth flows
- Curator data not readily available
- Feature definition unclear

**Recommendation:** Clarify feature scope before detailed planning

---

## Summary Matrix

| Feature | Readiness | Status | Dependencies | Effort | Priority |
|---------|-----------|--------|--------------|--------|----------|
| A. Database | 5/10 | **PENDING** | ~~Service layer~~ ✅ → ORM only | 3-5 days | **FIRST** |
| B. User Logins | 3/10 | **PENDING** | A (database) | 7-10 days | After A |
| C1. Automations | 5/10 | **PENDING** | A + Celery | 8-12 days | After A |
| C2. Raiding | 4/10 | **PENDING** | C1 | 8-11 days | After C1 |
| D. Notifications | 2/10 | **PENDING** | C1 + external APIs | 8-12 days | After C1 |
| E. Snappy UI | 7/10 | **PENDING** (partial) | Minor; WebSocket for advanced | 6-10 days | Parallel |
| F. Live Preview | 6/10 | **PENDING** | Preview endpoint | 6-9 days | Parallel |
| G. Playlist Growth | 3/10 | **PENDING** | A + social APIs | TBD | Last |

### Recommended Order

```
PHASE 1: FOUNDATION — IN PROGRESS (service layer done, database next)
├── ✅ Service layer extraction — COMPLETED (Jan 2026)
├── Feature A: Database — PENDING (critical path)
└── Feature E: UI improvements — PENDING (can run in parallel)

PHASE 2: CORE FEATURES — PENDING (blocked by Phase 1)
├── Feature C1: Automations — PENDING
├── Feature F: Live preview — PENDING (can start without database)
└── Feature B: User logins — PENDING (if needed)

PHASE 3: ADVANCED FEATURES — PENDING (blocked by Phase 2)
├── Feature C2: Raiding — PENDING
├── Feature D: Notifications — PENDING
└── Feature G: Growth — PENDING (scope TBD)
```

### Critical Path

```
Service Layer ✅ → Database (PENDING) → Automations → Raiding → Notifications
                       ↓
                  UI Improvements (parallel, PENDING)
                  Live Preview (parallel, PENDING)
```

---

**Next:** See [05_brainstorm_enhancements.md](./05_brainstorm_enhancements.md) for additional enhancement ideas.
