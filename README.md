# Shuffify - Playlist Perfection

Shuffify is a web application designed to give Spotify users advanced control over their playlists. It provides a suite of unique shuffling algorithms that go beyond Spotify's default shuffle, allowing users to reorder their tracks in more creative and useful ways.

**Live Application:** [**shuffify.app**](https://shuffify.app)

## Project Overview

This project was built to solve a common frustration for avid Spotify users: the generally highly manual nature of managing playlist song orders. Shuffify addresses this by providing a simple, intuitive interface where users can connect their Spotify account, select a playlist, and apply a variety of sorting algorithms to reorder the track list!

The application is built with a security-first mindset, using environment variables to manage sensitive API keys and a robust session management system. It features a multi-level undo system, allowing users to step back through previous shuffles with ease.

## Key Features

- **Secure Spotify Authentication:** Connects to your Spotify account using the official OAuth 2.0 flow with enhanced security messaging.
- **7 Shuffle Algorithms:**
    - **Basic Shuffle:** A standard, random reordering.
    - **Balanced Shuffle:** Distribute tracks from all parts of the playlist evenly using round-robin selection.
    - **Percentage Shuffle:** Keep a certain percentage of tracks at the top of the playlist.
    - **Stratified Shuffle:** Divide the playlist into sections, shuffle each section independently.
    - **Artist Spacing Shuffle:** Ensure the same artist doesn't appear back-to-back.
    - **Album Sequence Shuffle:** Keep album tracks together but shuffle albums.
    - **Newest First Shuffle:** Reorder by date added, newest first, with adjustable jitter.
- **Playlist Workshop:** Edit a local draft, preview algorithms, hold tracks, manage snapshots and review collected or archived tracks before saving.
- **Scheduled Operations:** Automated shuffle and raid jobs on recurring schedules via APScheduler.
- **Multi-Level Undo:** Step back through every shuffle you've made to a playlist within your session.
- **Enhanced User Experience:**
    - Responsive After Hours design with accessible task panels
    - A React interface with reviewed actions and durable operation receipts
    - Accessibility-first approach with ARIA labels and keyboard navigation
    - Smooth scroll animations and visual feedback
- **Session Management:** Logout functionality for secure user switching
- **Legal Compliance:** Includes Terms of Service and Privacy Policy pages, as required by Spotify's developer policies.

## Perfect For

- **Curated Collections:** Keep your carefully curated playlists fresh, especially after adding new songs
- **Tastemaker Playlists:** Perfect for tastemakers who want to mix it up and get those new adds to the top for their followers
- **New Perspectives:** Make your playlists feel new with a quick reorder
- **Playlist Maintenance:** Reorder that massive playlist that you've been meaning to update with one click

## Tech Stack

- **Backend:** Flask 3.1.x (Python 3.12+)
- **Frontend:** React, TypeScript, Vite and Tailwind CSS with shared design tokens
- **API:** Spotify Web API (direct HTTP client)
- **Database:** SQLAlchemy + PostgreSQL/SQLite
- **Scheduler:** APScheduler for background jobs
- **Server:** Gunicorn (production), Flask dev server (local)
- **Caching:** Redis for sessions and API response caching
- **Validation:** Pydantic v2 for request validation
- **Security:** Fernet encryption, environment validation, health checks
- **Containerization:** Docker with health checks

## Project Structure

The project follows a standard Flask application structure, with a focus on modularity and separation of concerns.

```
shuffify/
├── config.py                 # Application configuration (loads from .env)
├── requirements/             # Python dependencies (base.txt, dev.txt, prod.txt)
├── run.py                    # Application entry point
├── documentation/            # Project documentation
├── shuffify/
│   ├── __init__.py           # API app factory (Redis, DB, security headers)
│   ├── routes/               # Feature-based route modules
│   ├── services/             # Service modules (business logic layer)
│   ├── schemas/              # Pydantic validation schemas
│   ├── models/               # Data models + SQLAlchemy DB models
│   ├── spotify/              # Modular Spotify client (auth, api, cache)
│   ├── shuffle_algorithms/   # Shuffle algorithms and their registry
│   └── static/               # Shared fonts and branding
├── web/                      # React application, contracts and frontend checks
├── assets/                   # Canonical design and legal sources
├── tests/                    # Test suite
└── Dockerfile                # Defines the application container
```

## Visual design sources

The After Hours design system styles the whole React application, public pages included.
Authoritative tokens and recipes live in `assets/design/`; the frontend generates
its theme from those inputs. See the
[design system](documentation/design/after-hours/DESIGN.md).

Flask serves the JSON API, OAuth endpoints and health checks. Product pages are
rendered by `web/`; there are no server-rendered application templates. Run
`npm ci`, `npm test` and `npm run build` from `web/` for the frontend.

When an HTTPS frontend proxies the API through a different backend hostname,
set `PUBLIC_ORIGIN` on the API to the browser-facing HTTPS origin. Production
uses `https://shuffify.app`. Include a port only when it is nonstandard; do
not include credentials, a path, query or fragment. Invalid values prevent app
startup. This pins unsafe requests to the configured origin after proxy
reconstruction while keeping Flask-WTF token and strict referrer checks enabled.
It never authorizes an origin from client-supplied forwarded headers. Read
requests and their redirects retain their normal host behavior. The production
split API refuses to start without `PUBLIC_ORIGIN`; development, the harness,
and the private worker do not require it.

Legal copy is canonical in `assets/legal/`. The frontend prerenders that copy
for `/terms` and `/privacy`, and `tests/test_legal_copy_pin.py` fails when the
copy changes without its pinned hash being updated.

The frontend build escapes the `CONTACT_ADDRESS` environment value into that
copy and otherwise uses the owner address, `christophertrogers37@gmail.com`. An
unresolved `CONTACT_ADDRESS` marker fails generation and the release build. Use
the same contact value for `npm run public:generate` and `npm run public:check`.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.

## Split runtime

The API runs Gunicorn with one gthread worker, eight threads, a 30-second timeout, and application preload disabled. Its command is `gunicorn --config gunicorn.conf.py run:app`. The background service runs `python -m shuffify.worker`; it owns queued Spotify writes and the in-memory scheduler. Start one worker replica. API and worker each expose `/health` on their own listener. Worker health reports whether the command processor is serving; scheduler leadership and registration are reported separately by authenticated private control. A healthy standby can take the scheduler lock when the leader drains. Detailed worker control requires the shared bearer credential and stays on the private network.

Run `SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1 flask db upgrade` as a separate pre-deploy step. `scripts/pre-deploy.sh` provides that command. Starting the image only executes the selected service command. An empty development database requires an explicit one-time `flask create-schema` bootstrap before upgrading; never stamp a populated database to bypass migration checks.

The Railway infrastructure definition and setup instructions are in [.railway/README.md](.railway/README.md).

| Service | Required environment variable names |
| --- | --- |
| API and worker | `APP_CONFIG`, `DATABASE_URL` (required owner-set direct, unpooled Neon URL), `REDIS_URL`, `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`, `WORKER_CONTROL_TOKEN`, `SCHEDULER_ENABLED`, `SCHEDULER_TIMEZONE` |
| API pre-deploy migration | `DATABASE_URL` (inherits the API's required owner-set direct, unpooled Neon URL) |
| API | `PORT`, `SHUFFIFY_PROCESS_ROLE`, `WORKER_CONTROL_URL`, `PUBLIC_ORIGIN` (required owner-set browser-facing origin) |
| Worker | `PORT`, `SHUFFIFY_PROCESS_ROLE`, `WORKER_CONTROL_HOST`, `WORKER_CONTROL_PORT` |
| Frontend build | `RAILWAY_API_URL`; optional `CONTACT_ADDRESS` override |

Keep the OAuth callback on the public frontend origin. API rewrites preserve the same-origin session and CSRF flow. Public legal content receives its contact address at build time. Store credentials in the hosting platforms and keep environment files out of Git and build artifacts.

Railway does not provision the application database. Production uses the Neon
project `shuffify-db`, branch `production`, through the shared `DATABASE_URL`
(direct and unpooled); every API deployment upgrades it in the pre-deploy step.
A new nonproduction environment needs its own Neon branch: a backup branch is
not staging, and staging must never use production. Check the Neon project and
branch in the owner console before setting `DATABASE_URL`.
[Production](documentation/guides/production.md) describes what runs where, the
backups and how to roll back. See [.railway/README.md](.railway/README.md) for
the Railway setup steps.

For local development, the [local test harness](documentation/guides/testing-harness.md) runs the API, the operation worker, PostgreSQL, Redis and a fake Spotify on one machine; it runs on Linux only. To run the API alone, copy `.env.example` to `.env`, set the Spotify credentials and run `python run.py`: it uses `DevConfig`, listens on port 8000 (`FLASK_PORT` overrides it), stores data in SQLite unless `DATABASE_URL` is set, and treats Redis as optional (sessions fall back to the filesystem when `REDIS_URL` is unreachable). The API serves no pages, so to use the app, start it with `FLASK_HOST=127.0.0.1 FLASK_PORT=5000 python run.py` (on macOS, AirPlay Receiver holds port 5000 on every interface) and run `npm run dev` in `web/` (Node 24, after `npm ci`), which proxies `/api`, `/login`, `/callback`, `/logout` and `/health` to it. Open `http://127.0.0.1:5173`, not `localhost`, and send Spotify's login back through the dev server: set `SPOTIFY_REDIRECT_URI=http://127.0.0.1:5173/callback` and add that URI to the Spotify app. `npm run dev -- --mode fixture` serves recorded fixtures without an API.

The Vercel project root is `web/`. Its build command generates `.vercel/output/config.json` from `RAILWAY_API_URL` and copies only the built static files. Dynamic API/authentication/health routes precede static files and the SPA fallback, and use `no-store` headers. The API URL must be an HTTPS origin with no credentials or path. Set the API origin and any desired public-contact override in the platform, then build normally; generated output stays outside Git. See [web/README.md](web/README.md) for local routing, security-header and accessibility checks.
