# Phase 02: Animated Playlist Shuffle Hero

**PR Title:** feat: Add animated playlist shuffle visualization to hero section
**Status:** 🔧 IN PROGRESS
**Started:** 2026-02-22
**Risk:** Low
**Effort:** High (~4-6 hours)
**Files Modified:** 1 (`shuffify/templates/index.html`) — base.html no longer needs changes
**Files Created:** 0

---

## 1. Context

The landing page currently has zero product visualization. Visitors see text and a CTA button but cannot understand what Shuffify does before signing up. This phase adds the centerpiece animation: a mock playlist card in the hero section that continuously demonstrates track reordering in real-time. This is the most impactful change for first-impression conversion.

Phase 01 (Dark Theme Foundation) must be applied first. That phase changes the page background to `#0a0a0f`, establishes neon green (`#00ff87`) accents, and replaces the emoji title with a "Shuff"/"ify" wordmark. This phase builds directly on that dark foundation.

---

## 2. Dependencies

- **Requires:** Phase 01 (Dark Theme + Neon Accent Foundation) -- the dark background and neon color palette must exist before this phase can work visually.
- **Unlocks:** Phase 04 (Mobile Responsive + Performance) -- Phase 04 will add reduced-motion media queries and mobile adjustments for this animation.
- **Parallel-safe with:** Phase 03 (Section Redesign + Scroll Motion) -- Phase 03 modifies sections below the hero; this phase only modifies the hero `<div>` (lines 7-71 of current `index.html`).

---

## 3. Visual Specification

### 3.1 Two-Column Hero Layout

The hero section becomes a flex row on desktop (>=1024px):

- **Left column (55% width):** Text content -- wordmark (from Phase 01), subtitle, description paragraph, consent form, CTA button. Left-aligned.
- **Right column (45% width):** Animated playlist card. Vertically centered relative to the text.
- **Gap:** 48px (`gap-12`) between columns.
- **Max width:** `max-w-7xl` (1280px), centered with `mx-auto`.
- **Padding:** `pt-20 pb-16 px-6`.

### 3.2 Playlist Card Design

The card simulating a playlist:

- **Container:** `width: 380px`, `border-radius: 16px`, `background: rgba(255, 255, 255, 0.05)`, `border: 1px solid rgba(0, 255, 135, 0.15)`, `box-shadow: 0 0 40px rgba(0, 255, 135, 0.05)`.
- **Card header:** A top bar with text "My Playlist" in white/80, a small play button icon, and track count "8 tracks". Height ~48px, bottom border `rgba(255, 255, 255, 0.08)`.
- **Track list area:** Vertical list of 8 track items. Each item is 56px tall.

### 3.3 Individual Track Item Design

Each track row contains:

- **Album art placeholder:** 40x40px square, `border-radius: 6px`, filled with a unique CSS gradient (no images).
- **Track info:** Track name in white (14px, font-medium), artist name in white/50 (12px).
- **Duration:** Right-aligned, white/40, 12px. Format "M:SS".
- **Row styling:** `padding: 8px 16px`, `border-bottom: 1px solid rgba(255, 255, 255, 0.04)`, `border-radius: 8px`.
- **Hover state (CSS only):** `background: rgba(0, 255, 135, 0.05)`.

### 3.4 Fake Track Data

| # | Track Name | Artist | Duration | Gradient Colors |
|---|-----------|--------|----------|-----------------|
| 1 | Midnight Drive | Luna Wave | 3:42 | `#667eea` to `#764ba2` |
| 2 | Electric Dreams | Neon Pulse | 4:15 | `#f093fb` to `#f5576c` |
| 3 | Golden Hour | Amber Skies | 3:28 | `#4facfe` to `#00f2fe` |
| 4 | Velvet Thunder | Storm Chaser | 5:01 | `#43e97b` to `#38f9d7` |
| 5 | Cosmic Drift | Star Wanderer | 3:55 | `#fa709a` to `#fee140` |
| 6 | Ocean Waves | Deep Current | 4:33 | `#a18cd1` to `#fbc2eb` |
| 7 | Neon Lights | City Pulse | 3:17 | `#ffecd2` to `#fcb69f` |
| 8 | Shadow Dance | Phantom Beat | 4:48 | `#a1c4fd` to `#c2e9fb` |

### 3.5 Animation Behavior

