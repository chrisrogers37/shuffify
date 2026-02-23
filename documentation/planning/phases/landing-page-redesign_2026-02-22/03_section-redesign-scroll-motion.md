# Phase 03: Section Redesign + Scroll Motion System

**PR Title:** Redesign below-hero sections with glassmorphism cards, timeline, and scroll-triggered animations
**Risk:** Low
**Effort:** Medium (4-6 hours)
**Files modified:** 1 (`shuffify/templates/index.html`)
**Files created:** 0
**Status:** 🔧 IN PROGRESS
**Started:** 2026-02-22
**Dependencies:** Phase 01 (Dark Theme Foundation) must be completed first
**Unlocks:** Phase 04 (Mobile Responsive + Performance Fixes)

---

## 1. Context

After Phase 01 establishes the dark background and neon green accent palette, the sections below the hero still use flat, unstyled cards with minimal visual differentiation. The "How It Works" steps are plain numbered circles with no connecting visual thread. The "Perfect For" and "Features" sections use emoji icons that look unprofessional. The scroll animation system is broken -- the IntersectionObserver on line 319-325 of `index.html` adds an `animate-in` CSS class, but no CSS rule defines what `animate-in` does, so nothing actually animates.

This phase transforms every section below the hero into a polished, visually distinct component with glassmorphism treatments, replaces all emoji with inline SVG icons, expands the Features section from 2 to 4 cards, builds a connected visual timeline for "How It Works," and implements a working scroll-triggered staggered animation system.

---

## 2. Visual Specification

### Color & Style Tokens (established by Phase 01, used here)

| Token | Value | Usage |
|-------|-------|-------|
| `bg-spotify-dark` / `#191414` | Page background | Already set by Phase 01 |
| `text-spotify-green` / `#1DB954` | Neon accent color | Icons, glows, borders |
| `bg-white/5` | Glass card background | Card surfaces |
| `border-white/10` | Glass card border | Card edges |
| `backdrop-blur-xl` | Glass blur amount | Card blur effect |
| `text-white` | Primary text | Headings |
| `text-white/70` | Secondary text | Descriptions |
| Neon glow shadow | `0 0 20px rgba(29, 185, 84, 0.3)` | Hover glows, accents |

### Section Layout Order (top to bottom, all below hero)

1. How It Works (timeline)
2. Development Mode Banner (untouched by this phase -- Phase 01 restyled it)
3. Why I Built This (testimonial)
4. Perfect For (2x2 grid)
5. Features (2x2 grid, expanded from 2 to 4)
6. Trust Indicators (4-column row)

---

## 3. Dependencies

- **Phase 01 (Dark Theme Foundation):** MUST be completed first. Phase 01 changes the page background from green gradient to dark, updates `spotify-dark` usage, and establishes the neon green accent system. All HTML in this phase assumes a dark page background is already in place.
- **Phase 02 (Animated Hero):** Independent. Phase 02 modifies lines 7-71 (hero section). Phase 03 modifies lines 73-101, 120-238, and 241-332 (everything below the hero). No overlap.

**Unlocks:** Phase 04 (Mobile Responsive + Performance Fixes)

---

### Step 0 (added during challenge round): Update Consent Card Disclosure Text

**Location:** `shuffify/templates/index.html`, line 44 (inside the consent card)

**BEFORE:**
```html
                                    <p class="text-white/80 text-sm mb-4">
                                        We use Spotify's secure OAuth to access your playlists. Your data stays with Spotify - we never store it.
                                    </p>
```

**AFTER:**
```html
                                    <p class="text-white/80 text-sm mb-4">
                                        We use Spotify's secure OAuth to access your playlists. We store your settings and playlist information to power features like snapshots, scheduling, and undo.
                                    </p>
```

**Why:** The previous text ("we never store it") was inaccurate — the app stores user profiles, settings, snapshots, activity logs, and more across 10 database models. The updated text is honest and frames storage as a benefit.

---

## 4. Detailed Implementation Plan

### Step 1: Surgically Update the `<style>` Block

**Location:** `shuffify/templates/index.html`, the `<style>` block inside `{% block content %}`

**Action:** Three surgical edits to the existing style block:

**1a. Remove old `.step-number` hover rules** (replaced by `.step-circle` in Step 2):

Delete:
```css
    /* Step number animation */
    .step-number {
        transition: all 0.3s ease;
    }
    .step-number:hover {
        transform: scale(1.1);
    }
```

**1b. Remove old `.use-case-card:hover, .feature-card:hover` rules** (replaced by `.glass-card` below):

Delete:
```css
    /* Card hover effects — neon glow on dark theme */
    .use-case-card:hover,
    .feature-card:hover {
        border-color: rgba(29, 185, 84, 0.3);
        box-shadow: 0 0 20px rgba(29, 185, 84, 0.15);
    }
```

**1c. Add new CSS classes** before the closing `</style>` tag:

```css
    /* ===== SCROLL REVEAL ANIMATION SYSTEM ===== */
    .scroll-reveal {
        opacity: 0;
        transform: translateY(30px);
        transition: opacity 0.6s cubic-bezier(0.16, 1, 0.3, 1),
                    transform 0.6s cubic-bezier(0.16, 1, 0.3, 1);
    }

    .scroll-reveal.visible {
        opacity: 1;
        transform: translateY(0);
    }

    /* Stagger delay classes for sequential card/element animation */
    .scroll-reveal-delay-1 { transition-delay: 100ms; }
    .scroll-reveal-delay-2 { transition-delay: 200ms; }
    .scroll-reveal-delay-3 { transition-delay: 300ms; }
    .scroll-reveal-delay-4 { transition-delay: 400ms; }

    /* ===== TIMELINE CONNECTOR ===== */
    .timeline-connector {
        position: absolute;
        top: 28px; /* center of the 56px (w-14) step circles */
        left: calc(16.67% + 28px); /* start after first circle center */
        right: calc(16.67% + 28px); /* end before last circle center */
        height: 2px;
        background: linear-gradient(90deg,
            #1DB954 0%,
            rgba(29, 185, 84, 0.4) 50%,
            #1DB954 100%);
        box-shadow: 0 0 8px rgba(29, 185, 84, 0.4);
    }

    /* Vertical timeline connector for mobile */
    .timeline-connector-vertical {
        display: none;
    }

    @media (max-width: 767px) {
        .timeline-connector {
            display: none;
        }
        .timeline-connector-vertical {
            display: block;
            position: absolute;
            left: 50%;
            transform: translateX(-50%);
            top: 80px; /* below first circle */
            bottom: 80px; /* above last circle */
            width: 2px;
            background: linear-gradient(180deg,
                #1DB954 0%,
                rgba(29, 185, 84, 0.4) 50%,
                #1DB954 100%);
            box-shadow: 0 0 8px rgba(29, 185, 84, 0.4);
        }
    }

    /* ===== STEP CIRCLE GLOW ===== */
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

    /* ===== GLASS CARD HOVER ===== */
    .glass-card {
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .glass-card:hover {
        transform: translateY(-4px);
        border-color: rgba(29, 185, 84, 0.4);
        box-shadow: 0 0 20px rgba(29, 185, 84, 0.15),
                    0 8px 32px rgba(0, 0, 0, 0.3);
    }

    /* ===== NEON SECTION DIVIDER ===== */
    .neon-divider {
        height: 1px;
        background: linear-gradient(90deg,
            transparent 0%,
            rgba(29, 185, 84, 0.6) 50%,
            transparent 100%);
        box-shadow: 0 0 8px rgba(29, 185, 84, 0.3);
    }

    /* ===== TRUST ICON GLOW ===== */
    .trust-icon {
        filter: drop-shadow(0 0 4px rgba(29, 185, 84, 0.4));
        transition: filter 0.3s ease;
    }
    .trust-icon:hover {
        filter: drop-shadow(0 0 8px rgba(29, 185, 84, 0.6));
    }

    /* ===== TESTIMONIAL QUOTE MARK ===== */
    .quote-decoration {
        position: absolute;
        top: -10px;
        left: 20px;
        font-size: 6rem;
        line-height: 1;
        color: rgba(29, 185, 84, 0.15);
        font-family: Georgia, serif;
        pointer-events: none;
        user-select: none;
    }

    /* ===== TESTIMONIAL LEFT ACCENT BAR ===== */
    .quote-accent-bar {
        border-left: 3px solid #1DB954;
        box-shadow: -4px 0 12px rgba(29, 185, 84, 0.3);
    }
```

---

### Step 2: Replace "How It Works" Section (lines 73-101)

**BEFORE (lines 73-101):**
```html
    <!-- How It Works Section -->
    <div class="relative pt-12 pb-12">
        <div class="w-full max-w-6xl mx-auto px-4">
            <h3 class="text-3xl font-bold text-white text-center mb-12">How It Works</h3>
            
            <div class="grid grid-cols-1 md:grid-cols-3 gap-8">
                <!-- Step 1: Connect -->
                <div class="text-center">
                    <div class="step-number bg-white text-spotify-green rounded-full w-12 h-12 flex items-center justify-center mx-auto mb-4 font-bold text-xl">1</div>
                    <h4 class="text-xl font-semibold text-white mb-3">Connect</h4>
                    <p class="text-white/80">Link your Spotify account in one click</p>
                </div>
                
                <!-- Step 2: Choose -->
                <div class="text-center">
                    <div class="step-number bg-white text-spotify-green rounded-full w-12 h-12 flex items-center justify-center mx-auto mb-4 font-bold text-xl">2</div>
                    <h4 class="text-xl font-semibold text-white mb-3">Choose</h4>
                    <p class="text-white/80">Pick your playlist and reordering algorithm</p>
                </div>
                
                <!-- Step 3: Enjoy -->
                <div class="text-center">
                    <div class="step-number bg-white text-spotify-green rounded-full w-12 h-12 flex items-center justify-center mx-auto mb-4 font-bold text-xl">3</div>
                    <h4 class="text-xl font-semibold text-white mb-3">Reorder</h4>
                    <p class="text-white/80">Enjoy a fresh playlist flow in seconds</p>
                </div>
            </div>
        </div>
    </div>
```

