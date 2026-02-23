# Phase 01: Dark Theme + Neon Accent Foundation

**Status:** ✅ COMPLETE
**Started:** 2026-02-22
**Completed:** 2026-02-22
**PR:** #89

**PR Title:** feat: Dark theme with neon green accents for landing page
**Risk:** Low
**Effort:** Medium (~2-3 hours)
**Files Modified:** 3 | Files Created: 0

| File | Action | Description |
|------|--------|-------------|
| `shuffify/templates/base.html` | Modify | Add dark theme colors and neon glow utilities to Tailwind config; update footer |
| `shuffify/templates/index.html` | Modify | Replace green gradient with dark background; restyle all cards, hero, dev banner |
| `shuffify/static/images/hero-pattern.svg` | Modify | Change stroke colors from white to neon green |

---

## 1. Context

The current landing page is a single green gradient wall (`bg-gradient-to-br from-spotify-green via-spotify-green/90 to-spotify-dark`) with cards at `bg-white/10` that are barely visible against the green background. The emoji `🎵` serves as brand identity. The yellow dev mode banner is visually dominant. None of this communicates the bold, energetic identity the product deserves.

This phase establishes the dark foundation that all subsequent phases build on. Phases 02 (animated hero), 03 (section redesign), and 04 (mobile + performance) all depend on the color scheme and Tailwind utilities defined here. Nothing else in the redesign can begin until this phase ships.

**Scope boundary:** This phase ONLY modifies `index.html`, `base.html`, and `hero-pattern.svg`. The other templates (`dashboard.html`, `workshop.html`, `settings.html`, `schedules.html`) also use the green gradient and `bg-white/10` cards, but they are NOT part of the landing page redesign. They remain unchanged. If a full app-wide dark theme is desired later, that would be a separate initiative.

---

## 2. Dependencies

- **Depends on:** None (this is the foundation phase)
- **Unlocks:** Phase 02 (Animated Playlist Shuffle Hero), Phase 03 (Section Redesign + Scroll Motion), Phase 04 (Mobile + Performance)

---

## 3. Visual Specification

### Color Palette

| Token | Hex | Usage |
|-------|-----|-------|
| `dark-base` | `#0a0a0f` | Primary page background (near-black with slight blue tint) |
| `dark-surface` | `#0f0f17` | Alternating section background for visual separation |
| `dark-card` | `#141420` | Card backgrounds (testimonial, use cases, features) |
| existing `spotify-green` | `#1DB954` | Primary accent — used for all neon glow treatments and backward compatibility |
| existing `spotify-dark` | `#191414` | Keep as-is for backward compatibility with other templates |

### Neon Glow Values

| Utility Class | CSS Property | Value |
|---------------|-------------|-------|
| `.neon-glow-sm` | `box-shadow` | `0 0 10px rgba(29, 185, 84, 0.2)` |
| `.neon-glow` | `box-shadow` | `0 0 20px rgba(29, 185, 84, 0.3)` |
| `.neon-glow-lg` | `box-shadow` | `0 0 40px rgba(29, 185, 84, 0.4)` |
| `.neon-text-glow` | `text-shadow` | `0 0 40px rgba(29, 185, 84, 0.5)` |

### Typography

- Wordmark font: System default sans-serif stack (already used by Tailwind). No external font required.
- "Shuff" in white (`#ffffff`), "ify" in neon green (`#1DB954`)
- Wordmark has `neon-text-glow` applied

---

## 4. Detailed Implementation Plan

### Step 1: Update Tailwind Config in `base.html`

**File:** `shuffify/templates/base.html`
**Lines:** 10-38 (the `tailwind.config` script block)

**BEFORE** (lines 10-38):
```html
    <script>
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
                    keyframes: {
                        fadeIn: {
                            '0%': { opacity: '0', transform: 'translateY(20px)' },
                            '100%': { opacity: '1', transform: 'translateY(0)' }
                        },
                        slideUp: {
                            '0%': { opacity: '0', transform: 'translateY(40px)' },
                            '100%': { opacity: '1', transform: 'translateY(0)' }
                        },
                        scaleIn: {
                            '0%': { opacity: '0', transform: 'scale(0.9)' },
                            '100%': { opacity: '1', transform: 'scale(1)' }
                        }
                    }
                }
            }
        }
    </script>
```