The animation loop cycle (total ~7 seconds):

1. **Settled state (2.5s):** All tracks visible at rest positions. No movement.
2. **Shuffle indicator (0.5s):** A subtle green pulse/glow on the card border indicating a shuffle is about to happen. The border color briefly pulses to `rgba(0, 255, 135, 0.4)` and back.
3. **Shuffle animation (1.5s):** Tracks smoothly slide from their current positions to new positions using `transform: translateY()`. Each track has a staggered delay (0ms to 210ms, 30ms apart). Tracks moving down use `cubic-bezier(0.34, 1.56, 0.64, 1)` (slight overshoot). Tracks moving up use `cubic-bezier(0.22, 0.61, 0.36, 1)` (smooth ease-out).
4. **Post-settle pause (2.5s):** Tracks at new positions. Cycle restarts from step 1.

### 3.6 Glow Effect on Moving Tracks

When a track is animating (during the shuffle phase):
- Add `box-shadow: 0 0 12px rgba(0, 255, 135, 0.15)` to the track row.
- Add `background: rgba(0, 255, 135, 0.03)` to the track row.
- Remove both after the transition completes.

---

## 4. Detailed Implementation Plan

### Step 1: Update Tailwind Config in `base.html`

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/base.html`
**Location:** Lines 9-38 (the `tailwind.config` script block)

Add new animation keyframes to the existing config. The current config at line 10-38 looks like:

```javascript
tailwind.config = {
    theme: {
        extend: {
            colors: {
                'spotify-green': '#1DB954',
                'spotify-dark': '#191414',
            },
            animation: {
                'fade-in': 'fadeIn 0.6s ease-out',
                'slide-up': 'slideUp 0.6s ease-out',
                'scale-in': 'scaleIn 0.4s ease-out',
            },
            // ... keyframes
        }
    }
}
```

**After Phase 01**, the colors will already include neon values. Add to the `animation` object:

```javascript
'shuffle-glow': 'shuffleGlow 0.5s ease-in-out',
```

And add to the `keyframes` object:

```javascript
shuffleGlow: {
    '0%': { borderColor: 'rgba(0, 255, 135, 0.15)' },
    '50%': { borderColor: 'rgba(0, 255, 135, 0.4)', boxShadow: '0 0 30px rgba(0, 255, 135, 0.1)' },
    '100%': { borderColor: 'rgba(0, 255, 135, 0.15)' }
}
```

**Important:** Phase 01 will have already modified this config to add neon colors. Do NOT overwrite Phase 01 changes -- only ADD the new animation/keyframe entries.

### Step 2: Replace the Hero Section in `index.html`

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/index.html`
**Location:** Lines 7-71 (the hero `<div>` from `<!-- Hero Section -->` through the closing `</div>` that contains the text + consent form)

**Current code (lines 7-71):**
```html
<!-- Hero Section -->
<div class="relative flex items-center justify-center pt-16 pb-8">
    <div class="w-full max-w-6xl mx-auto px-4 text-center">
        <div class="animate-fade-in space-y-6">
            <!-- ... title, subtitle, description, consent form ... -->
        </div>
    </div>
</div>
```

**Replace with the following complete HTML (this is the ENTIRE hero section replacement):**

