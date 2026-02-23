# Landing Page Redesign — Overview

**Session:** landing-page-redesign_2026-02-22
**Date:** 2026-02-22
**Scope:** Complete visual overhaul of the Shuffify landing page (index.html)
**App URL:** https://shuffify.app
**Screenshots:** `/tmp/design-review-2026-02-22_000000/`

---

## Design Goals (from user interview)

- **Bold & energetic** — dark backgrounds, neon green accents, motion
- **Animated product demo** — show songs flipping/flying around a playlist to demonstrate reordering
- **Remove emoji branding** — replace with proper typographic/visual identity
- **Show the product in action** — visitors should immediately understand what Shuffify does
- **Exciting, not "Streamlitty"** — the page should feel like a polished product, not a prototype

---

## Current State (problems)

1. Single green gradient wall — zero visual section differentiation
2. Emoji 🎵 as brand identity — no real logo or wordmark
3. No product visualization — users can't see what the app does before signing up
4. Cards at `bg-white/10` nearly invisible against green background
5. Scroll animations broken — IntersectionObserver adds `animate-in` class but no CSS defines it
6. Mobile overflow — title clips at 375px
7. Yellow dev mode banner is the most visually prominent element
8. Only 2 features highlighted despite 7 algorithms + workshop + snapshots

---

## Phase Plan

| Phase | Title | Impact | Effort | Risk | Dependencies |
|-------|-------|--------|--------|------|--------------|
| 01 | Dark Theme + Neon Accent Foundation | High | Medium | Low | None | 🔧 IN PROGRESS |
| 02 | Animated Playlist Shuffle Hero | High | High | Low | Phase 01 |
| 03 | Section Redesign + Scroll Motion System | High | Medium | Low | Phase 01 |
| 04 | Mobile Responsive + Performance Fixes | Medium | Low | Low | Phase 01, 02, 03 |

### Dependency Graph

```
Phase 01 (Dark Theme Foundation)
  ├──> Phase 02 (Animated Hero)
  ├──> Phase 03 (Section Redesign + Motion)
  └──> Phase 04 (Mobile + Performance) — after 02 & 03
```

- **Phase 01** must complete first (establishes the color scheme everything else builds on)
- **Phases 02 and 03** can run in parallel after Phase 01 (they touch different sections of index.html — hero vs. below-hero)
- **Phase 04** runs last (responsive fixes + performance optimization for all new animations)

---

## Files Affected

| File | Phases | Notes |
|------|--------|-------|
| `shuffify/templates/index.html` | 01, 02, 03, 04 | Primary file — all phases modify this |
| `shuffify/templates/base.html` | 01, 04 | Tailwind config, global styles, animation keyframes |
| `shuffify/static/images/hero-pattern.svg` | 01 | Replace or restyle for dark theme |

**Note:** Phases 02 and 03 touch *different sections* of `index.html` (hero section vs. below-hero sections), so they can safely run in parallel without merge conflicts.

---

## Phase Documents

- [01_dark-theme-neon-foundation.md](01_dark-theme-neon-foundation.md) — Dark background, neon accents, brand wordmark, section contrast
- [02_animated-playlist-hero.md](02_animated-playlist-hero.md) — CSS/JS animated playlist demo in the hero section
- [03_section-redesign-scroll-motion.md](03_section-redesign-scroll-motion.md) — Glassmorphism cards, scroll animations, section redesign
- [04_mobile-responsive-performance.md](04_mobile-responsive-performance.md) — Mobile fixes, reduced-motion, animation performance

---

## Implementation

Run `/implement-plan documentation/planning/phases/landing-page-redesign_2026-02-22/` to start building — it will handle challenge review, branching, implementation, and PRs for each phase doc.