**AFTER** (replace lines 10-38):
```html
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        'spotify-green': '#1DB954',
                        'spotify-dark': '#191414',
                        'dark-base': '#0a0a0f',
                        'dark-surface': '#0f0f17',
                        'dark-card': '#141420',
                    },
                    animation: {
                        'fade-in': 'fadeIn 0.6s ease-out',
                        'slide-up': 'slideUp 0.6s ease-out',
                        'scale-in': 'scaleIn 0.4s ease-out',
                    },
                    keyframes: {
                        fadeIn: {
                            '0%': { opacity: '0', transform: 'translateY(20px)' },
                            '100%': { opacity: '1', transform: 'translateY(0)' }
                        },
                        slideUp: {
                            '0%': { opacity: '0', transform: 'translateY(40px)' },
                            '100%': { opacity: '1', transform: 'translateY(0)' }
                        },
                        scaleIn: {
                            '0%': { opacity: '0', transform: 'scale(0.9)' },
                            '100%': { opacity: '1', transform: 'scale(1)' }
                        }
                    }
                }
            }
        }
    </script>
```

**Note:** We use `spotify-green` for all neon glow treatments. No separate `neon-green` token — they would be identical values and YAGNI applies.

### Step 2: Add Neon Glow Utilities to Global `<style>` in `base.html`

**File:** `shuffify/templates/base.html`
**Lines:** 40-105 (the `<style>` block)

Add the following CSS rules immediately BEFORE the closing `</style>` tag (before line 105). Insert them after the existing `::-webkit-scrollbar-thumb:hover` rule (after line 103):

**INSERT** (new lines, before `</style>` on line 105):
```css
        /* Neon glow utilities for dark theme */
        .neon-glow-sm {
            box-shadow: 0 0 10px rgba(29, 185, 84, 0.2);
        }
        .neon-glow {
            box-shadow: 0 0 20px rgba(29, 185, 84, 0.3);
        }
        .neon-glow-lg {
            box-shadow: 0 0 40px rgba(29, 185, 84, 0.4);
        }
        .neon-text-glow {
            text-shadow: 0 0 40px rgba(29, 185, 84, 0.5);
        }
```

### Step 3: Update Footer in `base.html`

**File:** `shuffify/templates/base.html`
**Lines:** 122-127 (the `<footer>` block)

**BEFORE** (lines 122-127):
```html
    <footer class="bg-black/30 text-white py-4 backdrop-blur-sm">
        <div class="container mx-auto text-center text-sm">
            <p>Built with Flask and Spotify API</p>
            <p class="text-gray-400 mt-1">© {{ current_year }} Shuffify. All rights reserved.</p>
        </div>
    </footer>
```

**AFTER** (replace lines 122-127):
```html
    <footer class="bg-dark-base border-t border-white/10 text-white py-4">
        <div class="container mx-auto text-center text-sm">
            <p class="text-white/50">Built with Flask and Spotify API</p>
            <p class="text-white/30 mt-1">© {{ current_year }} Shuffify. All rights reserved.</p>
        </div>
    </footer>
```

**Why:** The old footer used `bg-black/30 backdrop-blur-sm`, which was designed to sit over the green gradient. On a dark background, `bg-black/30` would be nearly invisible and the blur is unnecessary. The new footer uses `bg-dark-base` with a subtle `border-t border-white/10` separator. Text colors shift from `text-gray-400` to `text-white/30` for the copyright to maintain the same dim appearance against the dark background.