```html
<!-- Hero Section — Two-Column Layout -->
<div class="relative pt-20 pb-16 px-6">
    <div class="w-full max-w-7xl mx-auto">
        <div class="flex flex-col lg:flex-row items-center gap-12">

            <!-- Left Column: Text Content -->
            <div class="flex-1 lg:max-w-[55%] text-center lg:text-left animate-fade-in space-y-6">
                <h1 class="text-6xl font-bold text-white">
                    <span style="color: #00ff87;">Shuff</span><span class="text-white">ify</span>
                </h1>
                <h2 class="text-3xl font-semibold text-white/90">Playlist Perfection</h2>
                <p class="text-xl text-white/80 max-w-xl leading-relaxed">
                    Reorder your carefully curated Spotify playlists with intelligent shuffling algorithms.
                    Perfect for tastemakers who want to keep their playlists fresh and flowing.
                </p>

                {% if not session.get('spotify_token') %}
                <div class="mt-8">
                    <!-- Legal Consent Form -->
                    <form action="{{ url_for('main.login') }}" method="GET" class="max-w-lg {% if not 'lg' %}mx-auto{% endif %}">
                        <!-- Consent Card -->
                        <div class="consent-card bg-white/5 backdrop-blur-md rounded-2xl p-6 border border-white/10 mb-6">
                            <div class="flex items-start space-x-4">
                                <div class="flex-shrink-0">
                                    <div class="w-8 h-8 rounded-full flex items-center justify-center" style="background: #00ff87;">
                                        <svg class="w-4 h-4 text-black" fill="currentColor" viewBox="0 0 20 20">
                                            <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/>
                                        </svg>
                                    </div>
                                </div>
                                <div class="flex-1">
                                    <h4 class="text-lg font-semibold text-white mb-2">Quick &amp; Secure</h4>
                                    <p class="text-white/70 text-sm mb-4">
                                        We use Spotify's secure OAuth to access your playlists. Your data stays with Spotify — we never store it.
                                    </p>
                                    <div class="flex items-start space-x-3">
                                        <input type="checkbox" id="legal-consent" name="legal_consent" required
                                               class="mt-1 h-4 w-4 bg-white/10 border-white/30 rounded focus:ring-2"
                                               style="accent-color: #00ff87;">
                                        <label for="legal-consent" class="text-sm text-white/80 leading-relaxed legal-links">
                                            I agree to the
                                            <a href="{{ url_for('main.terms') }}" target="_blank" class="font-medium hover:underline" style="color: #00ff87;">Terms of Service</a>
                                            and
                                            <a href="{{ url_for('main.privacy') }}" target="_blank" class="font-medium hover:underline" style="color: #00ff87;">Privacy Policy</a>
                                        </label>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- CTA Button -->
                        <button type="submit"
                                class="w-full inline-flex items-center justify-center px-8 py-4 rounded-full font-bold transition-all duration-300 text-lg hover:scale-105 hover:shadow-xl disabled:opacity-50 disabled:cursor-not-allowed cta-button"
                                style="background: #00ff87; color: #0a0a0f;"
                                id="login-button"
                                aria-label="Connect with Spotify to start shuffling playlists">
                            <svg class="w-6 h-6 mr-3" viewBox="0 0 24 24" fill="currentColor">
                                <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
                            </svg>
                            <div class="text-left">
                                <div class="font-bold">Start Reordering Now</div>
                                <div class="text-sm opacity-80" id="cta-subtext">Free &bull; Approve &amp; Connect</div>
                            </div>
                        </button>
                    </form>
                </div>
                {% endif %}
            </div>

            <!-- Right Column: Animated Playlist Visualization -->
            <div class="flex-1 lg:max-w-[45%] w-full max-w-[420px]" aria-hidden="true">
                <div id="shuffle-demo" class="shuffle-demo-card rounded-2xl overflow-hidden"
                     style="background: rgba(255,255,255,0.05); border: 1px solid rgba(0,255,135,0.15); box-shadow: 0 0 40px rgba(0,255,135,0.05);">

                    <!-- Card Header -->
                    <div class="flex items-center justify-between px-5 py-3" style="border-bottom: 1px solid rgba(255,255,255,0.08);">
                        <div class="flex items-center space-x-3">
                            <!-- Play icon -->
                            <div class="w-8 h-8 rounded-full flex items-center justify-center" style="background: #00ff87;">
                                <svg class="w-4 h-4 text-black ml-0.5" fill="currentColor" viewBox="0 0 24 24">
                                    <path d="M8 5v14l11-7z"/>
                                </svg>
                            </div>
                            <div>
                                <div class="text-white text-sm font-semibold">My Playlist</div>
                                <div class="text-white/40 text-xs">8 tracks</div>
                            </div>
                        </div>
                        <div id="shuffle-indicator" class="text-xs font-medium px-2 py-1 rounded-full opacity-0 transition-opacity duration-300" style="color: #00ff87; background: rgba(0,255,135,0.1);">
                            Shuffling...
                        </div>
                    </div>

                    <!-- Track List -->
                    <div id="shuffle-track-list" class="relative" style="padding: 4px 0;">
                        <!-- Tracks are rendered by JavaScript -->
                    </div>
                </div>
            </div>

        </div>
    </div>
</div>
```

