# Phase 04: Mobile Responsive + Performance Fixes

**Status:** ✅ COMPLETE
**Started:** 2026-02-22
**Completed:** 2026-02-22
**PR Title:** Responsive layout fixes, reduced-motion support, and animation performance tuning
**Risk Level:** Low
**Effort:** Low-Medium (~2-3 hours)
**Files Modified:** 2 | Files Created: 0

| File | Action | Description |
|------|--------|-------------|
| `shuffify/templates/base.html` | Modify | Add `prefers-reduced-motion` CSS, GPU acceleration hints, Tailwind config update |
| `shuffify/templates/index.html` | Modify | Responsive classes, mobile animation adjustments, touch targets, observer cleanup |

---

## 1. Context

This phase runs AFTER Phases 01, 02, and 03. By the time this phase begins, the landing page will have:

- **Phase 01:** Dark background (`bg-[#0a0a0a]`), neon green accents (`#1DB954`), typographic wordmark replacing emoji, section contrast bands
- **Phase 02:** Two-column hero with an animated playlist visualization on the right (CSS/JS tracks shuffling in a mock playlist card)
- **Phase 03:** Glassmorphism feature/use-case cards, a working scroll-reveal animation system using `IntersectionObserver`, inline SVG icons replacing emoji, redesigned "How It Works" horizontal timeline

The current page (before any phases) already has a title overflow problem at 375px mobile width (visible in the mobile screenshot). Additionally, IntersectionObserver elements are never `unobserve()`d, and there is no `prefers-reduced-motion` support anywhere in the codebase. This phase addresses all responsive, accessibility, and performance concerns introduced by Phases 01-03.

---

## 2. Dependencies

- **Requires:** Phase 01 (dark theme foundation), Phase 02 (animated hero), Phase 03 (scroll motion system)
- **Unlocks:** None (this is the final phase)
- **Cannot run in parallel** with any other phase -- it modifies responsive behavior across all sections touched by 01-03

---

## 3. Visual Specification

### 3.1 Breakpoint Strategy

All responsive changes use Tailwind's mobile-first responsive prefixes. The three target breakpoints are:

| Breakpoint | Tailwind Prefix | Width Range | Description |
|------------|----------------|-------------|-------------|
| Mobile | (default) | < 768px | Single column, simplified animations |
| Tablet | `md:` | 768px - 1023px | Two columns (narrower), reduced animation |
| Desktop | `lg:` | >= 1024px | Full two-column hero, full animations |

### 3.2 Hero Section Responsive Behavior

**Desktop (>= 1024px):** Two columns -- text left, animated playlist right. Full animation with 6-8 tracks.

**Tablet (768px - 1023px):** Two columns, but narrower gap. Playlist animation card scales down. Track count reduced to 5 items.

**Mobile (< 768px):** Single column. Text block on top, simplified playlist animation below. Animation shows only 3 tracks with longer pause intervals (4s between shuffles instead of 2-3s). Track items have reduced padding and smaller font size.

### 3.3 Title Responsive Sizing

The title currently uses a fixed `text-6xl` which overflows at 375px.

**Before (Phase 01 will have set this):**
```html
<h1 class="text-6xl font-bold text-white tracking-tight">Shuffify</h1>
```

**After:**
```html
<h1 class="text-4xl md:text-5xl lg:text-6xl font-bold text-white tracking-tight">Shuffify</h1>
```

The subtitle similarly needs scaling:

**Before:**
```html
<h2 class="text-3xl font-semibold text-white/90">...</h2>
```

**After:**
```html
<h2 class="text-xl md:text-2xl lg:text-3xl font-semibold text-white/90">...</h2>
```

### 3.4 "How It Works" Timeline Responsive

Phase 03 introduces a horizontal timeline for "How It Works." This must convert to vertical on mobile.

**Desktop (>= 768px):** Horizontal timeline with a connecting line and 3 steps in a row.

**Mobile (< 768px):** Vertical timeline. Connecting line runs down the left side. Each step appears as a row with the step number on the left and text on the right.

### 3.5 Cards Layout

Phase 03 grids should already use `grid-cols-1 md:grid-cols-2`. This phase adds padding reduction on mobile.

### 3.6 Trust Indicators Row

The trust indicators currently use `grid-cols-1 md:grid-cols-4`. On mobile, they should stack as `grid-cols-2` (two per row) instead of one per row, to save vertical space.