**Impact on other templates:** The footer is in `base.html` and renders on ALL pages (dashboard, workshop, settings, schedules). Those pages still use the green gradient background. `bg-dark-base` (solid `#0a0a0f`) will look slightly different from the old semi-transparent `bg-black/30` when rendered over a green gradient, but the visual difference is minimal -- the footer will appear as a solid dark bar at the bottom of those pages, which is acceptable and arguably better than the current semi-transparent treatment. If the user dislikes this on authenticated pages, it can be revisited, but this keeps the footer consistent site-wide.

### Step 4: Replace Green Gradient with Dark Background in `index.html`

**File:** `shuffify/templates/index.html`
**Line:** 4

**BEFORE** (line 4):
```html
<div class="min-h-screen bg-gradient-to-br from-spotify-green via-spotify-green/90 to-spotify-dark">
```

**AFTER** (replace line 4):
```html
<div class="relative min-h-screen bg-dark-base">
```

**Why `relative`:** The dev banner (Step 9) uses `absolute top-0` positioning. Without `relative` on this container, the banner would position relative to the viewport or an unexpected ancestor. Adding it here gives the banner a reliable positioning context.

**Why:** This is the single most impactful change. The entire green gradient wall is replaced with the near-black `#0a0a0f` foundation. Every element on the page will now read against a dark background instead of a green one.

### Step 5: Update Hero Pattern Overlay Opacity in `index.html`

**File:** `shuffify/templates/index.html`
**Line:** 5

**BEFORE** (line 5):
```html
    <div class="absolute inset-0" style="background-image: url('/static/images/hero-pattern.svg'); opacity: 0.15; pointer-events: none;"></div>
```

**AFTER** (replace line 5):
```html
    <div class="absolute inset-0" style="background-image: url('/static/images/hero-pattern.svg'); opacity: 0.3; pointer-events: none;"></div>
```

**Why:** The SVG strokes are changing from `rgba(255,255,255,0.2)` to `rgba(29,185,84,0.1)` (Step 11), making them much dimmer. Increasing the overlay opacity from `0.15` to `0.3` compensates so the pattern remains subtly visible on the dark background.

### Step 6: Replace Emoji Wordmark in `index.html`

**File:** `shuffify/templates/index.html`
**Line:** 11

**BEFORE** (line 11):
```html
                <h1 class="text-6xl font-bold text-white">🎵 Shuffify</h1>
```

**AFTER** (replace line 11):
```html
                <h1 class="text-6xl font-bold neon-text-glow"><span class="text-white">Shuff</span><span class="text-spotify-green">ify</span></h1>
```

**Why:** The emoji `🎵` is replaced with a typographic wordmark. "Shuff" in white and "ify" in neon green creates a distinct visual identity. The `neon-text-glow` class adds `text-shadow: 0 0 40px rgba(29, 185, 84, 0.5)` which creates a subtle green glow behind the entire text, but it is most visible around the green "ify" portion.

### Step 7: Update Consent Card in `index.html`

**File:** `shuffify/templates/index.html`
**Line:** 25

**BEFORE** (line 25):
```html
                        <div class="consent-card bg-white/10 backdrop-blur-md rounded-2xl p-6 border border-white/20 mb-6">
```

**AFTER** (replace line 25):
```html
                        <div class="consent-card bg-dark-card rounded-2xl p-6 border border-spotify-green/20 mb-6">
```

**Why:** `bg-white/10` was nearly invisible on the green gradient. `bg-dark-card` (`#141420`) provides a visible, distinct card surface against `bg-dark-base` (`#0a0a0f`). The border changes from `border-white/20` to `border-spotify-green/20` to pick up the neon accent. `backdrop-blur-md` is removed because there is no gradient to blur through on a solid dark background.

### Step 8: Update CTA Button Hover Glow in `index.html`

**File:** `shuffify/templates/index.html`
**Lines:** 54-55 (the CTA button)

**BEFORE** (lines 54-55):
```html
                        <button type="submit" 
                                class="w-full inline-flex items-center justify-center px-8 py-4 rounded-full bg-white text-spotify-dark font-bold transition-all duration-300 text-lg hover:bg-white/90 hover:scale-105 hover:shadow-xl disabled:opacity-50 disabled:cursor-not-allowed cta-button"
```

