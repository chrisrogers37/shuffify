# Shuffify - Playlist Perfection

Shuffify is a web application designed to give Spotify users advanced control over their playlists. It provides a suite of unique shuffling algorithms that go beyond Spotify's default shuffle, allowing users to reorder their tracks in more creative and useful ways.

**Live Application:** [**shuffify.app**](https://shuffify.app)

<!-- Placeholder, consider adding a real screenshot of the page-->

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
    - **Tempo Gradient Shuffle:** Sort by BPM for DJ-style transitions *(hidden — needs Audio Features API)*.
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
- **API:** Spotify Web API (via spotipy)
- **Database:** SQLAlchemy + SQLite
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
├── documentation/            # Project documentation and evaluations
├── shuffify/
│   ├── __init__.py           # App factory (Redis, DB, Scheduler init)
│   ├── routes.py             # All web routes and view logic
│   ├── services/             # 10 service modules (business logic layer)
│   ├── schemas/              # Pydantic validation schemas
│   ├── models/               # Data models + SQLAlchemy DB models
│   ├── spotify/              # Modular Spotify client (auth, api, cache)
│   ├── shuffle_algorithms/   # 7 shuffle algorithms with registry
│   ├── templates/            # Jinja2 templates (5 pages)
│   └── static/               # Static assets (images, public pages)
├── tests/                    # 690 tests
├── Dockerfile                # Defines the application container
└── docker-compose.yml        # Orchestrates the local Docker environment
```

## Recent Updates

- **Playlist Workshop Enhancement Suite** (6 phases) — Track management, playlist merging, external raiding, user database, scheduled operations
- **7 Shuffle Algorithms** — Added ArtistSpacing, AlbumSequence, TempoGradient (hidden)
- **SQLAlchemy Database** — User, Schedule, and JobExecution models
- **APScheduler Integration** — Background job execution for automated shuffle/raid
- **Fernet Token Encryption** — Secure storage of Spotify refresh tokens
- **10 Service Layer Modules** — Full separation of concerns
- **690 Tests** — Comprehensive coverage across all modules

## License

This project is licensed under the MIT License - see the `LICENSE` file for details. 