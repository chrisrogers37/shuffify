# After Hours design system

Design direction: **After Hours**.

Shuffify uses graphite surfaces, warm white text, and a citron accent. Manrope
gives headings their character; DM Sans keeps controls and track information
easy to scan. The landing page introduces an asymmetric hero with a playlist
preview. Every authenticated page must use this exact theme and its shared components.
There is one product design system; page-specific rules describe composition only.

Pages are built with Flask, Jinja, and Tailwind. Library, Workshop, Routines,
Activity, Settings and every authenticated error page share the authenticated
navigation and task-view infrastructure, and use the full After Hours
presentation, as do their dynamic dialogs and feedback.
Library lists playlists as rows whose artwork and Open playlist link open the
playlist workspace; a labelled Playlist actions disclosure holds quick shuffle,
restore and visibility, and Organize holds the app preferences.
Authenticated review uses the guarded local fake Spotify harness described in
[the testing guide](../../guides/testing-harness.md).

## Tokens

Core utility recipes use the `sh` Tailwind namespace, such as `bg-sh-surface`,
`text-sh-muted`, and `border-sh-control-border`. Templates use shared
component classes and layout utilities. The sole token source is `assets/design/tokens.json`.
`assets/design/index.js` derives both Tailwind theme entries and `--sh-*` CSS
variables from it; never maintain a second set of literal values.

The adapter fails on unknown spacing aliases, invalid font families, or missing
font assets outside the self-hosted fonts directory. Shared recipes, scrollbars,
focus and reduced motion belong in `core.css`. Feature component recipes live in
`components/library.css`, `components/workspace.css`, and `components/routines.css`
inside the same core bundle and consume the same tokens. Page files compose recipes.
HTML/JS and generic CSS may use layout utilities but must not define
another palette, font or component recipe, or link a second presentation bundle.
`tests/test_design_surface_contract.py` enforces this for every template and static
builder, with no exceptions.

After Hours is the default on every page. `navigation_mode` is independent:
the landing is always public, and errors preserve their authenticated session
navigation. Authenticated pages receive `sh-shell sh-app-shell`; public pages
receive `sh-shell sh-public-shell`. The `sh-document`/`sh-shell` markers scope
shared scrollbars, focus and motion. `app-feedback.js` supplies text-only
`showNotification` feedback, and `base.js` uses the same flash recipes.

| Role | Value | Use |
| --- | --- | --- |
| Ink | `#f4f6ef` | Headings and primary text |
| Muted | `#aeb5b9` | Secondary text and captions |
| Base | `#111417` | Page background |
| Surface | `#1a1e22` | Cards and input backgrounds |
| Raised | `#22272c` | Hover and disabled surfaces |
| Border | `#3b4247` | Decorative divisions and panel edges |
| Control border | `#74818b` | Identifiable input and secondary-button boundaries |
| Accent | `#c9ed75` | Primary actions and focus rings |
| Accent ink | `#171e0b` | Text on the accent |
| Accent hover | `#d5f39c` | Primary-action hover feedback |
| Success / surface | `#9cdfca` / `#182a25` | Confirmation messages |
| Error / surface | `#ffb4ab` / `#321f20` | Error messages and invalid fields |
| Warning / surface | `#f3cd81` / `#302719` | Attention required |
| Scrim | `#00000099` | Blocking modal backdrop |

Status colors are distinct from the citron action color. Include an explicit
status label or icon; color alone does not communicate success, error, or warning.

### Type

`font-sh-display` is Manrope; `font-sh-ui` is DM Sans. Both fall back to Arial and
the system sans-serif font. Headings use weight 700 and modest negative letter
spacing. Interface text uses weights 400–700. Long paragraphs use a line height
of at least 1.5. Metadata may truncate when needed, with its full text available.

| Tailwind token | Size / line height | Typical use |
| --- | --- | --- |
| `text-sh-caption` | 12 / 18 px | Captions and metadata |
| `text-sh-body` | 14 / 22 px | Controls and body text |
| `text-sh-lead` | 18 / 30 px | Introductory text |
| `text-sh-heading` | 32 / 38 px | Page and section headings |
| `text-sh-title` | 48 / 52 px | Mobile landing heading |
| `text-sh-hero` | 76 / 80 px | Desktop landing heading |

Responsive layouts select the appropriate size rather than shrinking interface
text to fit. The public shell uses 15 px default body text.