**AFTER** (replace lines 54-55):
```html
                        <button type="submit" 
                                class="w-full inline-flex items-center justify-center px-8 py-4 rounded-full bg-white text-spotify-dark font-bold transition-all duration-300 text-lg hover:bg-white/90 hover:scale-105 disabled:opacity-50 disabled:cursor-not-allowed cta-button"
```

**Why:** Removing `hover:shadow-xl` from the Tailwind classes because the `.cta-button:hover` CSS rule (in the `<style>` block at line 254) already applies a neon-tinted shadow: `box-shadow: 0 10px 25px rgba(29, 185, 84, 0.3)`. Having both `hover:shadow-xl` (Tailwind) and the CSS rule would create conflicting shadows. The CSS rule already provides the correct neon green glow on hover.

### Step 9: Move and Restyle Dev Mode Banner in `index.html`

**File:** `shuffify/templates/index.html`

**Action 9a:** Delete the old dev banner at lines 103-118.

**DELETE** (lines 103-118):
```html
    <!-- Development Mode Notice -->
    <div class="relative py-8">
        <div class="w-full max-w-4xl mx-auto px-4">
            <div class="p-6 bg-yellow-500/20 border border-yellow-500/30 rounded-2xl">
                <div class="flex items-center">
                    <svg class="w-6 h-6 text-yellow-400 mr-4 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
                    </svg>
                    <div>
                        <h4 class="text-yellow-200 font-semibold text-lg mb-2">Development Mode</h4>
                        <p class="text-yellow-100">This app is currently in development. To check it out, please contact <a href="mailto:christophertrogers37@gmail.com" class="underline font-medium">christophertrogers37@gmail.com</a> to request user whitelisting.</p>
                    </div>
                </div>
            </div>
        </div>
    </div>
```

**Action 9b:** Insert the new slim dev banner immediately after line 4 (inside the `min-h-screen` container, before the hero pattern overlay).

**INSERT** (after line 4, before line 5):
```html
    <!-- Development Mode Notice — slim top bar -->
    <div class="absolute top-0 left-0 right-0 z-10 bg-dark-surface/90 border-b border-amber-500/20 py-2 px-4 text-center">
        <p class="text-amber-400/80 text-xs font-medium">
            Development Mode — Contact <a href="mailto:christophertrogers37@gmail.com" class="underline hover:text-amber-300">christophertrogers37@gmail.com</a> for access
        </p>
    </div>
```

**Why:** Moving the banner HTML to the top of the content block (right after the outer container div) matches its visual position. With `absolute top-0`, it renders at the top regardless, but placing it at the top of the source makes the HTML order match the visual order. The `z-10` ensures it stays above the hero pattern overlay.

**Action 9c:** Increase hero top padding to clear the banner.

**File:** `shuffify/templates/index.html`
**Line:** 8

**BEFORE** (line 8):
```html
    <div class="relative flex items-center justify-center pt-16 pb-8">
```

**AFTER** (replace line 8):
```html
    <div class="relative flex items-center justify-center pt-24 pb-8">
```

**Why:** Changed `pt-16` (64px) to `pt-24` (96px) to account for the slim dev banner at the top. The banner itself is approximately 32-36px tall, so adding 32px of extra top padding ensures the hero content clears it.

### Step 10: Update All Card Styles in `index.html`

Each card that currently uses `bg-white/10 border border-white/20` must be updated. Here is every instance:

#### 10a: Developer Testimonial Card (line 127)

**BEFORE** (line 127):
```html
                <div class="bg-white/10 backdrop-blur-md rounded-2xl p-6 border border-white/20">
```

**AFTER**:
```html
                <div class="bg-dark-card rounded-2xl p-6 border border-white/10 hover:border-spotify-green/30 transition duration-300">
```

#### 10b: Use Case Cards (lines 158, 164, 170, 176)

All four use case cards share the same pattern. Update each one identically.

**BEFORE** (each of lines 158, 164, 170, 176):
```html
                <div class="use-case-card p-6 rounded-2xl bg-white/10 border border-white/20 transform transition duration-300 hover:scale-105 hover:shadow-2xl">
```