---

## 4. Detailed Implementation Plan

### Step 1: Add `prefers-reduced-motion` CSS to `base.html`

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/base.html`
**Location:** Inside the existing `<style>` block (after line 104, before the closing `</style>` tag at line 105)

Add this CSS block immediately before the closing `</style>`:

```css
/* Accessibility: respect reduced motion preferences */
@media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
        scroll-behavior: auto !important;
    }
    .scroll-reveal {
        opacity: 1 !important;
        transform: none !important;
    }
    .playlist-track-item {
        animation: none !important;
        transform: none !important;
    }
}
```

**Why:** This is a critical accessibility requirement. Users who enable "reduce motion" in their OS settings (macOS: System Preferences > Accessibility > Display > Reduce motion) will get a fully static page. The `.scroll-reveal` class is the class Phase 03 uses for scroll-triggered animations. The `.playlist-track-item` class is used by Phase 02 for the animated track items.

**Also in `base.html`:** Override the `html { scroll-behavior: smooth; }` rule at line 87-89 to be governed by reduced-motion. The existing rule stays as-is; the `@media (prefers-reduced-motion: reduce)` block above handles the override with `scroll-behavior: auto !important;`.

### Step 2: Add GPU acceleration hints to `base.html`

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/base.html`
**Location:** Inside the same `<style>` block, add after the `prefers-reduced-motion` block:

```css
/* GPU acceleration for animated elements */
.scroll-reveal,
.playlist-track-item,
.use-case-card,
.feature-card {
    will-change: transform, opacity;
}

/* Remove will-change after animation completes to free GPU memory */
.scroll-reveal.visible {
    will-change: auto;
}
```

