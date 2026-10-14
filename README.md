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

## License

This project is licensed under the MIT License - see the `LICENSE` file for details. 