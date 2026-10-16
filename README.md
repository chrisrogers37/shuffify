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
- **Playlist Workshop:** Advanced playlist management with track operations, playlist merging, and external playlist raiding.
- **Scheduled Operations:** Automated shuffle and raid jobs on recurring schedules via APScheduler.
- **Multi-Level Undo:** Step back through every shuffle you've made to a playlist within your session.
- **Enhanced User Experience:**
    - Modern, responsive design with glassmorphism effects
    - Progressive enhancement with dynamic interactions
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
- **Frontend:** Tailwind CSS with custom animations
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
│   ├── __init__.py           # App factory (Redis, DB, Scheduler init)
│   ├── routes/               # Feature-based route modules
│   ├── services/             # Service modules (business logic layer)
│   ├── schemas/              # Pydantic validation schemas
│   ├── models/               # Data models + SQLAlchemy DB models
│   ├── spotify/              # Modular Spotify client (auth, api, cache)
│   ├── shuffle_algorithms/   # Shuffle algorithms and their registry
│   ├── templates/            # Jinja2 templates
│   └── static/               # Static assets (images, public pages)
├── tests/                    # Test suite
├── Dockerfile                # Defines the application container
└── docker-compose.yml        # Orchestrates the local Docker environment
```

## Visual design sources

The After Hours design system is shared by the landing page and the app pages.
Its authoritative tokens and styles live in `assets/design/`; Jinja components
live in `shuffify/templates/macros/design*.html`. See the
[design system](documentation/design/after-hours/DESIGN.md).

After editing design sources or templates, run `./scripts/build-css.sh`. After
editing shared shell macros or legal sources, also run
`python scripts/build-public-pages.py`. Commit the generated CSS and legal HTML;
`./scripts/build-css.sh --check` and pytest verify they match their sources.

Legal pages receive their contact email from the `CONTACT_ADDRESS` build
environment variable. Run `python scripts/build-public-pages.py` with that
variable set to generate the public pages; `--check` uses the same variable.
Without an override, legal pages use the published support address
`christophertrogers37@gmail.com`.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.

## Split runtime

The API runs Gunicorn with one gthread worker, eight threads, a 30-second timeout, and application preload disabled. Its command is `gunicorn --config gunicorn.conf.py run:app`. The background service runs `python -m shuffify.worker`; it owns queued Spotify writes and the in-memory scheduler. Start one worker replica. API and worker each expose `/health` on their own listener. Worker health reports whether the command processor is serving; scheduler leadership and registration are reported separately by authenticated private control. A healthy standby can take the scheduler lock when the leader drains. Detailed worker control requires the shared bearer credential and stays on the private network.

Run `SCHEDULER_ENABLED=false SHUFFIFY_MIGRATION_STEP=1 flask db upgrade` as a separate pre-deploy step. `scripts/pre-deploy.sh` provides that command. Starting the image only executes the selected service command. An empty development database requires an explicit one-time `flask create-schema` bootstrap before upgrading; never stamp a populated database to bypass migration checks.

The Railway infrastructure definition and setup instructions are in [.railway/README.md](.railway/README.md).

| Service | Required environment variable names |
| --- | --- |
| API and worker | `APP_CONFIG`, `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`, `WORKER_CONTROL_TOKEN`, `SCHEDULER_ENABLED`, `SCHEDULER_TIMEZONE` |
| API | `PORT`, `SHUFFIFY_PROCESS_ROLE`, `WORKER_CONTROL_URL` |
| Worker | `PORT`, `SHUFFIFY_PROCESS_ROLE`, `WORKER_CONTROL_HOST`, `WORKER_CONTROL_PORT` |
| Frontend build | `RAILWAY_API_URL` |

Keep the OAuth callback on the public frontend origin. API rewrites preserve the same-origin session and CSRF flow. Legal pages use the published support address unless `CONTACT_ADDRESS` overrides it at build time. Store credentials in the hosting platforms and keep environment files out of Git and build artifacts.

For local containers, set the shared environment names in `.env`; `DATABASE_URL` must use the `db` service hostname and `REDIS_URL` must use `redis`. The PostgreSQL image provides TLS for the production configuration. Build the image and start `db redis`, then bootstrap a fresh empty database explicitly with `docker compose --profile migration run --rm -e SCHEDULER_ENABLED=false -e SHUFFIFY_MIGRATION_STEP=1 migrate flask create-schema`. Run `docker compose --profile migration run --rm migrate` before `docker compose up -d api worker`. The migration profile runs only when selected. The development override mounts source into both application processes; the production override starts image-only services when selected with the base file explicitly.

The Vercel project root is `web/`. Its build command generates `.vercel/output/config.json` from `RAILWAY_API_URL` and copies only the built static files. Dynamic API/authentication/health routes precede static files and the SPA fallback, and use `no-store` headers. The API URL must be an HTTPS origin with no credentials or path. Set the API origin and any contact override in the platform, then build normally; generated output stays outside Git.