**AFTER:**
```html
    <!-- How It Works Section -->
    <div class="relative pt-16 pb-16">
        <div class="w-full max-w-6xl mx-auto px-4">
            <h3 class="scroll-reveal text-3xl font-bold text-white text-center mb-16">How It Works</h3>
            
            <div class="relative grid grid-cols-1 md:grid-cols-3 gap-12 md:gap-8">
                <!-- Timeline connector line (horizontal on desktop, vertical on mobile) -->
                <div class="timeline-connector" aria-hidden="true"></div>
                <div class="timeline-connector-vertical" aria-hidden="true"></div>

                <!-- Step 1: Connect -->
                <div class="scroll-reveal scroll-reveal-delay-1 text-center relative z-10">
                    <div class="step-circle bg-spotify-dark border-2 border-spotify-green text-spotify-green rounded-full w-14 h-14 flex items-center justify-center mx-auto mb-5 font-bold text-xl">
                        <!-- Link/chain icon -->
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                        </svg>
                    </div>
                    <h4 class="text-xl font-semibold text-white mb-3">Connect</h4>
                    <p class="text-white/70 max-w-xs mx-auto">Link your Spotify account in one click</p>
                </div>
                
                <!-- Step 2: Choose -->
                <div class="scroll-reveal scroll-reveal-delay-2 text-center relative z-10">
                    <div class="step-circle bg-spotify-dark border-2 border-spotify-green text-spotify-green rounded-full w-14 h-14 flex items-center justify-center mx-auto mb-5 font-bold text-xl">
                        <!-- Sliders/settings icon -->
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
                        </svg>
                    </div>
                    <h4 class="text-xl font-semibold text-white mb-3">Choose</h4>
                    <p class="text-white/70 max-w-xs mx-auto">Pick your playlist and reordering algorithm</p>
                </div>
                
                <!-- Step 3: Reorder -->
                <div class="scroll-reveal scroll-reveal-delay-3 text-center relative z-10">
                    <div class="step-circle bg-spotify-dark border-2 border-spotify-green text-spotify-green rounded-full w-14 h-14 flex items-center justify-center mx-auto mb-5 font-bold text-xl">
                        <!-- Shuffle arrows icon -->
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4" />
                        </svg>
                    </div>
                    <h4 class="text-xl font-semibold text-white mb-3">Reorder</h4>
                    <p class="text-white/70 max-w-xs mx-auto">Enjoy a fresh playlist flow in seconds</p>
                </div>
            </div>
        </div>
    </div>
```

**Why:** The numbered circles are replaced with glowing neon-bordered circles containing descriptive SVG icons. A horizontal gradient line connects them on desktop (vertical on mobile). Each step staggers in via the scroll-reveal system. The `relative z-10` on each step ensures the circles render above the connector line.

---

### Step 3: Replace "Why I Built This" Section (lines 120-150)

**BEFORE (lines 120-150):**
```html
    <!-- Developer Testimonial Section -->
    <div class="relative py-12">
        <div class="w-full max-w-4xl mx-auto px-4 text-center">
            <h3 class="text-2xl font-semibold text-white mb-8">Why I Built This</h3>
            
            <!-- Developer Testimonial -->
            <div class="max-w-3xl mx-auto">
                <div class="bg-white/10 backdrop-blur-md rounded-2xl p-6 border border-white/20">
                    <div class="flex items-start space-x-4">
                        <div class="flex-shrink-0">
                            <div class="w-12 h-12 bg-spotify-green rounded-full flex items-center justify-center">
                                <span class="text-white font-bold text-lg">C</span>
                            </div>
                        </div>
                        <div class="flex-1">
                            <blockquote class="text-white/90 text-lg leading-relaxed italic">
                                "I curate many large playlists and shuffify allows me to easily rearrange them to keep them feeling fresh, especially after I spend hours crate-digging and dumping new finds in at the bottom!"
                            </blockquote>
                            <div class="mt-4 flex items-end justify-end space-x-3">
                                <div class="text-right">
                                    <div class="text-white font-semibold">Chris</div>
                                    <div class="text-white/70 text-sm">Developer & Playlist Curator</div>
                                </div>
                                <div class="text-2xl">🎵</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
```

**AFTER:**
```html
    <!-- Developer Testimonial Section -->
    <div class="relative py-16">
        <div class="w-full max-w-4xl mx-auto px-4 text-center">
            <h3 class="scroll-reveal text-2xl font-semibold text-white mb-10">Why I Built This</h3>
            
            <!-- Developer Testimonial -->
            <div class="scroll-reveal scroll-reveal-delay-1 max-w-3xl mx-auto">
                <div class="relative bg-white/5 backdrop-blur-xl rounded-2xl p-8 border border-white/10 quote-accent-bar text-left">
                    <!-- Decorative quotation mark -->
                    <div class="quote-decoration" aria-hidden="true">&ldquo;</div>
                    
                    <div class="flex items-start space-x-5">
                        <div class="flex-shrink-0">
                            <div class="w-12 h-12 bg-spotify-green rounded-full flex items-center justify-center">
                                <span class="text-white font-bold text-lg">C</span>
                            </div>
                        </div>
                        <div class="flex-1">
                            <blockquote class="text-white/90 text-lg leading-relaxed italic relative z-10">
                                "I curate many large playlists and shuffify allows me to easily rearrange them to keep them feeling fresh, especially after I spend hours crate-digging and dumping new finds in at the bottom!"
                            </blockquote>
                            <div class="mt-5 flex items-end justify-end space-x-3">
                                <div class="text-right">
                                    <div class="text-white font-semibold">Chris</div>
                                    <div class="text-white/70 text-sm">Developer & Playlist Curator</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
```