**AFTER** (replace each):
```html
                <div class="use-case-card p-6 rounded-2xl bg-dark-card border border-white/10 hover:border-spotify-green/30 transform transition duration-300 hover:scale-105 hover:neon-glow">
```

**Why:** `hover:shadow-2xl` is replaced with `hover:neon-glow` (the custom CSS class) for the neon green glow effect. The `hover:neon-glow` works because the class is applied on hover via JavaScript/CSS -- but note that Tailwind's `hover:` prefix only works with Tailwind utilities, not custom classes. So instead, we need to handle this in CSS. See Step 13 for the CSS rule that handles this.

**CORRECTION:** Since `neon-glow` is a custom CSS class (not a Tailwind utility), `hover:neon-glow` will NOT work as a Tailwind class. Instead, use the plain class and handle hover in CSS. Replace the above AFTER with:

**AFTER** (corrected, replace each of lines 158, 164, 170, 176):
```html
                <div class="use-case-card p-6 rounded-2xl bg-dark-card border border-white/10 transform transition duration-300 hover:scale-105">
```

The hover glow and border color change will be handled by a CSS rule in Step 13.

#### 10c: Feature Cards (lines 189, 197)

Both feature cards share the same pattern.

**BEFORE** (lines 189 and 197):
```html
                <div class="p-6 rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 transform transition duration-300 hover:scale-105 hover:shadow-2xl">
```

**AFTER** (replace each):
```html
                <div class="feature-card p-6 rounded-2xl bg-dark-card border border-white/10 transform transition duration-300 hover:scale-105">
```

**Why:** Added `feature-card` class so the CSS hover rule in Step 13 can target these cards. Removed `shadow-xl`, `backdrop-blur-md`, and `hover:shadow-2xl` because the neon glow replaces the shadow effect and blur is unnecessary on a solid background.

#### 10d: Use Cases Section Background (line 153)

**BEFORE** (line 153):
```html
    <div class="relative py-12 bg-white/5">
```

**AFTER** (replace line 153):
```html
    <div class="relative py-12 bg-dark-surface">
```

**Why:** `bg-white/5` was a barely-visible tint over the green gradient. `bg-dark-surface` (`#0f0f17`) provides clear visual separation from `bg-dark-base` (`#0a0a0f`) sections.

#### 10e: Trust Indicators Border (line 209)

**BEFORE** (line 209):
```html
    <div class="relative py-12 border-t border-white/20">
```

**AFTER** (replace line 209):
```html
    <div class="relative py-12 bg-dark-surface border-t border-white/10">
```

**Why:** Added `bg-dark-surface` to create section alternation. Softened border from `border-white/20` to `border-white/10` for subtlety against the dark background.

### Step 11: Update Hero Pattern SVG

**File:** `shuffify/static/images/hero-pattern.svg`

Replace all six instances of `rgba(255,255,255,0.2)` with `rgba(29,185,84,0.1)` and both instances of `rgba(255,255,255,0.15)` with `rgba(29,185,84,0.07)`.

