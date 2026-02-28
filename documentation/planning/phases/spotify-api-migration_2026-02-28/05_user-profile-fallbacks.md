# Phase 05: User Profile Fallbacks

`PENDING` Target: 2026-02-28

---

## Overview

| Field | Value |
|-------|-------|
| **PR Title** | `fix: Add defensive fallbacks for missing user profile fields` |
| **Risk Level** | Low |
| **Estimated Effort** | Low (1-2 hours) |
| **Dependencies** | None |
| **Blocks** | None |

---

## Motivation

Spotify's February 2026 API changes remove `country`, `email`, `product`, and `explicit_content` from `GET /v1/me`. While `display_name` is NOT being removed, it can be `None` for some Spotify accounts. The user has explicitly stated: **"When we don't have someone's name we should offer a general greeting."**

Additionally, the current upsert logic unconditionally overwrites DB fields with API response values — meaning every login after March 9 will silently erase previously-stored `email`, `country`, and `product` values.

---

## Key Decisions

- **OAuth scopes retained**: `user-read-private` and `user-read-email` are NOT removed. Removing them would invalidate all existing refresh tokens and force re-authentication. A comment is added explaining why.
- **No DB migration**: All affected columns are already nullable.
- **Preserve-on-absent pattern**: When a field key is absent from the API response (not just `None`), keep the existing DB value.

---

## Files to Modify

### `shuffify/templates/dashboard.html`

#### Avatar fallback (lines 16-19)

Current `{{ user.display_name[0] }}` crashes when `display_name` is `None`.

Replace the `{% else %}` block with a conditional showing either the initial or a generic person SVG icon:

```html
{% else %}
    <div class="w-16 h-16 rounded-full bg-white/10 border-2 border-white/20 flex items-center justify-center mr-4">
        {% if user.display_name %}
            <span class="text-2xl text-white">{{ user.display_name[0] }}</span>
        {% else %}
            <svg class="w-8 h-8 text-white/60" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>
            </svg>
        {% endif %}
    </div>
{% endif %}
```

#### Greeting fallbacks (lines 22-37)

Each of the three greeting variants gets a conditional:

- Returning user: `Welcome back, {{ user.display_name }}!` → `Welcome back!` when no name
- New user: `Welcome to Shuffify, {{ user.display_name }}!` → `Welcome to Shuffify!` when no name
- Fallback: `Welcome, {{ user.display_name }}!` → `Welcome!` when no name

Pattern for each:
```html
{% if user.display_name %}
    <h2 ...>Welcome back, {{ user.display_name }}!</h2>
{% else %}
    <h2 ...>Welcome back!</h2>
{% endif %}
```

#### Image alt text (line 14)

```html
alt="{{ user.display_name or 'User avatar' }}"
```

### `shuffify/services/user_service.py` (lines 89-105)

Replace unconditional field overwrites with preserve-on-absent pattern:

```python
# Always update fields Spotify still returns
user.display_name = user_data.get("display_name")
user.profile_image_url = profile_image_url
user.spotify_uri = user_data.get("uri")

# Preserve existing DB values when API fields are absent
# (Spotify Feb 2026 removed email, country, product from GET /v1/me)
if "email" in user_data:
    user.email = user_data["email"]
if "country" in user_data:
    user.country = user_data["country"]
if "product" in user_data:
    user.spotify_product = user_data["product"]
```

Key distinction: `"email" in user_data` checks for key presence, not just truthiness. This allows explicit `None` values to overwrite while absent keys preserve existing data.

### `shuffify/spotify/auth.py` (line 26)

Add explanatory comment above `DEFAULT_SCOPES`:

```python
# NOTE: user-read-private and user-read-email are retained even
# though Spotify's Feb 2026 API changes removed country, email,
# product, and explicit_content from GET /v1/me. Removing these
# scopes would invalidate all existing refresh tokens, forcing
# every user to re-authenticate. The scopes remain valid — they
# simply return fewer fields now.
```

---

## Testing

### New tests in `tests/services/test_user_service.py`

| Test | What It Verifies |
|------|-----------------|
| `test_update_preserves_email_when_absent` | Email not overwritten when key missing from API response |
| `test_update_preserves_country_when_absent` | Country not overwritten when key missing |
| `test_update_preserves_product_when_absent` | Product not overwritten when key missing |
| `test_update_allows_explicit_none_for_email` | When key IS present with `None` value, it DOES overwrite |
| `test_create_without_removed_fields` | New user without email/country/product gets `None` |

### New tests in `tests/routes/test_core_routes.py`

| Test | What It Verifies |
|------|-----------------|
| `test_dashboard_renders_without_display_name` | Dashboard shows "Welcome!" (no crash) when `display_name` is `None` |
| `test_dashboard_returning_user_without_display_name` | Shows "Welcome back!" (not "Welcome back, !") |

---

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| `display_name` removed by Spotify later | Low | Fallback already handles `None`/empty |
| `"email" in user_data` keeps stale data | Very Low | Acceptable: old email > blank |
| SVG icon inconsistent with design | Low | Uses same opacity as existing text initial |