**Why:** The card gets a deeper glassmorphism treatment (`bg-white/5`, `backdrop-blur-xl`, `border-white/10`), a neon green left accent bar via `.quote-accent-bar`, and a large decorative quotation mark positioned absolutely in the top-left. The `🎵` emoji is removed (no more emoji in the design). The `relative z-10` on the blockquote ensures text renders above the decorative quotation mark.

---

### Step 4: Replace "Perfect For" Section (lines 152-183)

**BEFORE (lines 152-183):**
```html
    <!-- Use Cases Section -->
    <div class="relative py-12 bg-white/5">
        <div class="w-full max-w-6xl mx-auto px-4">
            <h3 class="text-3xl font-bold text-white text-center mb-12">Perfect For</h3>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
                <div class="use-case-card p-6 rounded-2xl bg-white/10 border border-white/20 transform transition duration-300 hover:scale-105 hover:shadow-2xl">
                    <div class="text-3xl mb-4">🎵</div>
                    <h4 class="text-xl font-semibold text-white mb-3">Curated Collections</h4>
                    <p class="text-white/80">Keep your curated playlists fresh, especially after adding new songs.</p>
                </div>
                
                <div class="use-case-card p-6 rounded-2xl bg-white/10 border border-white/20 transform transition duration-300 hover:scale-105 hover:shadow-2xl">
                    <div class="text-3xl mb-4">🎧</div>
                    <h4 class="text-xl font-semibold text-white mb-3">Tastemaker Playlists</h4>
                    <p class="text-white/80">Perfect for tastemakers who want to mix it up and get those new adds to the top for their followers.</p>
                </div>
                
                <div class="use-case-card p-6 rounded-2xl bg-white/10 border border-white/20 transform transition duration-300 hover:scale-105 hover:shadow-2xl">
                    <div class="text-3xl mb-4">🔄</div>
                    <h4 class="text-xl font-semibold text-white mb-3">New Perspectives</h4>
                    <p class="text-white/80">Make your playlists feel new with a quick shuffle.</p>
                </div>
                
                <div class="use-case-card p-6 rounded-2xl bg-white/10 border border-white/20 transform transition duration-300 hover:scale-105 hover:shadow-2xl">
                    <div class="text-3xl mb-4">✨</div>
                    <h4 class="text-xl font-semibold text-white mb-3">Playlist Maintenance</h4>
                    <p class="text-white/80">Reorder that massive playlist that you've been meaning to update with one click.</p>
                </div>
            </div>
        </div>
    </div>
```

**AFTER:**
```html
    <!-- Use Cases Section -->
    <div class="relative py-16 bg-dark-surface">
        <div class="w-full max-w-6xl mx-auto px-4">
            <h3 class="scroll-reveal text-3xl font-bold text-white text-center mb-12">Perfect For</h3>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <!-- Card 1: Curated Collections -->
                <div class="scroll-reveal scroll-reveal-delay-1 glass-card p-6 rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10">
                    <div class="w-10 h-10 mb-4 text-spotify-green">
                        <!-- Music notes SVG -->
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2z" />
                        </svg>
                    </div>
                    <h4 class="text-xl font-semibold text-white mb-3">Curated Collections</h4>
                    <p class="text-white/70">Keep your curated playlists fresh, especially after adding new songs.</p>
                </div>
                
                <!-- Card 2: Tastemaker Playlists -->
                <div class="scroll-reveal scroll-reveal-delay-2 glass-card p-6 rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10">
                    <div class="w-10 h-10 mb-4 text-spotify-green">
                        <!-- Headphones SVG -->
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M19 14v3a2 2 0 01-2 2h-1a2 2 0 01-2-2v-1a2 2 0 012-2h3zm0 0V9a7 7 0 00-14 0v5m0 0h3a2 2 0 012 2v1a2 2 0 01-2 2H6a2 2 0 01-2-2v-3z" />
                        </svg>
                    </div>
                    <h4 class="text-xl font-semibold text-white mb-3">Tastemaker Playlists</h4>
                    <p class="text-white/70">Perfect for tastemakers who want to mix it up and get those new adds to the top for their followers.</p>
                </div>
                
                <!-- Card 3: New Perspectives -->
                <div class="scroll-reveal scroll-reveal-delay-3 glass-card p-6 rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10">
                    <div class="w-10 h-10 mb-4 text-spotify-green">
                        <!-- Refresh/rotate SVG -->
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                        </svg>
                    </div>
                    <h4 class="text-xl font-semibold text-white mb-3">New Perspectives</h4>
                    <p class="text-white/70">Make your playlists feel new with a quick shuffle.</p>
                </div>
                
                <!-- Card 4: Playlist Maintenance -->
                <div class="scroll-reveal scroll-reveal-delay-4 glass-card p-6 rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10">
                    <div class="w-10 h-10 mb-4 text-spotify-green">
                        <!-- Wrench/settings SVG -->
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                            <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        </svg>
                    </div>
                    <h4 class="text-xl font-semibold text-white mb-3">Playlist Maintenance</h4>
                    <p class="text-white/70">Reorder that massive playlist that you've been meaning to update with one click.</p>
                </div>
            </div>
        </div>
    </div>
```