**BEFORE** (full file, 28 lines):
```xml
<svg width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">
    <defs>
        <pattern id="music-pattern" width="160" height="160" patternUnits="userSpaceOnUse">
            <!-- Music note 1 -->
            <path d="M40 60 v-30 l3 -3 h6 l3 3 v30 l-3 3 h-6 l-3 -3z M37 60 a6 6 0 1 0 12 0" 
                  fill="none" stroke="rgba(29,185,84,0.1)" stroke-width="2"/>
            
            <!-- Music note 2 -->
            <path d="M100 100 v-30 l3 -3 h6 l3 3 v30 l-3 3 h-6 l-3 -3z M97 100 a6 6 0 1 0 12 0" 
                  fill="none" stroke="rgba(29,185,84,0.1)" stroke-width="2"/>
            
            <!-- Music note 3 -->
            <path d="M140 40 v-30 l3 -3 h6 l3 3 v30 l-3 3 h-6 l-3 -3z M137 40 a6 6 0 1 0 12 0" 
                  fill="none" stroke="rgba(29,185,84,0.1)" stroke-width="2"/>
            
            <!-- Eighth note -->
            <path d="M60 140 v-40 l20 5 v35 l-3 3 h-6 l-3 -3z M57 140 a6 6 0 1 0 12 0" 
                  fill="none" stroke="rgba(29,185,84,0.1)" stroke-width="2"/>
            
            <!-- Additional decorative elements -->
            <circle cx="20" cy="20" r="3" 
                    fill="none" stroke="rgba(29,185,84,0.07)" stroke-width="1"/>
            <circle cx="120" cy="140" r="3" 
                    fill="none" stroke="rgba(29,185,84,0.07)" stroke-width="1"/>
        </pattern>
    </defs>
    <rect width="100%" height="100%" fill="url(#music-pattern)"/>
</svg>
```

**Summary of stroke changes:**
- Lines 6, 10, 14, 18: `rgba(255,255,255,0.2)` becomes `rgba(29,185,84,0.1)`
- Lines 22, 24: `rgba(255,255,255,0.15)` becomes `rgba(29,185,84,0.07)`

### Step 12: Update Section Heading Colors in `index.html`

Several section headings need subtle updates for the dark theme. They currently use `text-white` which is fine, but some body text needs updating.

#### 12a: "How It Works" step descriptions (lines 83, 90, 97)

These use `text-white/80` which will work well on the dark background. **No change needed.**

#### 12b: Trust indicators text (line 211)

**BEFORE** (line 211):
```html
            <div class="grid grid-cols-1 md:grid-cols-4 gap-6 text-sm text-white/70">
```

This is fine on the dark background. **No change needed.**

#### 12c: "How It Works" step number circles (lines 81, 87, 93)

**BEFORE** (each of lines 81, 87, 93):
```html
                    <div class="step-number bg-white text-spotify-green rounded-full w-12 h-12 flex items-center justify-center mx-auto mb-4 font-bold text-xl">
```

**AFTER** (replace each):
```html
                    <div class="step-number bg-spotify-green text-dark-base rounded-full w-12 h-12 flex items-center justify-center mx-auto mb-4 font-bold text-xl">
```

**Why:** White circles with green text looked good on a green background. On a dark background, neon green circles with dark text create a bolder accent that pops. The green circles tie into the neon accent theme.

### Step 13: Update the `<style>` Block in `index.html`

**File:** `shuffify/templates/index.html`
**Lines:** 241-283 (the `<style>` block at the bottom of the template)

**BEFORE** (lines 241-283):
```html
<style>
    /* Force legal links to be white */
    .legal-links a {
        color: #1DB954 !important;
    }

    /* Enhanced CTA button styles */
    .cta-button {
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }

    .cta-button:hover {
        transform: translateY(-2px);
        box-shadow: 0 10px 25px rgba(29, 185, 84, 0.3);
    }

    .cta-button:focus {
        outline: 2px solid white;
        outline-offset: 2px;
    }

    /* Animation for elements */
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(20px); }
        to { opacity: 1; transform: translateY(0); }
    }
    .animate-fade-in {
        animation: fadeIn 1s ease-out;
    }

    /* Step number animation */
    .step-number {
        transition: all 0.3s ease;
    }
    .step-number:hover {
        transform: scale(1.1);
    }

    /* Use case card hover effects */
    .use-case-card:hover {
        background: rgba(255, 255, 255, 0.15);
    }
</style>
```

