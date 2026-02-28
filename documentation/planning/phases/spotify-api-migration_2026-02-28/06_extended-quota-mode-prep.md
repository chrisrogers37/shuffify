# Phase 06: Extended Quota Mode Preparation

`PENDING` — Business/Process Document (no code changes except scope reduction)

---

## Overview

| Field | Value |
|-------|-------|
| **Type** | Business preparation (not code implementation) |
| **Risk Level** | Low |
| **Estimated Effort** | Low (documentation + 1 code change) |
| **Dependencies** | None |
| **Blocks** | None |

Spotify's February 2026 changes impose a 5-user limit on Development Mode apps. To grow beyond 5 users, Shuffify must apply for Extended Quota Mode.

---

## 1. OAuth Scope Audit

### Current Scopes (10)

| # | Scope | Used? | Required By | Verdict |
|---|-------|-------|-------------|---------|
| 1 | `playlist-read-private` | **YES** | `get_user_playlists()` | **KEEP** |
| 2 | `playlist-read-collaborative` | **YES** | Collaborative playlist filtering | **KEEP** |
| 3 | `playlist-modify-private` | **YES** | All write operations | **KEEP** |
| 4 | `playlist-modify-public` | **YES** | Write operations on public playlists | **KEEP** |
| 5 | `user-read-private` | **PARTIALLY** | `current_user()` — still needed for id, display_name | **KEEP** |
| 6 | `user-read-email` | **NO** | Email field removed from API in Feb 2026 | **DROP** |
| 7 | `user-read-playback-state` | **NO** | Zero references in codebase | **DROP** |
| 8 | `user-read-currently-playing` | **NO** | Zero references in codebase | **DROP** |
| 9 | `user-read-recently-played` | **NO** | Zero references in codebase | **DROP** |
| 10 | `user-top-read` | **NO** | Zero references in codebase | **DROP** |

### Recommended Minimal Set (5 scopes)

```python
DEFAULT_SCOPES = [
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-read-private",
]
```

50% reduction. Every remaining scope maps to a core feature.

### Re-authentication Impact

Removing scopes does NOT invalidate existing tokens. Existing tokens retain their original scope set through refreshes. Users only get the reduced set on fresh OAuth logins. No forced re-authentication needed.

---

## 2. Application Narrative

### App Description

Shuffify is a web-based playlist management tool that gives Spotify users advanced control over playlist ordering. Unlike Spotify's built-in shuffle (single algorithm), Shuffify provides six specialized reordering algorithms that each solve a different listening problem: Artist Spacing (prevents back-to-back artists), Album Sequence (keeps album tracks together), Balanced Shuffle (even representation), Stratified Shuffle (section-aware), Percentage Shuffle (protect favorites), and Basic Shuffle (pure random).

Additional features: playlist workshop for track curation, point-in-time snapshots for backup/restoration, automated scheduled shuffles and raids, personalized dashboard with activity history.

### Problem Statement

Spotify's built-in shuffle doesn't account for artist variety, album coherence, or structural balance. Users who invest time curating playlists have no way to reorder them intelligently.

### Data Access Justification

| Scope | Justification |
|-------|--------------|
| `playlist-read-private` | Display user's private playlists for selection |
| `playlist-read-collaborative` | Include collaborative playlists in all operations |
| `playlist-modify-private` | Apply shuffle results, workshop edits, raid additions |
| `playlist-modify-public` | Same write operations for public playlists |
| `user-read-private` | Identify user (id, display_name, images) for dashboard and ownership |

---

## 3. Compliance Checklist

| # | Requirement | Status | Notes |
|---|-------------|--------|-------|
| 1 | App description | READY | Section 2 above |
| 2 | Scope justification | READY | Section 2 above |
| 3 | Privacy Policy | **NEEDS UPDATE** | `/privacy` — states "No Permanent Storage" which is false (PostgreSQL). Must update. |
| 4 | Terms of Service | **NEEDS DATE UPDATE** | `/terms` — last updated Jan 27, 2025 |
| 5 | Screenshots/demo | NOT STARTED | Need 3-5 screenshots |
| 6 | Registered business | OWNER ACTION | Required by Spotify's May 2025 criteria |
| 7 | Company email | OWNER ACTION | Must not be personal Gmail |
| 8 | 250k MAU threshold | NOT MET | See Section 6 |
| 9 | Developer Policy compliance | NEEDS AUDIT | Branding, attribution, trademark usage |
| 10 | Design Guidelines compliance | NEEDS AUDIT | "Powered by Spotify" attribution |

### Privacy Policy Issues

1. Section 4 states "No Permanent Storage" — must describe PostgreSQL storage, what's stored, retention
2. No mention of email collection removal after Feb 2026
3. Last updated date is over a year old

---

## 4. Scope Reduction Code Change

**Single file:** `shuffify/spotify/auth.py` lines 27-38

Remove 5 unused scopes. Add comment explaining why `user-read-private` is retained despite field removals.

**Test impact:** `tests/spotify/test_auth.py` imports `DEFAULT_SCOPES` — tests auto-adapt since they reference the import, not hardcoded values.

---

## 5. Timeline

| Date | Action |
|------|--------|
| Week 1 (Feb 28 - Mar 7) | Implement scope reduction, update Privacy Policy and Terms |
| Week 2 (Mar 7 - Mar 9) | Configure 5 allowlisted users, verify Premium subscription |
| Week 3+ (Mar 10+) | Submit Extended Quota Mode application |

---

## 6. Strategic Options for 250k MAU Requirement

The 250k MAU threshold (per Spotify's April 2025 criteria) is the primary obstacle.

1. **Apply anyway** — Demonstrate good faith, minimal scopes, clear enhancement. Review is described as "iterative."
2. **Operate within 5-user limit** — Sufficient for personal use and small group
3. **Seek commercial partner** — Music-related business with existing MAU base
4. **Open-source algorithms** — Standalone Python package; users run with own credentials

---

## 7. Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Application denied (250k MAU) | High | Plan to operate in dev mode; re-submit if criteria change |
| Scope reduction breaks tokens | Very Low | Removing scopes doesn't invalidate existing tokens |
| Privacy Policy flagged in review | Medium | Update BEFORE submitting application |
| Premium subscription lapses | Low | Set monthly calendar reminder |