**Why:** Emoji icons replaced with inline SVGs (`stroke="currentColor"` inherits `text-spotify-green`). Cards use deeper glass effect (`bg-white/5`, `backdrop-blur-xl`, `border-white/10`). The `.glass-card` class provides the lift + glow hover effect. The `bg-white/5` on the wrapping `<div>` is removed (the section no longer needs a background tint since the cards provide their own contrast against the dark page). Each card staggers in via `scroll-reveal-delay-N`.

---

### Step 5: Replace Features Section (lines 185-206)

**BEFORE (lines 185-206):**
```html
    <!-- Features Section -->
    <div class="relative py-12">
        <div class="w-full max-w-6xl mx-auto px-4">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
                <div class="p-6 rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 transform transition duration-300 hover:scale-105 hover:shadow-2xl">
                    <div class="text-4xl mb-4">🎵</div>
                    <h3 class="text-xl font-semibold mb-3 text-white">Intelligent Reordering</h3>
                    <p class="text-white/80">
                        Choose from multiple intelligent algorithms to reorder your playlist while maintaining the perfect flow.
                    </p>
                </div>
                
                <div class="p-6 rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 transform transition duration-300 hover:scale-105 hover:shadow-2xl">
                    <div class="text-4xl mb-4">↩️</div>
                    <h3 class="text-xl font-semibold mb-3 text-white">Easy Undo</h3>
                    <p class="text-white/80">
                        Not happy with the reorder? Instantly revert to your previous playlist order.
                    </p>
                </div>
            </div>
        </div>
    </div>
```

**AFTER:**
```html
    <!-- Features Section -->
    <div class="relative py-16">
        <div class="w-full max-w-6xl mx-auto px-4">
            <h3 class="scroll-reveal text-3xl font-bold text-white text-center mb-12">Powerful Features</h3>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <!-- Feature 1: 7 Shuffle Algorithms -->
                <div class="scroll-reveal scroll-reveal-delay-1 glass-card p-6 rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10">
                    <div class="w-10 h-10 mb-4 text-spotify-green">
                        <!-- Grid/algorithm SVG -->
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M4 5a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1V5zm10 0a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1V5zM4 15a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1v-4zm10 0a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" />
                        </svg>
                    </div>
                    <h4 class="text-xl font-semibold mb-3 text-white">7 Shuffle Algorithms</h4>
                    <p class="text-white/70">
                        From artist spacing to album sequencing, choose the perfect strategy to reorder your playlist.
                    </p>
                </div>
                
                <!-- Feature 2: Instant Undo -->
                <div class="scroll-reveal scroll-reveal-delay-2 glass-card p-6 rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10">
                    <div class="w-10 h-10 mb-4 text-spotify-green">
                        <!-- Undo arrow SVG -->
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M3 10h10a5 5 0 015 5v2M3 10l4-4m-4 4l4 4" />
                        </svg>
                    </div>
                    <h4 class="text-xl font-semibold mb-3 text-white">Instant Undo</h4>
                    <p class="text-white/70">
                        Not happy with the reorder? Revert to your previous playlist order with one click.
                    </p>
                </div>
                
                <!-- Feature 3: Playlist Workshop -->
                <div class="scroll-reveal scroll-reveal-delay-3 glass-card p-6 rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10">
                    <div class="w-10 h-10 mb-4 text-spotify-green">
                        <!-- Workshop/beaker SVG -->
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
                        </svg>
                    </div>
                    <h4 class="text-xl font-semibold mb-3 text-white">Playlist Workshop</h4>
                    <p class="text-white/70">
                        Merge, raid, and craft playlists from multiple sources in an interactive workspace.
                    </p>
                </div>
                
                <!-- Feature 4: Scheduled Shuffles -->
                <div class="scroll-reveal scroll-reveal-delay-4 glass-card p-6 rounded-2xl bg-white/5 backdrop-blur-xl border border-white/10">
                    <div class="w-10 h-10 mb-4 text-spotify-green">
                        <!-- Clock/schedule SVG -->
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                    </div>
                    <h4 class="text-xl font-semibold mb-3 text-white">Scheduled Shuffles</h4>
                    <p class="text-white/70">
                        Set it and forget it. Automate reordering on your schedule so your playlists stay fresh.
                    </p>
                </div>
            </div>
        </div>
    </div>
```

**Why:** Expanded from 2 to 4 feature cards to showcase the app's full capabilities (7 algorithms, undo, workshop, scheduled shuffles). All emoji replaced with inline SVGs. Same glassmorphism treatment and hover behavior as the "Perfect For" cards for visual consistency. Added a section heading ("Powerful Features") that was previously missing.

---

### Step 6: Replace Trust Indicators Section (lines 208-238)