### Space, shape, and depth

Use Tailwind's 4 px spacing rhythm. The named additions are `sh-gutter` (20 px),
`sh-gutter-wide` (40 px), `sh-panel` (24 px), and `sh-section` (88 px). The public
container is at most 1200 px wide, with 20 px side padding below 768 px and 40 px
above it. Panels use a 12 px radius, controls 8 px, and badges 6 px. Separate
Spotify artwork radii are described below.

The workspace breakpoint is `breakpoints.workspace=1024px`, exposed as
`@screen sh-workspace` and `--sh-breakpoint-workspace`. At or above it,
an open contextual task uses a 360 px `layout.aside`, a 24 px panel gap,
and a primary pane with a 560 px `layout.workspace-min`. Below it, the task takes
the primary content area. Panel padding is 16 px on narrow screens and 24 px on
desktop. Headers and footers wrap with a 12 px gap; footers remain in layout and
include safe-area padding rather than covering the final field.

`shadow-sh-panel` is `0 24px 70px rgb(0 0 0 / 0.33)`;
`shadow-sh-popover` is `0 12px 32px rgb(0 0 0 / 0.25)`. Reserve these for the hero
preview and floating content. Normal cards rely on their surface and border.
Blocking dialogs use `shadow-sh-modal` with the shared scrim token.

## Core module and ownership

`assets/design/` owns the complete visual system:

- `tokens.json`: colors, fonts, type, spacing, radii, shadows, motion, and the
  fictional preview palette. Edit each value once.
- `index.js`: dependency-free adapter consumed by the existing standalone
  Tailwind CLI. It exposes the same values as utilities and CSS variables.
- `core.css`: shell, typography, surfaces, controls, navigation, feedback,
  focus, and reduced-motion treatment.
- `pages/`: compositions for individual screens. Reuse core components and
  token variables; do not define a second palette or component recipe here.
- `templates/legal.html.j2`: static legal-page composition, using the same
  Jinja shell macros as the Flask pages.

`shuffify/templates/macros/design.html` owns the brand, skip link, header, and
footer markup. `macros/design_states.html` owns error-state presentation.
`macros/design_showcase.html` owns reusable feature walkthroughs, illustrative
panels, playlist rows, and labeled flow connectors; their component recipes
live in `core.css`. `macros/app_ui.html` owns labelled task-panel and modal
markup. `macros/forms.html` provides shared select and checkbox fields.
These components accept explicit, escaped inputs. New app compositions must
reuse the core controls and existing cards/forms/states macros without changing
their forms, IDs, data attributes, or ARIA contracts. Shared
scrollbars and interaction states belong in the core, rather than in each page.

`assets/tailwind.src.css` imports the module and emits one committed
`static/css/tailwind.css` bundle. Every page loads that bundle, including
Terms and Privacy. No other presentation bundle or palette overrides are loaded.

The legal copy lives in `assets/legal/`. `python scripts/build-public-pages.py`
generates the checked-in static documents from the shared macros without
changing their existing Flask routes. Its `--check` mode and pytest detect
source/output drift. Preserve the literal legal content fragments.

## Shared components

Every public and authenticated screen, including dialogs and dynamic builders,
uses the shared `sh-*` components.

| Class | Purpose |
| --- | --- |
| `sh-shell` | Shared page color, UI font, flex layout, visible focus, dark controls |
| `sh-public-shell`, `sh-app-shell` | Public and authenticated presentation markers |
| `sh-container` | Consistent responsive content width |
| `sh-public-nav`, `sh-nav-inner` | Public header with 68 px mobile / 84 px desktop minimum height |
| `sh-brand`, `sh-brand-mark` | Manrope wordmark with an independent shuffle mark |
| `sh-nav-links` | Wrapping links with 44 px minimum target height |
| `sh-footer`, `sh-footer-inner` | Shared public footer, stacked on mobile |
| `sh-skip-link` | Keyboard-visible skip to main content |
| `sh-toast`, `sh-toast-error`, `sh-toast-success` | Escaped flash text and status feedback |
| `sh-surface`, `sh-heading`, `sh-text-muted` | Shared panels and typography |
| `sh-button`, `sh-button-secondary` | Primary and secondary actions with disabled states |
| `sh-input` | Inputs with visible boundaries and invalid state |
| `sh-app-nav`, `sh-app-menu`, `sh-app-links` | Named authenticated destinations and native mobile disclosure |
| `sh-form-field`, `sh-field-label`, `sh-field-help`, `sh-toggle` | Labelled fields and native checkboxes with full-row targets |
| `sh-field-error`, `sh-form-error` | Persistent field and form validation feedback |
| `sh-workspace`, `sh-task-panel`, `sh-panel-*` | Contextual task layout, header/body/footer, and blocking modal |
| `sh-state` | Empty and unavailable-content panels |
| `sh-showcase`, `sh-showcase-*` | Copy-first responsive feature walkthroughs |
| `sh-example`, `sh-example-*` | Fictional panels, labeled flows, and playlist/schedule rows |
| `sh-mini-playlist`, `sh-song-*`, `sh-playlist-*`, `sh-illustration-cover*` | Shared song tiles, cover palette, and role-labeled playlist examples |