**AFTER** (replace lines 241-283):
```html
<style>
    /* Force legal links to be neon green */
    .legal-links a {
        color: #1DB954 !important;
    }

    /* Enhanced CTA button styles */
    .cta-button {
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }

    .cta-button:hover {
        transform: translateY(-2px);
        box-shadow: 0 10px 25px rgba(29, 185, 84, 0.4);
    }

    .cta-button:focus {
        outline: 2px solid white;
        outline-offset: 2px;
    }

    /* Animation for elements */
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(20px); }
        to { opacity: 1; transform: translateY(0); }
    }
    .animate-fade-in {
        animation: fadeIn 1s ease-out;
    }

    /* Step number animation */
    .step-number {
        transition: all 0.3s ease;
    }
    .step-number:hover {
        transform: scale(1.1);
    }

    /* Card hover effects — neon glow on dark theme */
    .use-case-card:hover,
    .feature-card:hover {
        border-color: rgba(29, 185, 84, 0.3);
        box-shadow: 0 0 20px rgba(29, 185, 84, 0.15);
    }
</style>
```

**Changes:**
1. CTA hover shadow opacity bumped from `0.3` to `0.4` for more visible glow on dark background (line: `.cta-button:hover`)
2. `.use-case-card:hover` changed from `background: rgba(255, 255, 255, 0.15)` to `border-color` + `box-shadow` (neon glow effect)
3. Added `.feature-card:hover` with same neon glow treatment
4. Updated comment from "white" to "neon green" for clarity

---

## 5. Responsive Behavior

This phase makes no layout changes -- only color/style changes. All existing responsive behavior (grid columns, padding, text sizes) remains unchanged. The dark colors render identically at all viewport widths.

One consideration: the new absolute-positioned dev mode banner. It uses `text-center` and `text-xs` which works at all widths. At very narrow widths (< 320px), the single-line text might wrap to two lines, but the `py-2` padding accommodates this gracefully. No special responsive handling is needed.

---

## 6. Accessibility Checklist

- [ ] **Color contrast:** White text (`#ffffff`) on `dark-base` (`#0a0a0f`) = ratio 19.4:1 (exceeds WCAG AAA 7:1)
- [ ] **Color contrast:** `text-white/80` on `dark-base` = rgba(255,255,255,0.8) on #0a0a0f = effective ~15.5:1 (exceeds AAA)
- [ ] **Color contrast:** `text-white/50` on `dark-base` = rgba(255,255,255,0.5) on #0a0a0f = effective ~10:1 (exceeds AAA)
- [ ] **Color contrast:** Neon green (`#1DB954`) on `dark-base` (`#0a0a0f`) = ratio 6.5:1 (exceeds WCAG AA 4.5:1; slightly below AAA 7:1 for body text, but green is only used for accents/headings where AA applies)
- [ ] **Color contrast:** Amber text (`text-amber-400/80`) on `dark-surface` for dev banner = sufficient for small UI text
- [ ] **Focus outlines:** Existing `outline: 2px solid #1DB954` (from `base.html` line 74) remains highly visible against dark backgrounds
- [ ] **Skip-to-content link:** Existing `a.sr-only` link (base.html line 109) works unchanged
- [ ] **No information conveyed by color alone:** The wordmark split (white "Shuff" + green "ify") is decorative, not informational
- [ ] **Checkbox + label association:** Consent checkbox (line 40-47) unchanged, `for`/`id` pairing preserved

---

## 7. Test Plan

### Existing Tests (no modifications needed)

The existing test file `tests/routes/test_core_routes.py` tests route status codes (200, 302) only. It does not assert on template content. All 10 tests in that file will pass unchanged.

### Manual Verification Steps

1. **Start dev server:** `python run.py`
2. **Visit http://localhost:8000** (unauthenticated)
3. **Verify dark background:** Page should have a near-black background, NOT the green gradient
4. **Verify wordmark:** "Shuffify" should appear with "Shuff" in white and "ify" in green, with a subtle green glow behind the text
5. **Verify dev banner:** A slim amber text bar at the very top of the page, NOT a large yellow card in the middle
6. **Verify cards:** All cards (testimonial, use cases, features) should be dark (`#141420`) with subtle borders
7. **Verify card hover:** Hovering over use case and feature cards should produce a neon green glow and green border tint
8. **Verify CTA button:** White button should glow green on hover
9. **Verify hero pattern:** Subtle green music note pattern visible on the dark background
10. **Verify step numbers:** "1", "2", "3" circles should be neon green with dark text
11. **Verify section alternation:** "Perfect For" and "Trust Indicators" sections should have a slightly lighter background (`#0f0f17`) than the hero section (`#0a0a0f`)
12. **Verify footer:** Dark footer with subtle top border, dim text
13. **Verify consent checkbox:** Checkbox still functions (enable/disable CTA button)
14. **Verify mobile (375px):** Resize browser to 375px width. All content should render without horizontal overflow.
15. **Check other pages are unaffected:** Log in (if possible) and verify `/dashboard`, `/workshop` still render with the green gradient (they should -- only `index.html` was changed for the main background)