**Why:** `will-change` tells the browser to promote these elements to their own compositor layer, enabling GPU-accelerated transforms. The `.visible` class (added by Phase 03's IntersectionObserver at L648) removes `will-change` after the element has finished animating, which frees GPU memory.

### Step 3: Responsive title and subtitle in `index.html`

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/index.html`

Locate the `<h1>` tag (currently line 11). Phase 01 will have already changed the emoji to a wordmark, but the `text-6xl` sizing will remain. Change the responsive sizing classes.

**Before (what Phase 01 will have left):**
```html
<h1 class="text-6xl font-bold text-white tracking-tight">Shuffify</h1>
```

**After:**
```html
<h1 class="text-4xl md:text-5xl lg:text-6xl font-bold text-white tracking-tight">Shuffify</h1>
```

Locate the `<h2>` subtitle tag (currently line 12). Apply the same responsive pattern:

**Before:**
```html
<h2 class="text-3xl font-semibold text-white/90">Playlist Perfection</h2>
```

**After:**
```html
<h2 class="text-xl md:text-2xl lg:text-3xl font-semibold text-white/90">Playlist Perfection</h2>
```

Locate the description paragraph (currently line 13). Reduce text size on mobile:

**Before:**
```html
<p class="text-xl text-white/80 max-w-3xl mx-auto leading-relaxed">
```

**After:**
```html
<p class="text-base md:text-lg lg:text-xl text-white/80 max-w-3xl mx-auto leading-relaxed">
```

### ~~Step 4: SKIPPED~~ — Hero two-column layout already implemented by Phase 02 (flex-col lg:flex-row at L17)

### Step 5: Mobile-optimized playlist animation in `index.html`

**NOTE:** Reduced-motion JS check and visibilitychange handler already exist (Phase 02, lines 789 and 806). This step ONLY adds viewport-based track count and interval adjustments.

Locate the Phase 02 animation IIFE script (lines 662-822). Modify the constants section to detect viewport size and adjust track count/intervals:

```javascript
var isMobile = window.innerWidth < 768;
var VISIBLE_TRACK_COUNT = isMobile ? 5 : TRACKS.length;  // Show fewer tracks on mobile
var SETTLE_DURATION = isMobile ? 3500 : 2500;  // Longer pause between shuffles on mobile
```

Then in `renderTracks()`, use `VISIBLE_TRACK_COUNT` instead of `TRACKS.length` to limit rendered tracks on mobile.

**Track item responsive sizing.** The current track styles use fixed pixel values in CSS (lines 518-606). Add responsive CSS to reduce track padding/font on mobile:

```css
@media (max-width: 767px) {
    .shuffle-track { padding: 6px 12px; }
    .shuffle-track-art { width: 32px; height: 32px; margin-right: 8px; }
    .shuffle-track-name { font-size: 12px; max-width: 140px; }
    .shuffle-track-artist { font-size: 10px; max-width: 140px; }
    .shuffle-track-duration { font-size: 10px; padding-left: 8px; }
}
```

### ~~Step 6: SKIPPED~~ — How It Works vertical mobile timeline already implemented by Phase 03 (CSS media query at L420-442)

### Step 7: Card padding responsive adjustments in `index.html`

All glassmorphism cards (Phase 03) need padding reduction on mobile.

Locate every card `div` with `p-6` class in the "Perfect For", Features, and testimonial sections. Change to responsive padding:

**Before:**
```html
<div class="... p-6 ...">
```

**After:**
```html
<div class="... p-4 md:p-6 ...">
```

Apply this to all card instances. Specifically:
- "Perfect For" cards (4 cards, currently around lines 158-181)
- Features cards (2 cards, currently around lines 189-205)
- Testimonial card (currently around line 127)
- Consent card (currently around line 25)
- Dev mode notice (currently around line 106)

### ~~Step 8: SKIPPED~~ — Trust indicators already grid-cols-2 md:grid-cols-4 (L326)

### Step 9: Touch target fix for consent checkbox in `index.html`

The consent checkbox is currently `h-4 w-4` (16x16px), which fails the 44x44px minimum touch target requirement.

**Before (current lines 40-41):**
```html
<input type="checkbox" id="legal-consent" name="legal_consent" required 
       class="mt-1 h-4 w-4 text-spotify-green bg-white border-white/30 rounded focus:ring-spotify-green focus:ring-2">
```

**After:**
```html
<input type="checkbox" id="legal-consent" name="legal_consent" required 
       class="mt-1 h-5 w-5 min-h-[44px] min-w-[44px] text-spotify-green bg-white/20 border-2 border-white/40 rounded focus:ring-spotify-green focus:ring-2 cursor-pointer appearance-none checked:bg-[#1DB954] checked:border-[#1DB954] relative"
       style="padding: 10px; background-clip: content-box;">
```

**Alternative approach (recommended -- simpler and more robust):** Instead of enlarging the checkbox visually, wrap the entire checkbox + label area in a touchable container:

```html
<label for="legal-consent" class="flex items-start gap-3 cursor-pointer p-2 -m-2 rounded-lg hover:bg-white/5 transition-colors min-h-[44px]">
    <input type="checkbox" id="legal-consent" name="legal_consent" required 
           class="mt-1 h-5 w-5 flex-shrink-0 text-spotify-green bg-white/20 border-2 border-white/40 rounded focus:ring-spotify-green focus:ring-2 cursor-pointer">
    <span class="text-sm text-white/90 leading-relaxed legal-links">
        I agree to the 
        <a href="{{ url_for('main.terms') }}" target="_blank" class="text-[#1DB954] hover:underline font-medium">Terms of Service</a> 
        and 
        <a href="{{ url_for('main.privacy') }}" target="_blank" class="text-[#1DB954] hover:underline font-medium">Privacy Policy</a>
    </span>
</label>
```

**Key changes:**
- The outer `<label>` wraps both checkbox and text, so tapping anywhere on the text also toggles the checkbox
- `p-2 -m-2` adds 8px of padding (expanding the touch area) while `-m-2` compensates so layout doesn't shift
- `min-h-[44px]` ensures the entire row meets the 44px minimum touch target
- Checkbox increased from `h-4 w-4` to `h-5 w-5` (20x20px) for better visual affordance
- The old `<div class="flex items-start space-x-3">` wrapper and separate `<label>` are replaced by this single wrapping `<label>`

**Also verify the CTA button touch target.** The current CTA button has `py-4` (16px top + 16px bottom) + text line height, totaling approximately 56px height. This already meets the 44px minimum. No change needed.

### Step 10: IntersectionObserver cleanup in `base.html`

**NOTE:** index.html's observer already calls `unobserve()` (L650) and adds `.visible` class. Only base.html needs fixing.

The base.html IntersectionObserver (lines 212-218) adds `animate-fade-in` but never unobserves:

**Before (base.html lines 212-218):**
```javascript
const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            entry.target.classList.add('animate-fade-in');
        }
    });
}, observerOptions);
```

**After:**
```javascript
const observer = new IntersectionObserver((entries, obs) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            entry.target.classList.add('animate-fade-in');
            obs.unobserve(entry.target);
        }
    });
}, observerOptions);
```

### Step 11: Ensure animation properties use only compositor-safe properties

Audit all CSS animations and transitions in `index.html` to ensure they animate ONLY `transform` and `opacity`. These are the two CSS properties that can be handled entirely by the GPU compositor without triggering layout or paint.

**Existing violations to fix:**

1. **`.cta-button:hover` (line 253-256):** Currently animates `box-shadow`. Replace with a pseudo-element approach:

**Before:**
```css
.cta-button:hover {
    transform: translateY(-2px);
    box-shadow: 0 10px 25px rgba(29, 185, 84, 0.3);
}
```

**After:**
```css
.cta-button {
    position: relative;
    transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.cta-button::after {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: inherit;
    box-shadow: 0 10px 25px rgba(29, 185, 84, 0.3);
    opacity: 0;
    transition: opacity 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    pointer-events: none;
}

.cta-button:hover {
    transform: translateY(-2px);
}

.cta-button:hover::after {
    opacity: 1;
}
```

**Why:** Animating `box-shadow` directly triggers paint on every frame. By placing the shadow on a pseudo-element and animating its `opacity`, the browser only composites the layer -- no paint step needed.

2. **`.glass-card:hover` (lines 456-465):** Currently animates `transform`, `border-color`, AND `box-shadow`. Apply the same pseudo-element technique for the box-shadow:

**Before:**
```css
.glass-card {
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.glass-card:hover {
    transform: translateY(-4px);
    border-color: rgba(29, 185, 84, 0.4);
    box-shadow: 0 0 20px rgba(29, 185, 84, 0.15),
                0 8px 32px rgba(0, 0, 0, 0.3);
}
```

**After:**
```css
.glass-card {
    position: relative;
    transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1),
                border-color 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.glass-card::after {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: inherit;
    box-shadow: 0 0 20px rgba(29, 185, 84, 0.15),
                0 8px 32px rgba(0, 0, 0, 0.3);
    opacity: 0;
    transition: opacity 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    pointer-events: none;
    z-index: -1;
}
.glass-card:hover {
    transform: translateY(-4px);
    border-color: rgba(29, 185, 84, 0.4);
}
.glass-card:hover::after {
    opacity: 1;
}
```

3. **`.step-circle:hover` (lines 450-454):** Currently animates BOTH `transform: scale(1.1)` AND `box-shadow`. Apply the same pseudo-element fix:

**Before:**
```css
.step-circle {
    box-shadow: 0 0 15px rgba(29, 185, 84, 0.5),
                0 0 30px rgba(29, 185, 84, 0.2);
    transition: all 0.3s ease;
}
.step-circle:hover {
    box-shadow: 0 0 20px rgba(29, 185, 84, 0.7),
                0 0 40px rgba(29, 185, 84, 0.3);
    transform: scale(1.1);
}
```

**After:**
```css
.step-circle {
    position: relative;
    box-shadow: 0 0 15px rgba(29, 185, 84, 0.5),
                0 0 30px rgba(29, 185, 84, 0.2);
    transition: transform 0.3s ease;
}
.step-circle::after {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: inherit;
    box-shadow: 0 0 20px rgba(29, 185, 84, 0.7),
                0 0 40px rgba(29, 185, 84, 0.3);
    opacity: 0;
    transition: opacity 0.3s ease;
    pointer-events: none;
}
.step-circle:hover {
    transform: scale(1.1);
}
.step-circle:hover::after {
    opacity: 1;
}
```

---

## 5. Responsive Behavior Summary

| Element | Mobile (<768px) | Tablet (768-1023px) | Desktop (>=1024px) |
|---------|-----------------|--------------------|--------------------|
| **Title** | `text-4xl` | `text-5xl` | `text-6xl` |
| **Subtitle** | `text-xl` | `text-2xl` | `text-3xl` |
| **Description** | `text-base` | `text-lg` | `text-xl` |
| **Hero layout** | Single column (stacked) | Two columns (narrow) | Two columns (full) |
| **Playlist animation tracks** | 3 tracks, 4s interval | 5 tracks, 2.5s interval | 7 tracks, 2.5s interval |
| **Track item padding** | `p-2` | `p-3` | `p-3` |
| **Track item font** | `text-xs` / `text-[10px]` | `text-sm` / `text-xs` | `text-sm` / `text-xs` |
| **How It Works** | Vertical timeline (left line) | Horizontal timeline | Horizontal timeline |
| **Card padding** | `p-4` | `p-6` | `p-6` |
| **Trust indicators** | 2x2 grid | 1x4 row | 1x4 row |
| **Checkbox** | 20x20px, 44px touch area | 20x20px, 44px touch area | 20x20px |

---

## 6. Accessibility Checklist

- [ ] `prefers-reduced-motion` media query added to `base.html` -- disables ALL animations and transitions
- [ ] JS animation code checks `window.matchMedia('(prefers-reduced-motion: reduce)')` and shows static playlist if true
- [ ] All interactive elements (CTA button, checkbox, links) meet 44x44px minimum touch target
- [ ] Consent checkbox wrapped in `<label>` so tapping text toggles checkbox
- [ ] `scroll-behavior: smooth` overridden to `auto` when reduced motion is preferred
- [ ] Focus styles (`outline: 2px solid #1DB954`) remain unchanged and visible on all interactive elements
- [ ] `aria-label` on CTA button remains intact from current code (line 57)
- [ ] Skip-to-content link in `base.html` (line 109) remains functional
- [ ] Color contrast ratios unaffected (this phase changes no colors)

---

## 7. Test Plan

### Automated Tests

No new Python tests needed -- this phase is purely frontend CSS/JS. The existing test suite (`pytest tests/ -v`) must continue to pass with zero failures, as template rendering tests verify the templates load without syntax errors.

### Manual Verification Steps

1. **375px mobile viewport test:**
   - Open Chrome DevTools > Toggle Device Toolbar
   - Set viewport to 375x667 (iPhone SE)
   - Verify title "Shuffify" does NOT overflow or clip
   - Verify all text is readable, no horizontal scroll appears
   - Verify playlist animation shows exactly 3 tracks
   - Verify "How It Works" displays as vertical timeline

2. **768px tablet viewport test:**
   - Set viewport to 768x1024 (iPad)
   - Verify two-column hero layout appears
   - Verify playlist animation shows 5 tracks
   - Verify "How It Works" displays as horizontal timeline

3. **1024px+ desktop viewport test:**
   - Set viewport to 1440x900
   - Verify full two-column hero with 7-track animation
   - Verify all cards at `p-6` padding

4. **Reduced motion test:**
   - In Chrome DevTools: Rendering tab > Emulate CSS media feature > `prefers-reduced-motion: reduce`
   - Verify ALL animations are disabled (no scroll reveals, no playlist shuffling, no hover transforms)
   - Verify playlist card shows static tracks (not hidden)
   - Verify page is fully usable and all content visible

5. **Touch target test:**
   - On mobile viewport, verify the consent checkbox area is tappable across the full label text
   - Verify CTA button is easily tappable

6. **Performance test:**
   - Open Chrome DevTools > Performance tab
   - Record a 5-second scroll through the page
   - Verify no layout thrashing (no forced reflows in the flame chart)
   - Verify animations stay above 30fps on mobile viewport
   - Check that `will-change` is removed from elements after they animate (inspect computed styles)

### Regression Checks

- Run `flake8 shuffify/` -- must pass (templates are not linted, but Python files must remain clean)
- Run `pytest tests/ -v` -- all 1081+ tests must pass
- Verify the page renders identically on desktop after changes (no visual regression at 1440px)

---

## 8. Verification Checklist

- [ ] `flake8 shuffify/` passes with 0 errors
- [ ] `pytest tests/ -v` passes with all tests green
- [ ] Title does not overflow at 375px viewport width
- [ ] Playlist animation runs with 3 tracks on mobile, 5 on tablet, 7 on desktop
- [ ] Playlist animation is static when `prefers-reduced-motion: reduce` is active
- [ ] All animations are disabled when `prefers-reduced-motion: reduce` is active
- [ ] "How It Works" renders as vertical timeline on mobile (<768px)
- [ ] "How It Works" renders as horizontal timeline on tablet+ (>=768px)
- [ ] Consent checkbox touch area meets 44x44px minimum
- [ ] Cards use `p-4` on mobile and `p-6` on tablet+
- [ ] Trust indicators render as 2x2 grid on mobile
- [ ] IntersectionObserver calls `unobserve()` after each element animates in
- [ ] No `box-shadow` properties are directly animated (all use opacity pseudo-element technique)
- [ ] `will-change` is set on animated elements and removed via `.revealed` class after animation
- [ ] No horizontal scrollbar appears at any viewport width down to 320px
- [ ] CTA button focus ring is clearly visible on keyboard navigation

---

## 9. Performance Audit Checklist

These are specific performance concerns the implementer must verify:

- [ ] **No layout thrashing in animation JS:** All DOM reads (e.g., `getBoundingClientRect()`, `offsetHeight`) must be batched before any DOM writes (e.g., `classList.add()`, style changes). Never interleave reads and writes.
- [ ] **CSS animations use only `transform` and `opacity`:** These are the only two properties that the browser can animate on the compositor thread without triggering layout or paint. Verify no animation touches `width`, `height`, `top`, `left`, `margin`, `padding`, `box-shadow`, or `border`.
- [ ] **IntersectionObserver `unobserve()`:** Every element is unobserved after it has animated in. This prevents the observer callback from firing repeatedly as the user scrolls.
- [ ] **`will-change` lifecycle:** `will-change: transform, opacity` is set BEFORE animation starts and removed (set to `auto`) AFTER animation completes. Permanent `will-change` wastes GPU memory.
- [ ] **Animation interval cleanup:** If the playlist shuffle uses `setInterval`, ensure it is cleared when the tab is not visible (use `document.visibilitychange` event) and when reduced motion is detected.
- [ ] **No forced synchronous layouts:** Never read layout properties (like `offsetWidth`) immediately after modifying the DOM in the animation loop.

---

## 10. "What NOT To Do" Section

1. **Do NOT use `@media (max-width: 767px)` in custom CSS.** Tailwind uses a mobile-first approach. Use the default (no prefix) for mobile styles and `md:` / `lg:` prefixes for larger screens. Writing max-width media queries fights against Tailwind's paradigm and creates specificity conflicts.

2. **Do NOT hide the playlist animation on mobile.** Reducing track count and slowing the interval is the correct approach. Hiding it entirely removes the product demo that is the whole point of Phase 02.

3. **Do NOT add `will-change` to every element on the page.** Only add it to elements that actually animate. Over-using `will-change` consumes GPU memory and can actually degrade performance by creating too many compositor layers.

4. **Do NOT animate `box-shadow` directly.** It triggers paint on every frame. Always use the pseudo-element opacity technique described in Step 11.

5. **Do NOT use `transform: translate3d(0,0,0)` as a "GPU hack."** Use `will-change` instead -- it is the standards-based way to hint GPU compositing. The `translate3d` hack is obsolete and adds unnecessary transform stack entries.

6. **Do NOT remove the `prefers-reduced-motion` block "temporarily" for testing.** It must ship in the final PR. This is not optional -- it is an accessibility requirement.

7. **Do NOT change the IntersectionObserver `threshold` or `rootMargin` values.** These were set in Phase 03 and control when scroll animations trigger. This phase only adds `unobserve()` cleanup -- do not alter the trigger conditions.

8. **Do NOT use `display: none` to hide the mobile vertical timeline line on desktop.** Use Tailwind's `md:hidden` / `hidden md:block` pattern instead of writing custom CSS for this.

9. **Do NOT add responsive breakpoints to `tailwind.config` in `base.html`.** Tailwind CDN already includes `sm:` (640px), `md:` (768px), `lg:` (1024px), `xl:` (1280px), and `2xl:` (1536px). No custom breakpoints are needed.

10. **Do NOT forget to test at 320px width.** While 375px is the primary mobile target, some older devices are 320px wide. The page should not have horizontal scrollbar at 320px, even if it looks slightly cramped.

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/templates/index.html` - Primary file: all responsive classes, touch targets, observer cleanup, mobile animation config
- `/Users/chris/Projects/shuffify/shuffify/templates/base.html` - Global styles: prefers-reduced-motion CSS, will-change rules, GPU acceleration hints
- `/Users/chris/Projects/shuffify/documentation/planning/phases/landing-page-redesign_2026-02-22/00_OVERVIEW.md` - Overview context: dependency graph, phase relationships
- `/Users/chris/Projects/shuffify/shuffify/static/images/hero-pattern.svg` - Static asset reference: verify pattern renders correctly on dark background at all sizes

---