**BEFORE (lines 208-238):**
```html
    <!-- Trust Indicators -->
    <div class="relative py-12 border-t border-white/20">
        <div class="w-full max-w-4xl mx-auto px-4 text-center">
            <div class="grid grid-cols-1 md:grid-cols-4 gap-6 text-sm text-white/70">
                <div class="flex items-center justify-center">
                    <svg class="w-5 h-5 mr-2" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z" clip-rule="evenodd"/>
                    </svg>
                    Secure OAuth
                </div>
                <div class="flex items-center justify-center">
                    <svg class="w-5 h-5 mr-2" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M2.166 4.999A11.954 11.954 0 0010 1.944 11.954 11.954 0 0017.834 5c.11.65.166 1.32.166 2.001 0 5.225-3.34 9.67-8 11.317C5.34 16.67 2 12.225 2 7c0-.682.057-1.35.166-2.001zm11.541 3.708a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/>
                    </svg>
                    No Data Stored
                </div>
                <div class="flex items-center justify-center">
                    <svg class="w-5 h-5 mr-2" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clip-rule="evenodd"/>
                    </svg>
                    Instant Results
                </div>
                <div class="flex items-center justify-center">
                    <svg class="w-5 h-5 mr-2" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M3.172 5.172a4 4 0 015.656 0L10 6.343l1.172-1.171a4 4 0 115.656 5.656L10 17.657l-6.828-6.829a4 4 0 010-5.656z" clip-rule="evenodd"/>
                    </svg>
                    Free Forever
                </div>
            </div>
        </div>
    </div>
```

**AFTER:**
```html
    <!-- Trust Indicators -->
    <div class="relative py-12">
        <!-- Neon divider line replacing border-t -->
        <div class="neon-divider mb-12" aria-hidden="true"></div>
        
        <div class="w-full max-w-4xl mx-auto px-4 text-center">
            <div class="scroll-reveal grid grid-cols-2 md:grid-cols-4 gap-6 text-sm text-white/70">
                <div class="flex items-center justify-center">
                    <svg class="trust-icon w-5 h-5 mr-2 text-spotify-green" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z" clip-rule="evenodd"/>
                    </svg>
                    Secure OAuth
                </div>
                <div class="flex items-center justify-center">
                    <svg class="trust-icon w-5 h-5 mr-2 text-spotify-green" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M2.166 4.999A11.954 11.954 0 0010 1.944 11.954 11.954 0 0017.834 5c.11.65.166 1.32.166 2.001 0 5.225-3.34 9.67-8 11.317C5.34 16.67 2 12.225 2 7c0-.682.057-1.35.166-2.001zm11.541 3.708a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/>
                    </svg>
                    No Data Stored
                </div>
                <div class="flex items-center justify-center">
                    <svg class="trust-icon w-5 h-5 mr-2 text-spotify-green" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clip-rule="evenodd"/>
                    </svg>
                    Instant Results
                </div>
                <div class="flex items-center justify-center">
                    <svg class="trust-icon w-5 h-5 mr-2 text-spotify-green" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M3.172 5.172a4 4 0 015.656 0L10 6.343l1.172-1.171a4 4 0 115.656 5.656L10 17.657l-6.828-6.829a4 4 0 010-5.656z" clip-rule="evenodd"/>
                    </svg>
                    Free Forever
                </div>
            </div>
        </div>
    </div>
```

**Why:** The `border-t border-white/20` class is replaced with the `.neon-divider` element -- a gradient line that fades from transparent to neon green and back, with a subtle glow. Icons get the `.trust-icon` class for the green color and drop-shadow glow effect. The grid changes to `grid-cols-2` on mobile (was `grid-cols-1`) so trust items pair neatly on small screens instead of stacking into a long list.

---

### Step 7: Replace the JavaScript Block (lines 285-332)

**BEFORE (lines 285-332):**
```html
<script>
    // Enable/disable login button based on checkbox and update CTA text
    document.addEventListener('DOMContentLoaded', function() {
        const checkbox = document.getElementById('legal-consent');
        const loginButton = document.getElementById('login-button');
        const ctaSubtext = document.getElementById('cta-subtext');
        
        if (checkbox && loginButton) {
            function updateButtonState() {
                const isChecked = checkbox.checked;
                loginButton.disabled = !isChecked;
                
                // Update CTA subtext based on checkbox state
                if (ctaSubtext) {
                    if (isChecked) {
                        ctaSubtext.textContent = 'Free • Ready to Start';
                        ctaSubtext.classList.add('text-spotify-green');
                    } else {
                        ctaSubtext.textContent = 'Free • Approve & Connect';
                        ctaSubtext.classList.remove('text-spotify-green');
                    }
                }
            }
            
            checkbox.addEventListener('change', updateButtonState);
            updateButtonState(); // Initial state
        }

        // Add scroll-triggered animations
        const observerOptions = {
            threshold: 0.1,
            rootMargin: '0px 0px -50px 0px'
        };

        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('animate-in');
                }
            });
        }, observerOptions);

        // Observe elements for animation
        document.querySelectorAll('.use-case-card, .step-number').forEach(el => {
            observer.observe(el);
        });
    });
</script>
```