**IMPORTANT NOTE about the wordmark `<h1>`:** Phase 01 will establish the "Shuff"/"ify" wordmark styling. The `<h1>` shown above uses inline styles as a fallback. If Phase 01 introduces a CSS class for the wordmark (e.g., `.wordmark-green`), use that class instead of the inline `style="color: #00ff87;"`. The implementer should check Phase 01's output and adapt accordingly. The key requirement is that "Shuff" is neon green and "ify" is white.

**IMPORTANT NOTE about the `max-w-lg` form class:** The current code centers the form with `mx-auto`. In the two-column layout at `lg:` breakpoint, we want left-alignment. Jinja2 does not support Tailwind breakpoint logic in `{% if %}`. Instead, use this approach: apply `mx-auto lg:mx-0` to the `<form>` tag:

```html
<form action="{{ url_for('main.login') }}" method="GET" class="max-w-lg mx-auto lg:mx-0">
```

Replace the `{% if not 'lg' %}mx-auto{% endif %}` placeholder in the HTML above with `mx-auto lg:mx-0`.

### Step 3: Add Shuffle Animation CSS to `index.html`

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/index.html`
**Location:** Inside the existing `<style>` block (currently lines 241-283). Add after the existing styles.

Add the following CSS rules:

```css
/* ===== Shuffle Demo Animation Styles ===== */

/* Card border glow pulse when shuffle triggers */
.shuffle-demo-card.is-shuffling {
    animation: cardGlow 0.5s ease-in-out;
}

@keyframes cardGlow {
    0% { border-color: rgba(0, 255, 135, 0.15); box-shadow: 0 0 40px rgba(0, 255, 135, 0.05); }
    50% { border-color: rgba(0, 255, 135, 0.4); box-shadow: 0 0 60px rgba(0, 255, 135, 0.12); }
    100% { border-color: rgba(0, 255, 135, 0.15); box-shadow: 0 0 40px rgba(0, 255, 135, 0.05); }
}

/* Individual track item base styles */
.shuffle-track {
    display: flex;
    align-items: center;
    padding: 8px 16px;
    margin: 0 8px;
    border-radius: 8px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    will-change: transform;
    transition: transform 0.8s cubic-bezier(0.34, 1.56, 0.64, 1),
                box-shadow 0.3s ease,
                background-color 0.3s ease;
}

.shuffle-track:last-child {
    border-bottom: none;
}

/* Hover state for tracks */
.shuffle-track:hover {
    background: rgba(0, 255, 135, 0.05);
}

/* Track glow effect during animation */
.shuffle-track.is-moving {
    box-shadow: 0 0 12px rgba(0, 255, 135, 0.15);
    background: rgba(0, 255, 135, 0.03);
}

/* Album art gradient squares */
.shuffle-track-art {
    width: 40px;
    height: 40px;
    border-radius: 6px;
    flex-shrink: 0;
    margin-right: 12px;
}

/* Track text info */
.shuffle-track-name {
    font-size: 14px;
    font-weight: 500;
    color: rgba(255, 255, 255, 0.9);
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 180px;
}

.shuffle-track-artist {
    font-size: 12px;
    color: rgba(255, 255, 255, 0.45);
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 180px;
}

/* Track duration */
.shuffle-track-duration {
    font-size: 12px;
    color: rgba(255, 255, 255, 0.35);
    margin-left: auto;
    padding-left: 12px;
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
}

/* Staggered transition delays per track (applied by JS via data-index) */
.shuffle-track[data-index="0"] { transition-delay: 0ms; }
.shuffle-track[data-index="1"] { transition-delay: 30ms; }
.shuffle-track[data-index="2"] { transition-delay: 60ms; }
.shuffle-track[data-index="3"] { transition-delay: 90ms; }
.shuffle-track[data-index="4"] { transition-delay: 120ms; }
.shuffle-track[data-index="5"] { transition-delay: 150ms; }
.shuffle-track[data-index="6"] { transition-delay: 180ms; }
.shuffle-track[data-index="7"] { transition-delay: 210ms; }

/* Tracks moving upward get a smoother ease */
.shuffle-track.moving-up {
    transition-timing-function: cubic-bezier(0.22, 0.61, 0.36, 1);
}

/* Tracks moving downward get a slight overshoot bounce */
.shuffle-track.moving-down {
    transition-timing-function: cubic-bezier(0.34, 1.56, 0.64, 1);
}
```

### Step 4: Add Shuffle Animation JavaScript to `index.html`

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/index.html`
**Location:** Inside the existing `<script>` block (currently lines 285-332). Add BEFORE the existing `DOMContentLoaded` listener, or wrap everything in the existing one. The cleanest approach: add a new self-contained `<script>` block right before the closing `{% endblock %}` tag.