Controls are at least 44 px tall. Preserve existing ARIA labels, landmark names,
native form semantics, and keyboard behavior. Never remove a control to make the
mobile composition fit. Public navigation remains available at 360 px and may
wrap rather than hiding links.

### Library rows, Playlist actions and Organize

Library is the visual entry to playlist configuration in Workshop. "Your
playlists" lists Favorites, All playlists and a Hidden disclosure, each an
ordered list of rows named by their playlist, with counts. A row shows intact
64px artwork and the name and track count, an **Open playlist** link (artwork
opens it too), the official Spotify link as a separate destination, and a
labelled **Playlist actions** disclosure. No action appears only on hover.

Playlist actions holds the live Spotify actions: **Shuffle now** with the current
method in a quiet Change disclosure, **Restore previous saved version now** when
session history permits, and **Make private now**/**Make public now**, each
opening the shared live-action review first. **Organize** holds app preferences
that save immediately, as its note says: Favorite/Unfavorite, Hide/Show, Reset
preferences, and within Favorites and All playlists **Move up**, **Move down**
and **Move to position** (1…N), the keyboard and touch alternative to dragging.
A move keeps focus on the control used and announces the row's new position;
pointer drops take the same path.

Selecting a method or changing its fields never writes to Spotify. Only the
explicit Shuffle now action submits. A valid saved method and supported saved
parameters initialize the controls. No saved default keeps the method unselected
until the user chooses one; the initial Choose shuffle action opens those fields.
Show only that method's parameters, including Keep first tracks only where it is
supported. Empty playlists explain that tracks must be added in Workshop first.
During shuffle/undo, disable conflicting controls and guard in-app navigation.
Keep failed choices and inline feedback available. Escape closes a disclosure
and restores its summary focus, while native selects retain their own Escape.

The broader product goal is to configure a playlist and its routines, understand
the next scheduled action, and leave.

### Playlist workspace

Workshop is one playlist workspace. Its identity header names the playlist with
its track count, Spotify link and previous/next playlists, and offers **Switch
playlist** (a disclosure, hidden when closed) and **Playlist details**. The action
bar keeps **Preview shuffle** with the current method (one activation), **Shuffle
options** and **Add tracks** visible at every width. The labelled tool bar opens
Inbox (with its pending count), Archive, Routines and History; below 1024 px
Add tracks and Inbox stay visible and **Playlist tools** opens an index of every
destination. Nothing is reached by swiping or an unlabelled icon.

Each destination is a task panel beside the playlist at desktop widths and a
full-width task below them; multi-step setup (Source setup, Archive setup) uses
`task_panel(full=true)` at every width. Panels load once, keep their inputs,
results and scroll position, and return focus to their trigger.

The track list scrolls with the page. Each row is one occurrence of a track, with
a client-only key while the saved order stays a list of URIs: position, intact 40
px artwork, title and artist, the lock state in words, the duration (hidden on
phone), and one **Row actions** control. Row actions move the occurrence up,
down or to a position, cycle its lock (saved immediately, separately from the
draft) and remove exactly that occurrence; moves keep focus on the same
occurrence and announce its new position, and dragging takes the same path. The
draft status and **Review & save** sit under the header at desktop widths and
become a sticky footer below them, with its measured height as scroll padding
so focused rows are never covered.

History keeps three named concepts apart: **Playlist snapshots** (saved
playlist orders, with a read-only **View tracks**, reviewed restore and
delete), **Saved drafts** (named app-only copies of the working order that are
never sent to Spotify or saved automatically; loading one asks before
discarding unsaved changes) and **Activity** (links to the activity log and
the routine overview).

### Authenticated navigation and Settings

The five destinations are Library (`site.index`), Workshop
(`site.workshop_hub`), Routines (`site.schedules`), Activity, and Settings.
Endpoint-based `aria-current="page"` identifies the current destination without
changing route URLs. At 1024 px and wider, the links appear alongside the brand.
Below that width, a native `details`/`summary` Menu opens the labelled links in
document flow and works without JavaScript. Closed Menu links are absent from
the keyboard sequence. The visible current-destination text stays beside Menu.

Settings navigates to its dedicated page. The page groups Shuffle defaults, Snapshots &
History, Library, and Account in a 72ch column. Its form retains the POST action,
field names, CSRF token, 1–50 snapshot bounds, and native submission. Theme and
notification choices are unavailable explanatory text, with their saved values
preserved in hidden fields. Editable checkbox values retain their native
checked/unchecked behavior. Account contains the CSRF-protected POST logout.

`settings.js` enhances that same form: it validates before submitting, snapshots
FormData before disabling controls during the save, prevents duplicate submits,
and restores editing when the request finishes. Field errors are associated
with their input; unsuccessful requests remain visible in a focusable local
alert with the entered values intact. Success uses shared feedback. An explicitly
empty default algorithm clears the stored choice; an omitted algorithm in an
update leaves the stored value unchanged.

### Task panels and navigation hooks

Render `task_panel(panel_id, title, close_label='Close', modal=false)` from
`macros/app_ui.html`, providing the caller's `body` and `footer` branches. Place
contextual panels alongside `[data-panel-primary]` inside
`.sh-workspace[data-panel-workspace]`. Panels start `hidden inert`; their heading
is the accessible name and receives focus when opened. A closed panel has no
focusable descendants in the keyboard sequence.

`app-panels.js` loads for authenticated navigation and exposes:

| API | Contract |
| --- | --- |
| `AppPanels.open(id, {trigger, modal, beforeClose, onClose})` | Returns `Promise<boolean>`; opens one task and checks the active task's close hook before replacement. `trigger` defaults to the active element. |
| `AppPanels.requestClose(reason='close')` | Returns `Promise<boolean>`; requests dismissal and restores the trigger or a logical primary-content fallback when allowed. |
| `beforeClose(reason)` | May return or resolve `false` to retain the task. Reasons include `close`, `escape`, `replace`, and `suspend`. |
| `onClose(reason)` | Called after an allowed close. Consumers retain ownership of values, scroll snapshots, write state, and recovery. |
| `AppNavigation.addGuard(fn)` | Registers a synchronous or asynchronous navigation guard; returns an unsubscribe function. Returning/resolving `false` cancels navigation. |
| `AppNavigation.request(detail)` | Returns a boolean or promise for programmatic callers to await before navigating. Dispatches cancellable `app:before-navigate` first. |

A contextual task is a labelled region with no focus trap. Below 1024 px its
primary editor is hidden/inert; at desktop widths both panes remain available.
Resizing recomputes layout without rebuilding inputs. The controller preserves
the panel body's preferred scroll position across responsive clamping and modal
suspension; restored focus does not scroll that body back to the top. A blocking task uses
`role="dialog"`, `aria-modal="true"`, an inert background and contained keyboard
focus. It suspends at most one contextual panel and restores that panel and its
focus when dismissed. There are no stacked active task drawers. Escape applies
to the active task and leaves native select behavior alone. Close requests never
cancel a dispatched business write or clear recovery state; consumers decide
whether dismissal is allowed.

**Close hooks cannot re-enter panel transitions.** While an asynchronous
`beforeClose` is pending, calls to `AppPanels.open` or `requestClose` reject rather
than queue behind the hook and deadlock. A hook may await an external decision,
but must not call or await those panel methods itself. Return `false`, then let
the outer action or navigation guard open a confirmation; permit the `suspend`
reason when that confirmation should temporarily replace the contextual task.
Consumers must handle rejected transition promises. A rejected hook does not
permanently disable later transitions.

Same-tab HTTP(S) links and `form[data-app-logout]` pass through navigation guards; normal
fragment links, downloads and modified/new-tab clicks retain native behavior.
The guard detail supplies `kind`, `url`, `element`, and `event` for native actions.
The Workshop unsaved-changes check is attached to this hook, including
Settings navigation.

### Contrast and focus

Calculated WCAG relative-luminance ratios for the token pairs:

| Pair | Contrast |
| --- | --- |
| Ink on base | 16.96:1 |
| Muted on base / surface / raised | 8.90:1 / 8.07:1 / 7.25:1 |
| Accent ink on accent | 12.92:1 |
| Control border on surface | 4.20:1 |
| Focus ring on surface / raised | 12.66:1 / 11.37:1 |
| Success / error / warning on their status surfaces | 9.90:1 / 9.15:1 / 9.69:1 |

These text pairs exceed AA's 4.5:1 threshold; the control border exceeds the
3:1 threshold for identifying controls. The quieter panel border is decorative,
so do not reuse it as the only visible input boundary. The public shell uses a
3 px citron `:focus-visible` outline with a 4 px offset, including the skip link.

### Motion

Feedback transitions last 180 ms; track-row transitions last 240 ms. Use
`cubic-bezier(0.2, 0, 0, 1)`. Animate a change initiated by the user, with no
perpetual motion. Prefer background, border, and text-color feedback. Reduced
motion disables animations and transitions inside the shared shell; keep each
screen's own reduced-motion guards and reset smooth scrolling when the user
requests reduced motion.

User-triggered reorder demonstrations use the shared 720 ms `reorder` motion
token and standard easing, so the movement of individual tracks is legible.
JavaScript reads these core values; it does not introduce another motion recipe.

## Spotify content and branding

Follow [Spotify's Design & Branding Guidelines](https://developer.spotify.com/documentation/design).
The independent Shuffify mark stays separate from Spotify branding and does not
imply endorsement. Use the unmodified official white full logo on graphite
surfaces, at least 70 px wide, with clear space of half the icon height. Do not
recolor, stretch, animate, or combine it with the Shuffify wordmark.

Spotify artwork remains intact: no cropping, overlays, blurring, or distortion.
Use 4 px corners on small and medium devices and 8 px on large devices. Keep
provided metadata legible and unchanged, link it back to the Spotify service,
and attribute Spotify content with the full logo. Fictional landing-preview
tracks must be clearly identified as illustrative data.

`shuffify/static/images/spotify-logo-white.svg` is the verbatim
`Full_Logo_White_RGB.svg` entry in Spotify's official logo download. Render it
through an image element, maintaining its aspect ratio. Do not inline its SVG
markup into templates.

## Self-hosted assets

The fonts load from `/static/fonts/`, satisfying `font-src 'self'` with no runtime
font-service request and no new package dependency. Both use `font-display: swap`.
The Latin subsets cover the public interface; unsupported glyphs use the fallback.
Original SIL Open Font License notices ship beside each font.

| Asset | Size | Official source |
| --- | --- | --- |
| DM Sans variable, 400–700 | 36,980 bytes | [Google Fonts WOFF2](https://fonts.gstatic.com/s/dmsans/v17/rP2Yp2ywxg089UriI5-g4vlH9VoD8Cmcqbu0-K6z9mXg.woff2) |
| Manrope variable, 400–800 | 24,576 bytes | [Google Fonts WOFF2](https://fonts.gstatic.com/s/manrope/v20/xn7gYHE41ni1AdIRggexSvfedN4.woff2) |
| DM Sans license | 4,388 bytes | [Google Fonts OFL](https://github.com/google/fonts/blob/main/ofl/dmsans/OFL.txt) |
| Manrope license | 4,386 bytes | [Google Fonts OFL](https://github.com/google/fonts/blob/main/ofl/manrope/OFL.txt) |
| Official white Spotify full logo | 4,519 bytes | [Spotify logo ZIP](https://developer.spotify.com/images/guidelines/design/2024-spotify-full-logo.zip) |

The two font files total 61,556 bytes. The variable subset URLs come from the
[Google Fonts CSS API request](https://fonts.googleapis.com/css2?family=DM+Sans:wght@400..700&family=Manrope:wght@400..800&display=swap).

## Review and checks

Templates and public HTML have no inline style or event-handler attributes.
Template style and script blocks carry a nonce; presentation CSS and event
listeners live in static assets. Shell components keep accessible names, visible
focus, focus return, inert backgrounds for open panels, and keyboard navigation.
Layouts support a 360 px viewport and desktop widths.

Legal copy lives in `assets/legal/*.html`; the legal shell lives in
`assets/design/templates/legal.html.j2` and the shared shell macros. Public HTML
and `static/css/tailwind.css` are generated artifacts. The legal generator's
`COPYRIGHT_YEAR` is an explicit maintenance value, keeping output deterministic
across calendar years.

## Public pages

### Landing

The illustrative playlist shows five fictional tracks from positions 753–757
of an 800-track example. Its original order is rendered by Jinja and clearly
labeled. JavaScript reveals Preview Shuffle and Undo Changes, reorders those
same track elements into one illustrative order at positions 1–5, and restores
the exact original order on Undo. The demo makes no Spotify requests and has
no autoplay. Controls keep keyboard focus and state changes are announced.
Reduced motion skips the movement and changes the order immediately; without
JavaScript the original excerpt stays visible and controls stay hidden. Preserve
the GET sign-in form's `legal_consent` field, required checkbox, button ID,
and ARIA label. Consent feedback uses `landing.js` and event listeners;
its native required validation also works without JavaScript.

The landing continues below the hero with four sample walkthroughs: Raid
collects external discoveries into a review inbox/linked Raid playlist; Drip
moves a small random batch from Raid to the top of main, while archive rotation
separately exchanges eligible main tracks and the oldest archived tracks;
Schedules illustrates recurring Raid + Drip, Shuffle, and Rotation jobs. These
panels are static examples, with no simulated settings or action buttons. Real
jump links reuse the core secondary button and preserve keyboard focus. A short
quotation follows the product examples, and connection/data-use information
sits beside the consent form.

The examples show supported job types, without suggesting a configurable
dependency graph.

The landing leads with "Evolve your collection." under
the "Playlist tools for Spotify" category. It sells discovery, familiar music
in rotation and scheduled updates together. Bringing buried favorites back to
the top is a supporting label on the Shuffle/Undo example, rather than the
whole-product promise.
Public headings, jump links and example rows describe actions first; Raid,
Drip and Rotation are secondary feature labels. The archive is defined as a
second Spotify playlist for music set aside. Manual selection and gradual
additions are choices, so the copy does not imply human review gates automated
Raid + Drip.

The Drip and Rotate figures use independent demonstrations. Drip shows one
everyday playlist: three songs labeled "New" at the top, with its existing songs
below. The context label "From New finds" and a single transfer caption identify the
source and action without repeating the same playlists in a before/after grid.
Rotate shows one exchange in two stacked directional rows: saved songs return
to the everyday playlist, and everyday songs are set aside for another day.
Each row names both endpoints and the two-song example count. A quiet caption
says locked favorites stay in place. It uses the shared `playlist_row` macro
without duplicating playlists or song tiles. The Drip illustration uses the
shared mini-playlist and song-tile macros.
An explicit `cover_palette` macro keeps complete palette class names visible
to Tailwind. Both hero artwork tiles and the mini-playlists use this one core
palette recipe; sizing stays with their respective component recipes.
The benefits beside these figures are unnumbered. All shared examples use a
quiet "Sample playlists and songs" caption rather than a prominent disclosure
eyebrow. These are static diagrams.

The operation names are Raid, Drip and Rotate. Sample names describe their roles:
"Everyday mix", "New finds" and "Your archive". Raid explains scheduled collection
into a playlist the user chooses, then listening in Spotify or automatic additions
through Raid + Drip. Its duplicate filtering is a quiet note. Drip can use an
existing playlist of finds, with Raid as an optional way to fill it; songs move
out of the source and into Everyday mix. Rotate leads with bringing old favorites
back, and keeps opening-song protection, individual locks and the size limit in
supporting copy. Scheduling shows personal routines on Everyday mix, plus the
fixed Raid-first order of Raid + Drip and Raid + Shuffle. Clock-time selection
is described for daily and weekly routines; shorter intervals repeat automatically.
The Drip source is a paragraph label and the destination an h4 under the panel's
h3, so the heading outline does not nest the destination under its source.
The story section's introduction states a concrete purpose. Preserve the native
fragment IDs and the underlying application terminology.

### Error pages

Anonymous generic, 404, and 500 pages use one escaped presentation macro with
separate status tokens, a dashboard link and a retry action. Their
composition is part of the shared compiled design bundle and activated only on
logged-out responses.

### Legal pages

Terms and Privacy are static HTML served by their own routes. The shared shell
and core bundle provide a 72ch reading measure, visible focus, and responsive
typography. The static documents use no script or inline style/event attributes.