**AFTER:**
```html
<script>
    // Enable/disable login button based on checkbox and update CTA text
    document.addEventListener('DOMContentLoaded', function() {
        const checkbox = document.getElementById('legal-consent');
        const loginButton = document.getElementById('login-button');
        const ctaSubtext = document.getElementById('cta-subtext');
        
        if (checkbox && loginButton) {
            function updateButtonState() {
                const isChecked = checkbox.checked;
                loginButton.disabled = !isChecked;
                
                // Update CTA subtext based on checkbox state
                if (ctaSubtext) {
                    if (isChecked) {
                        ctaSubtext.textContent = 'Free \u2022 Ready to Start';
                        ctaSubtext.classList.add('text-spotify-green');
                    } else {
                        ctaSubtext.textContent = 'Free \u2022 Approve & Connect';
                        ctaSubtext.classList.remove('text-spotify-green');
                    }
                }
            }
            
            checkbox.addEventListener('change', updateButtonState);
            updateButtonState(); // Initial state
        }

        // ===== SCROLL REVEAL ANIMATION SYSTEM =====
        // Observe all .scroll-reveal elements and add .visible when they enter viewport.
        // Stagger delays are handled by CSS transition-delay classes on individual elements.
        const scrollObserverOptions = {
            threshold: 0.1,
            rootMargin: '0px 0px -60px 0px'
        };

        const scrollObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('visible');
                    // Stop observing once revealed (one-shot animation)
                    scrollObserver.unobserve(entry.target);
                }
            });
        }, scrollObserverOptions);

        // Observe every element with the .scroll-reveal class
        document.querySelectorAll('.scroll-reveal').forEach(el => {
            scrollObserver.observe(el);
        });
    });
</script>
```

**Why:** The old observer targeted `.use-case-card` and `.step-number` classes (which no longer exist in the new HTML) and added `animate-in` (which had no CSS definition). The new observer targets `.scroll-reveal` elements and toggles the `.visible` class, which is defined in the CSS added in Step 1. After an element becomes visible, it is unobserved to avoid re-triggering. The `rootMargin` bottom offset is increased slightly to `-60px` so elements animate in a bit before they're fully in view, creating a snappier feel.

---

## 5. Responsive Behavior

### Desktop (768px+)
- **How It Works:** 3-column grid with horizontal timeline connector line spanning between the first and last circle centers
- **Perfect For:** 2x2 grid, 4 glassmorphism cards
- **Features:** 2x2 grid, 4 glassmorphism cards
- **Trust Indicators:** 4-column row

### Mobile (<768px)
- **How It Works:** Single column stack. The horizontal timeline connector is hidden (`display: none`). A vertical connector line appears instead, centered between the first and last step circles. Step circles have `gap-12` for spacing to accommodate the vertical line.
- **Perfect For:** Single column stack, cards at full width
- **Features:** Single column stack, cards at full width
- **Trust Indicators:** 2-column grid (changed from `grid-cols-1` to `grid-cols-2` on the default breakpoint, so trust items pair up on mobile instead of stacking into a long list)

### Timeline Connector Positioning
- Desktop horizontal line: `top: 28px` (vertically centered in the 56px / `w-14` circle), `left` and `right` calculated to start/end at circle centers using `calc(16.67% + 28px)` (one-sixth of the container width + half the circle diameter)
- Mobile vertical line: `left: 50%` with `transform: translateX(-50%)`, `top: 80px` and `bottom: 80px` to span between circles without overlapping them

---

## 6. Accessibility Checklist

