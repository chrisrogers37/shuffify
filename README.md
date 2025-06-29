# Shuffify - Playlist Perfection

Shuffify is a web application designed to give Spotify users advanced control over their playlists. It provides a suite of unique shuffling algorithms that go beyond Spotify's default shuffle, allowing users to reorder their tracks in more creative and useful ways.

**Live Application:** [**shuffify.app**](https://shuffify.app)

<!-- Placeholder, consider adding a real screenshot of the page-->

## Project Overview

This project was built to solve a common frustration for avid Spotify users: the generally highly manual nature of managing playlist song orders. Shuffify addresses this by providing a simple, intuitive interface where users can connect their Spotify account, select a playlist, and apply a variety of sorting algorithms to reorder the track list!

The application is built with a security-first mindset, using environment variables to manage sensitive API keys and a robust session management system. It features a multi-level undo system, allowing users to step back through previous shuffles with ease.

## Key Features

- **Secure Spotify Authentication:** Connects to your Spotify account using the official OAuth 2.0 flow.
- **Multiple Shuffle Algorithms:**
    - **Basic Shuffle:** A standard, random reordering.
    - **Balanced Shuffle:** A more complex algorithm to distribute artists and genres evenly.
    - **Percentage Shuffle:** Keep a certain percentage of tracks at the top of the playlist.
    - **Stratified Shuffle:** Group tracks by audio features (like danceability or energy) before shuffling.
- **Multi-Level Undo:** Step back through every shuffle you've made to a playlist within your session.
- **Click-to-Open UI:** A clean and responsive user interface for a smooth user experience.
- **Legal Compliance:** Includes Terms of Service and Privacy Policy pages, as required by Spotify's developer policies.

## Tech Stack

- **Backend:** Flask (Python)
- **Frontend:** Tailwind CSS
- **API:** Spotify Web API
- **Server:** Gunicorn
- **Containerization:** Docker

## Project Structure

The project follows a standard Flask application structure, with a focus on modularity and separation of concerns.

```
shuffify/
├── config.py                 # Application configuration (loads from .env)
├── requirements/             # Python dependencies
├── run.py                    # Application entry point
├── shuffify/
│   ├── __init__.py           # App factory
│   ├── routes.py             # All web routes and view logic
│   ├── models/               # Data models (e.g., Playlist)
│   ├── spotify/              # Spotify API client
│   ├── shuffle_algorithms/   # Core shuffling logic
│   └── templates/            # Jinja2 templates for all pages
├── Dockerfile                # Defines the application container
└── docker-compose.yml        # Orchestrates the local Docker environment
```

## License

This project is licensed under the MIT License - see the `LICENSE` file for details. 