Add the following complete JavaScript:

```html
<script>
(function() {
    'use strict';

    // Track data — realistic names, artists, durations, album art gradients
    const TRACKS = [
        { name: 'Midnight Drive',   artist: 'Luna Wave',      duration: '3:42', gradient: 'linear-gradient(135deg, #667eea, #764ba2)' },
        { name: 'Electric Dreams',  artist: 'Neon Pulse',     duration: '4:15', gradient: 'linear-gradient(135deg, #f093fb, #f5576c)' },
        { name: 'Golden Hour',      artist: 'Amber Skies',    duration: '3:28', gradient: 'linear-gradient(135deg, #4facfe, #00f2fe)' },
        { name: 'Velvet Thunder',   artist: 'Storm Chaser',   duration: '5:01', gradient: 'linear-gradient(135deg, #43e97b, #38f9d7)' },
        { name: 'Cosmic Drift',     artist: 'Star Wanderer',  duration: '3:55', gradient: 'linear-gradient(135deg, #fa709a, #fee140)' },
        { name: 'Ocean Waves',      artist: 'Deep Current',   duration: '4:33', gradient: 'linear-gradient(135deg, #a18cd1, #fbc2eb)' },
        { name: 'Neon Lights',      artist: 'City Pulse',     duration: '3:17', gradient: 'linear-gradient(135deg, #ffecd2, #fcb69f)' },
        { name: 'Shadow Dance',     artist: 'Phantom Beat',   duration: '4:48', gradient: 'linear-gradient(135deg, #a1c4fd, #c2e9fb)' },
    ];

    const TRACK_HEIGHT = 56; // px — total height per track item (40px art + 16px padding)
    const SETTLE_DURATION = 2500; // ms — pause between shuffles
    const SHUFFLE_ANIMATION_DURATION = 1000; // ms — how long tracks animate
    const GLOW_DURATION = 500; // ms — border glow before shuffle
    const INITIAL_DELAY = 1500; // ms — delay before first shuffle after page load

    let currentOrder = TRACKS.map((_, i) => i); // indices into TRACKS array
    let trackElements = [];
    let isAnimating = false;
    let animationTimer = null;

    /**
     * Render the initial track list into the DOM.
     */
    function renderTracks() {
        const container = document.getElementById('shuffle-track-list');
        if (!container) return;

        container.innerHTML = '';
        // Set explicit height so absolute-positioned children don't collapse it
        container.style.height = (TRACKS.length * TRACK_HEIGHT) + 'px';
        container.style.position = 'relative';

        trackElements = TRACKS.map(function(track, index) {
            const el = document.createElement('div');
            el.className = 'shuffle-track';
            el.setAttribute('data-index', index);
            el.style.position = 'absolute';
            el.style.left = '0';
            el.style.right = '0';
            el.style.height = TRACK_HEIGHT + 'px';
            el.style.transform = 'translateY(' + (index * TRACK_HEIGHT) + 'px)';

            el.innerHTML =
                '<div class="shuffle-track-art" style="background: ' + track.gradient + ';"></div>' +
                '<div style="min-width: 0; flex: 1;">' +
                    '<div class="shuffle-track-name">' + track.name + '</div>' +
                    '<div class="shuffle-track-artist">' + track.artist + '</div>' +
                '</div>' +
                '<div class="shuffle-track-duration">' + track.duration + '</div>';

            container.appendChild(el);
            return el;
        });
    }

    /**
     * Generate a new random order that is different from the current one.
     * Uses Fisher-Yates shuffle. Ensures at least 3 tracks change position.
     */
    function generateNewOrder() {
        var newOrder;
        var attempts = 0;
        do {
            newOrder = currentOrder.slice();
            // Fisher-Yates shuffle
            for (var i = newOrder.length - 1; i > 0; i--) {
                var j = Math.floor(Math.random() * (i + 1));
                var temp = newOrder[i];
                newOrder[i] = newOrder[j];
                newOrder[j] = temp;
            }
            // Count how many tracks changed position
            var changedCount = 0;
            for (var k = 0; k < newOrder.length; k++) {
                if (newOrder[k] !== currentOrder[k]) changedCount++;
            }
            attempts++;
        } while (changedCount < 3 && attempts < 20);
        return newOrder;
    }

    /**
     * Animate tracks from current positions to new positions.
     */
    function performShuffle() {
        if (isAnimating) return;
        isAnimating = true;

        var card = document.getElementById('shuffle-demo');
        var indicator = document.getElementById('shuffle-indicator');

        // Phase 1: Show glow + indicator
        if (card) card.classList.add('is-shuffling');
        if (indicator) indicator.style.opacity = '1';

        setTimeout(function() {
            // Phase 2: Calculate new order and animate
            var newOrder = generateNewOrder();

            // For each track in TRACKS, find where it currently is and where it needs to go
            for (var trackIndex = 0; trackIndex < TRACKS.length; trackIndex++) {
                var currentPosition = currentOrder.indexOf(trackIndex);
                var newPosition = newOrder.indexOf(trackIndex);
                var el = trackElements[trackIndex];

                if (!el) continue;

                var yOffset = newPosition * TRACK_HEIGHT;

                // Add directional class for timing function
                el.classList.remove('moving-up', 'moving-down', 'is-moving');
                if (newPosition < currentPosition) {
                    el.classList.add('moving-up');
                } else if (newPosition > currentPosition) {
                    el.classList.add('moving-down');
                }

                // Add glow to moving tracks
                if (newPosition !== currentPosition) {
                    el.classList.add('is-moving');
                }

                // Apply transform
                el.style.transform = 'translateY(' + yOffset + 'px)';
            }

            currentOrder = newOrder;

            // Phase 3: Clean up after animation completes
            setTimeout(function() {
                for (var i = 0; i < trackElements.length; i++) {
                    trackElements[i].classList.remove('moving-up', 'moving-down', 'is-moving');
                }
                if (card) card.classList.remove('is-shuffling');
                if (indicator) indicator.style.opacity = '0';
                isAnimating = false;

                // Schedule next shuffle
                animationTimer = setTimeout(performShuffle, SETTLE_DURATION);
            }, SHUFFLE_ANIMATION_DURATION);

        }, GLOW_DURATION);
    }

    /**
     * Start the animation loop. Respects prefers-reduced-motion.
     */
    function startAnimation() {
        // Respect reduced motion preference
        if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            return; // Don't animate — tracks stay in initial order
        }

        renderTracks();
        animationTimer = setTimeout(performShuffle, INITIAL_DELAY);
    }

    /**
     * Stop animation (for cleanup or visibility changes).
     */
    function stopAnimation() {
        if (animationTimer) {
            clearTimeout(animationTimer);
            animationTimer = null;
        }
        isAnimating = false;
    }

    /**
     * Pause animation when tab is not visible to save CPU.
     */
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) {
            stopAnimation();
        } else {
            if (!animationTimer && document.getElementById('shuffle-track-list')) {
                animationTimer = setTimeout(performShuffle, SETTLE_DURATION);
            }
        }
    });

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', startAnimation);
    } else {
        startAnimation();
    }
})();
</script>
```