- [ ] All inline SVG icons have `aria-hidden="true"` (they are decorative; the adjacent text labels provide meaning)
- [ ] The decorative quotation mark in the testimonial has `aria-hidden="true"` and `pointer-events: none`
- [ ] Timeline connector lines have `aria-hidden="true"` (purely decorative)
- [ ] The neon divider has `aria-hidden="true"` (purely decorative)
- [ ] All heading hierarchy is maintained: `h3` for section titles, `h4` for card titles (consistent with existing structure)
- [ ] Color contrast: white text on dark backgrounds exceeds WCAG AA 4.5:1 ratio. `text-white/70` (#b3b3b3 equivalent) on `#191414` has a contrast ratio of approximately 7.5:1 (passes AA)
- [ ] Scroll animations use CSS transitions (not JS-driven animations), which means `prefers-reduced-motion` can be addressed in Phase 04 by adding a media query that sets `.scroll-reveal { opacity: 1; transform: none; transition: none; }`
- [ ] Interactive card hover states are cosmetic enhancements only -- no functionality is hidden behind hover
- [ ] Focus outlines remain intact (inherited from `base.html` global focus styles)

---

## 7. Test Plan

This is a frontend-only change to a single template file. There are no backend logic changes, no new routes, no database changes, and no Python code changes.

### Manual Verification Steps

1. **Start the dev server:** `python run.py`
2. **Load the landing page** at `http://localhost:8000` while logged out
3. **Scroll down slowly** and verify:
   - Each section heading fades in and slides up as it enters the viewport
   - Cards within each section animate in with staggered delays (first card, then second, etc.)
   - Animations fire only once (scroll back up and down -- they should not re-trigger)
4. **How It Works timeline:**
   - Three circles with SVG icons (link, sliders, shuffle arrows) connected by a horizontal green gradient line
   - Circles have a green glow on hover
   - On mobile (resize to 375px): horizontal line disappears, vertical line appears between stacked circles
5. **Why I Built This:**
   - Card has a green left accent bar with subtle glow
   - Large faded quotation mark visible in top-left of card
   - No `🎵` emoji present after the attribution
6. **Perfect For:**
   - 4 cards in 2x2 grid with SVG icons (music notes, headphones, refresh arrows, gear/settings)
   - No emoji visible anywhere
   - Cards lift on hover (`translateY(-4px)`) with green border glow
7. **Features:**
   - 4 cards (not 2) with heading "Powerful Features"
   - Features shown: 7 Shuffle Algorithms, Instant Undo, Playlist Workshop, Scheduled Shuffles
   - Same hover behavior as "Perfect For" cards
8. **Trust Indicators:**
   - Neon green gradient divider line above the section (not a plain white border)
   - Icons are green with a subtle glow
   - On mobile: 2-column layout (not stacked single column)
9. **Existing functionality preserved:**
   - Consent checkbox still enables/disables the CTA button
   - CTA subtext still updates on checkbox change
   - Dev mode banner is still visible and unmodified by this phase

### Automated Tests

No new Python tests are needed. This phase does not modify any backend code.

**Existing test verification:** Run `pytest tests/ -v` to confirm no regressions. All 1081 existing tests should continue to pass since only `index.html` is modified.

### Browser Testing

- Chrome (latest) -- primary
- Firefox (latest) -- verify `backdrop-blur-xl` renders correctly
- Safari (latest) -- verify `-webkit-backdrop-filter` works (Tailwind CDN handles the prefix)

---

## 8. Verification Checklist

- [ ] `python run.py` starts without errors
- [ ] `pytest tests/ -v` -- all 1081 tests pass
- [ ] `flake8 shuffify/` -- 0 errors (no Python files changed, but verify nothing broke)
- [ ] Landing page loads at `http://localhost:8000` with no console errors
- [ ] No emoji visible anywhere on the page below the hero
- [ ] Scroll animations trigger correctly on first scroll into view
- [ ] Scroll animations do NOT re-trigger when scrolling back up and down
- [ ] Timeline connector line is horizontal on desktop, vertical on mobile
- [ ] All 4 feature cards are visible with correct content
- [ ] Glassmorphism blur effect is visible on all cards (not just transparent backgrounds)
- [ ] Card hover effects work: lift + green border glow
- [ ] Testimonial has green left accent bar and decorative quotation mark
- [ ] Neon divider line glows above Trust Indicators section
- [ ] Trust icons are green with subtle glow
- [ ] No horizontal overflow on any viewport width (check at 375px, 768px, 1024px, 1440px)

---

## 9. "What NOT To Do" Section

1. **Do NOT add `prefers-reduced-motion` handling in this phase.** That belongs in Phase 04 (Mobile + Performance). This phase only establishes the animation system. Phase 04 will add the `@media (prefers-reduced-motion: reduce)` query.

2. **Do NOT modify lines 7-71 (the hero section).** Phase 02 handles the hero. Phase 03 starts at line 73.

3. **Do NOT modify the Development Mode Banner (lines 103-118).** Phase 01 restyled it. This phase leaves it exactly as Phase 01 left it.

4. **Do NOT use Tailwind's `animate-` utilities for the scroll reveal.** Tailwind's animation utilities (`animate-fade-in`, etc.) apply immediately on page load via `@keyframes`. The scroll reveal system needs elements to start invisible and only animate when scrolled into view. That requires a CSS transition triggered by a class toggle, not a CSS animation. The `.scroll-reveal` / `.visible` pattern uses `transition` (not `animation`), which is the correct approach.

5. **Do NOT use `hover:scale-105` on the new glass cards.** The old cards used `hover:scale-105` which feels heavy and jarring. The new `.glass-card:hover` uses `translateY(-4px)` for a subtle lift instead. Do not add `hover:scale-*` classes to any of the new cards.

6. **Do NOT forget `aria-hidden="true"` on decorative SVG icons.** Every SVG icon in this phase is decorative (the adjacent text provides the meaning). Missing `aria-hidden` causes screen readers to attempt to describe the SVG paths, which is meaningless noise.

7. **Do NOT remove the `animate-fade-in` CSS class or its `@keyframes fadeIn` definition.** The hero section (line 10) uses `animate-fade-in` for its entrance animation. Removing it would break the hero fade-in.

8. **Do NOT add `scroll-reveal` to elements inside the hero section.** The hero has its own `animate-fade-in` entrance animation. Adding `scroll-reveal` would cause it to start invisible and conflict with the hero animation.

9. **Do NOT use JavaScript `requestAnimationFrame` or manual opacity manipulation for the scroll animations.** The CSS transition approach is more performant (GPU-composited), simpler to maintain, and automatically handles easing. JS-driven animations are unnecessary here.

10. **Do NOT change the `<style>` block location.** Keep it at the same position in the template (after all HTML sections, before the `<script>` block). Moving it could affect specificity order with Tailwind utilities.

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/templates/index.html` - The single file modified: all HTML sections, CSS, and JavaScript changes happen here
- `/Users/chris/Projects/shuffify/shuffify/templates/base.html` - Reference only: contains the Tailwind CDN config and global styles (not modified by this phase, but the implementer needs to know what animation utilities and color tokens are already available)
- `/Users/chris/Projects/shuffify/documentation/planning/phases/landing-page-redesign_2026-02-22/00_OVERVIEW.md` - Reference: confirms Phase 03 touches below-hero sections only, Phase 02 touches hero only, establishing that they do not conflict