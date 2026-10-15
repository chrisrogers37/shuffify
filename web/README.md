# Shuffify web foundation

The React/Vite application uses the existing After Hours design. It supplies shared shells, errors, controls, task behavior, authentication and API plumbing. The normal production router currently renders the shared 404 page; it does not substitute the component review for a product page.

Node 24 is required. Install and verify from `web/`:

```sh
npm ci
npm run theme:check
npm run schemas:check
npm run typecheck
npm test
npm run build
npm run test:browser
```

The build emits static HTML, JavaScript, CSS, local fonts and images to `dist/`. `typecheck` checks the application, wire contracts and build/browser configuration separately. Playwright builds the fixture app, starts a static preview with the production CSP, and exercises Chromium at 360×800 and 1440×1000. Install its matching browser once with `npx playwright install chromium` if necessary. Screenshots are written to `evidence/`.

## Fixture mode

```sh
npm run dev -- --mode fixture
```

Open `/__foundation/app`. The signed-in review renders from the same recorded JSON used by the Python and TypeScript contract checks. `/__foundation/public` and `/__foundation/errors/404`, `/500`, `/503` exercise the other shells. Select a scenario in the review or use `?scenario=empty`, `error`, `partial`, `recovery`, `rollback`, `busy`, `rate-limit`, `signed-out`, `signed-out-notice`, or `session-error`.

The fixture transport fails closed for unknown endpoints and scenarios; it never falls through to a live API. The fixture build flag is a build-time constant. `npm run build` excludes the fixture router, transport and recorded data; `npm run build:fixtures` includes them for review and browser tests. No runtime flag or query parameter enables fixtures in a production build.

## Design source

`assets/design/tokens.json` in the repository root is authoritative. `npm run theme:generate` creates `src/design/generated/theme.css`, the Tailwind 4 theme, and a compatibility composition of the existing shared design recipes. It also copies local fonts, licenses, the Shuffify mark and the official Spotify logo from `shuffify/static/` into `public/`. `theme:check` rejects stale, missing and obsolete CSS output and stale copied assets. Do not edit generated CSS or copied assets by hand.

The foundation adds `radii.artwork-wide` and explicit existing legacy breakpoints to the shared token source. The old Tailwind 3 adapter remains intact. Artwork uses the two artwork tokens: 4px below the workspace breakpoint and 8px from that breakpoint. The component displays intact artwork with the official Spotify logo and a direct Spotify link. Layout, palette, type, spacing, radii, shadows and motion remain token driven.

An integration normally reads sources from the parent of `web/`. Standalone review can set `SHUFFIFY_SOURCE_ROOT` and `SHUFFIFY_TOKENS_FILE`; the scratch-only `.foundation-source.json` is not part of the deliverable.

## Shared component contracts

`AppShell` and `PublicShell` provide the skip link, main landmark, responsive navigation, legal footer, route title and heading focus. A newly revealed auth heading receives focus once; background data updates do not refocus an unchanged heading. Product routes compose these shared shells and providers.

`Providers` must be inside a React Router data router. It installs the session store, stable live regions, confirmation provider, navigation guard and task stack. `AuthGuard` starts with session loading, offers consent and a normal GET `/login` form when signed out, and keeps existing protected children mounted but hidden/inert during reauthentication so their drafts survive.

Render one `Workspace` for a route using context tasks. `openTask(task, 'push')` suspends and preserves the parent; the default `replace` mode closes the current context only after its guard allows it. Every task supplies a title, original `trigger` and route-heading `fallback`; focus returns to the surviving trigger or the fallback. `beforeClose(reason)` may be asynchronous and receives `close`, `escape`, `replace` or `suspend`. It is responsible for rendering any relevant validation error and returning false when transition is refused. Transitions reject reentry while a guard is pending. Context tasks permit desktop focus outside the panel; narrow layouts hide/inert primary content. A native modal confirmation suspends the underlying context and traps Tab/Shift+Tab. Opening/closing a panel never cancels an accepted operation.

`useNavigationBlocker(id, active, message)` protects route changes, normal document links, marked native `form[data-navigation]` submissions and browser unload. Approved native navigation bypasses a second unload confirmation. Navigation forms should put required submission data in actual controls; JavaScript-driven submissions should resolve the guard explicitly instead of bypassing it. Give each independently editable draft a stable blocker ID.

`Field` supplies native label/help/error associations to its child control. `Button` defaults to `type="button"`; its busy state remains focusable, announces disabled state and blocks duplicate clicks. `Announcer` has one polite and one assertive region. Pass a stable unique event ID for each announcement so Strict Mode replay does not repeat it. Callers should generate a new ID for a new notification, even when its text repeats.

## API contracts and requests

`src/api/contracts.ts` defines the snake_case wire format. Checked-in Draft 7 schemas are generated with `npm run schemas:generate`. `schemas:check` rejects missing, stale and obsolete schemas. The recorded valid/invalid cases in `fixtures/contract-cases.json` contain synthetic users, playlists, tracks, URLs and tokens. AJV is test-only, with coercion, default insertion and property removal disabled.

`createApiClient` accepts only root-relative `/api/` paths, uses same-origin cookies, rejects redirects and attaches `X-CSRFToken` from the current in-memory signed-in session for unsafe methods. The envelope is checked before returning data. Essential session/operation fields and operation identity are validated at runtime; page DTOs remain typed and covered by shared contract fixtures.

`request<T>` returns a discriminated result: `{status: 'completed', data: T}` for ordinary successful responses, or `{status: 'accepted', operation}` for HTTP 202. DB-only settings/preferences writes remain completed responses. A queued Spotify write is accepted, then separately observed to its real terminal state. Callers own each user intent's idempotency key; the client does not invent keys or automatically replay writes. A network/body failure on an unsafe request marks admission unknown. Preserve that intent and reconcile it using the server's operation/idempotency workflow before offering another write.

401 invalidates the shared session without replaying the request. 409 exposes the server's conflict and optional busy operation ID. 429 preserves the largest authoritative numeric/date header or JSON retry hint. `pollOperation` shares one sequential read loop per client and ID; it observes queued, running and compensating states until succeeded, failed, rolled_back or needs_attention. Transient read retries are bounded, 429 waits honor the complete hint, and aborting an observer stops only observation. No abort/retry path issues a Spotify write. Terminal failures retain their rich result and recovery facts.

Session notices are consumed once. A signed-out session omits `csrf_token`. Settings GET/POST share `SettingsContext`. `triggered_by` is `manual`, `scheduler` or null when origin was not recorded. Activity execution detail preserves typed resources, bookkeeping and counts, with explicit unavailable detail. Library/picker projections share `PlaylistSummary`; `ReturnTarget` is an enum, not an arbitrary navigation URL.

## Static hosting boundary

The Vite development proxy forwards `/api`, `/login`, `/callback`, `/logout` and `/health` to the local Flask API. The preview server enforces CSP with same-origin scripts, styles, fetches and fonts, Spotify image hosts, denied frames, nosniff, referrer policy and HSTS. There is no inline bootstrap, style attribute, CSS-in-JS runtime or CDN dependency. Production host rewrites and headers must mirror these rules. A Vite preview header is not deployment configuration.