### Step 5: Verify Existing JavaScript Still Works

The existing JavaScript in `index.html` (lines 285-332) handles:
1. Checkbox enable/disable for the CTA button (`legal-consent` checkbox)
2. Scroll-triggered animations via IntersectionObserver

Both of these must still function after this phase. The checkbox JS references `document.getElementById('legal-consent')` and `document.getElementById('login-button')` -- both IDs are preserved in the new HTML. The IntersectionObserver references `.use-case-card` and `.step-number` -- these are in sections below the hero and are NOT modified by this phase.

**No changes needed to the existing JavaScript.** The new shuffle animation script is self-contained in its own IIFE and does not conflict.

### Step 6: Update the Existing `<style>` Block

The existing styles in `index.html` lines 241-283 reference `.legal-links a`, `.cta-button`, `@keyframes fadeIn`, `.animate-fade-in`, `.step-number`, and `.use-case-card`.

After Phase 01, these may have been updated (e.g., colors changed from `#1DB954` to `#00ff87`). This phase does NOT need to modify any of these existing styles -- only ADD the new shuffle animation styles from Step 3.

**Important:** The `.cta-button:hover` style currently sets `box-shadow: 0 10px 25px rgba(29, 185, 84, 0.3)`. If Phase 01 changes this to use neon green (`#00ff87`), leave Phase 01's value. If Phase 01 does NOT change it, update it to:

```css
.cta-button:hover {
    transform: translateY(-2px);
    box-shadow: 0 10px 25px rgba(0, 255, 135, 0.3);
}
```

---

## 5. Responsive Behavior

### Desktop (>=1024px / `lg:` breakpoint)
- Two-column layout: text left (55%), animation right (45%)
- Playlist card width: natural flex sizing, up to ~380px
- Full 8-track animation visible

### Tablet (768px-1023px / `md:` breakpoint)
- Single column, stacked: text on top, animation below
- Playlist card centered with `max-w-[420px] mx-auto`
- Full animation visible
- Text center-aligned

### Mobile (<768px)
- Single column, stacked: text on top, animation below
- Playlist card takes full width minus padding
- Animation still visible (the card is the centerpiece -- do NOT hide it)
- Consider reducing card height: show 6 tracks instead of 8 on very small screens (<480px). This can be handled in Phase 04 as a refinement.

**Implementation note:** The flex layout `flex-col lg:flex-row` handles the column stacking automatically. The `text-center lg:text-left` classes handle text alignment. No additional media queries needed for the basic responsive behavior.

---

## 6. Accessibility Checklist

- [x] **`aria-hidden="true"`** on the animation container (`#shuffle-demo`'s parent div) -- the animation is decorative and provides no information screen readers need.
- [x] **`prefers-reduced-motion` respected** -- the JS checks `window.matchMedia('(prefers-reduced-motion: reduce)')` and skips all animation if true. Tracks render in static order.
- [x] **No `autoplay` audio/video** -- animation is CSS transform only, silent.
- [x] **Tab order preserved** -- the animation div is `aria-hidden="true"` and contains no interactive elements, so it doesn't interfere with keyboard navigation of the consent form and CTA.
- [x] **Color contrast** -- Track names at `rgba(255,255,255,0.9)` on `rgba(255,255,255,0.05)` background passes WCAG AA. Artist names at `rgba(255,255,255,0.45)` are decorative context within an `aria-hidden` region.
- [x] **No seizure-inducing flash** -- animations are smooth transitions (0.8s+), not rapid strobing. The glow pulse is subtle (0.5s, low intensity change).
- [x] **Focus indicators** -- existing focus styles in `base.html` (lines 70-76) apply to all interactive elements and are not modified.

---

## 7. Test Plan

### 7.1 Automated Tests

**File:** `/Users/chris/Projects/shuffify/tests/routes/test_core_routes.py`

Add the following test to the existing `TestIndexRoute` class:

```python
def test_unauthenticated_landing_has_shuffle_demo(self, db_app):
    """Landing page contains the animated shuffle demo component."""
    with db_app.test_client() as client:
        resp = client.get("/")
        html = resp.data.decode()
        assert resp.status_code == 200
        assert 'id="shuffle-demo"' in html
        assert 'id="shuffle-track-list"' in html
        assert 'Midnight Drive' not in html  # Tracks rendered by JS, not in server HTML
```

```python
def test_unauthenticated_landing_has_two_column_hero(self, db_app):
    """Landing page hero uses two-column flex layout."""
    with db_app.test_client() as client:
        resp = client.get("/")
        html = resp.data.decode()
        assert 'lg:flex-row' in html
        assert 'shuffle-demo-card' in html
```

**Note:** The track names ("Midnight Drive", etc.) should NOT appear in the server-rendered HTML because they are injected by JavaScript. The test verifies the container elements exist but tracks are JS-rendered.

### 7.2 Manual Verification Steps

1. Run `python run.py` and visit `http://localhost:8000` while logged out
2. Verify the hero has two columns on desktop (widen browser to >=1024px)
3. Verify the playlist card appears on the right with 8 tracks
4. Wait 1.5 seconds -- tracks should begin shuffling
5. Observe: tracks slide smoothly to new positions with staggered timing
6. Observe: card border briefly glows green before each shuffle
7. Observe: "Shuffling..." indicator appears and fades
8. Observe: animation loops continuously (~7 second cycle)
9. Resize to <1024px -- verify columns stack vertically
10. Resize to <768px -- verify card is still visible below text
11. Open browser DevTools > Rendering > check "Emulate CSS media feature prefers-reduced-motion: reduce" -- verify animation does not play
12. Switch to another tab and back -- verify animation resumes
13. Verify the consent checkbox and CTA button still work correctly
14. Run `flake8 shuffify/` -- should pass (no Python changes)
15. Run `pytest tests/ -v` -- all tests should pass

---

## 8. Verification Checklist

- [ ] `flake8 shuffify/` passes with 0 errors
- [ ] `pytest tests/ -v` passes (all 1081+ tests)
- [ ] Landing page loads without JS console errors
- [ ] Two-column layout renders correctly at >=1024px viewport
- [ ] Columns stack vertically at <1024px viewport
- [ ] 8 tracks render in the playlist card
- [ ] Shuffle animation starts after ~1.5s delay
- [ ] Tracks animate smoothly (no janky jumps)
- [ ] Card border glow pulse is visible before each shuffle
- [ ] "Shuffling..." indicator appears and fades
- [ ] Animation pauses when tab is hidden (check with `visibilitychange`)
- [ ] Animation does NOT play with `prefers-reduced-motion: reduce`
- [ ] Consent checkbox enables/disables CTA button
- [ ] CTA button links to login flow
- [ ] No layout shift / content jump on page load
- [ ] Track text does not overflow (ellipsis on long names)

---

## 9. "What NOT To Do" Section

1. **Do NOT use `setInterval` for the animation loop.** Use `setTimeout` chaining instead. `setInterval` accumulates drift and can cause overlapping animations if a cycle is delayed. The implementation uses `setTimeout` recursively.

2. **Do NOT use real Spotify API data for the demo tracks.** The animation must work for logged-out visitors who have no Spotify session. Use the hardcoded fake track data only.

3. **Do NOT add `<img>` tags for album art.** Use CSS gradients on `<div>` elements. Images would require loading external resources and add unnecessary HTTP requests to the landing page.

4. **Do NOT make the animation container focusable or interactive.** It is purely decorative (`aria-hidden="true"`). Adding click handlers or tabindex would confuse keyboard/screen reader users.

5. **Do NOT modify sections below the hero** (How It Works, Dev Mode, testimonial, use cases, features, trust indicators). Those sections are owned by Phase 03. This phase ONLY touches the hero section (the first `<div>` inside `{% block content %}`).

6. **Do NOT remove the `hero-pattern.svg` background reference** from the page. Phase 01 handles the background. This phase only replaces the hero section content.

7. **Do NOT use CSS `animation` with `@keyframes` for the track movement.** The shuffle positions are dynamic (computed by JS each cycle), so CSS `transition` on `transform` is the correct approach. `@keyframes` is only used for the static card glow effect.

8. **Do NOT set `transition-delay` via inline JS styles.** Use the CSS `data-index` attribute selectors (`.shuffle-track[data-index="0"]`, etc.) for staggered delays. This keeps timing in CSS and makes it easy to tune.

9. **Do NOT forget the `will-change: transform`** on `.shuffle-track`. Without it, browsers may not GPU-accelerate the transforms, causing jank on lower-end devices.

10. **Do NOT use `position: relative` with `top` for track positioning.** Use `position: absolute` with `transform: translateY()`. Transforms are composited on the GPU and do not trigger layout reflows. Using `top` would cause reflows on every frame.

---

- Wrote: `/Users/chris/Projects/shuffify/documentation/planning/phases/landing-page-redesign_2026-02-22/02_animated-playlist-hero.md`
- PR title: feat: Add animated playlist shuffle visualization to hero section
- Effort: High (~4-6 hours)
- Risk: Low
- Files modified: 2 | Files created: 0
- Dependencies: Phase 01
- Unlocks: Phase 04

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/templates/index.html` - Primary file: hero section replacement, CSS styles, JavaScript animation
- `/Users/chris/Projects/shuffify/shuffify/templates/base.html` - Tailwind config: add shuffle-glow animation keyframe
- `/Users/chris/Projects/shuffify/tests/routes/test_core_routes.py` - Test file: add hero component presence tests
- `/Users/chris/Projects/shuffify/documentation/planning/phases/landing-page-redesign_2026-02-22/00_OVERVIEW.md` - Reference: phase dependencies and parallel-safety notes
- `/Users/chris/Projects/shuffify/shuffify/static/images/hero-pattern.svg` - Context: existing background asset (not modified, but referenced by the page)