### Automated Tests

Run the full test suite to confirm no regressions:
```bash
flake8 shuffify/ && pytest tests/ -v
```

All 1081 tests should pass with zero failures.

---

## 8. Verification Checklist

- [ ] `flake8 shuffify/` passes with 0 errors
- [ ] `pytest tests/ -v` passes all 1081 tests
- [ ] Landing page (http://localhost:8000) loads without console errors
- [ ] No green gradient visible on landing page
- [ ] Wordmark reads "Shuffify" with split color treatment (no emoji)
- [ ] Dev banner is a slim bar at the top, not a large yellow card
- [ ] All 4 use case cards have dark backgrounds with neon hover glow
- [ ] Both feature cards have dark backgrounds with neon hover glow
- [ ] Hero pattern shows green-tinted music notes (look carefully -- they are subtle)
- [ ] Footer is dark with subtle text
- [ ] Dashboard/Workshop pages still work if authenticated (green gradient preserved)
- [ ] `hero-pattern.svg` contains `rgba(29,185,84,0.1)` (not `rgba(255,255,255,0.2)`)

---

## 9. What NOT To Do

1. **DO NOT change any template other than `index.html` and `base.html`.** The other templates (`dashboard.html`, `workshop.html`, `settings.html`, `schedules.html`) all use `bg-white/10` and the green gradient. Those are out of scope for this phase. Changing them would be a much larger effort and is not part of the landing page redesign.

2. **DO NOT add Google Fonts or external font CDNs.** The wordmark uses the system sans-serif font stack that Tailwind already provides. Adding a CDN would introduce a network dependency and slow page load.

3. **DO NOT use `hover:neon-glow` as a Tailwind class.** Tailwind's `hover:` variant only works with Tailwind utilities, not custom CSS classes. The neon glow hover effect is handled by the CSS rules in the `<style>` block (`.use-case-card:hover, .feature-card:hover`).

4. **DO NOT remove `backdrop-blur-md` from cards on OTHER templates.** It is only safe to remove on `index.html` because we are replacing the gradient with a solid color. Other templates still need blur for their green gradient backgrounds.

5. **DO NOT change the `spotify-green` color value.** It is used throughout the entire application for both brand identity and neon glow treatments.

6. **DO NOT move the dev banner HTML outside the main `<div class="relative min-h-screen bg-dark-base">` container.** The `absolute top-0` positioning is relative to this container (which has `position: relative`). Moving it outside would break the positioning.

8. **DO NOT delete the `cta-button` CSS rules.** The hover shadow effect is critical for the neon glow on the CTA button. It is NOT redundant with Tailwind classes -- it provides the specific neon-green-tinted shadow.

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/templates/index.html` - Primary template: replace gradient, restyle all cards, replace emoji, replace dev banner
- `/Users/chris/Projects/shuffify/shuffify/templates/base.html` - Add dark theme colors to Tailwind config, add neon glow CSS utilities, update footer
- `/Users/chris/Projects/shuffify/shuffify/static/images/hero-pattern.svg` - Change stroke colors from white to neon green
- `/Users/chris/Projects/shuffify/tests/routes/test_core_routes.py` - Verify existing tests still pass (no modifications needed)
- `/Users/chris/Projects/shuffify/documentation/planning/phases/landing-page-redesign_2026-02-22/00_OVERVIEW.md` - Reference for phase dependencies and scope

---
