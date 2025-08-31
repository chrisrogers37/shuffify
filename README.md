# Shuffify - Playlist Perfection

Shuffify is a web application designed to give Spotify users advanced control over their playlists. It provides a suite of unique shuffling algorithms that go beyond Spotify's default shuffle, allowing users to reorder their tracks in more creative and useful ways.

**Live Application:** [**shuffify.app**](https://shuffify.app)

<!-- Placeholder, consider adding a real screenshot of the page-->

## Project Overview

This project was built to solve a common frustration for avid Spotify users: the generally highly manual nature of managing playlist song orders. Shuffify addresses this by providing a simple, intuitive interface where users can connect their Spotify account, select a playlist, and apply a variety of sorting algorithms to reorder the track list!

The application is built with a security-first mindset, using environment variables to manage sensitive API keys and a robust session management system. It features a multi-level undo system, allowing users to step back through previous shuffles with ease.

## Key Features

- **Secure Spotify Authentication:** Connects to your Spotify account using the official OAuth 2.0 flow with enhanced security messaging.
- **Multiple Shuffle Algorithms:**
    - **Basic Shuffle:** A standard, random reordering.
    - **Balanced Shuffle:** A more complex algorithm to distribute tracks from all parts of the playlist evenly using round-robin selection.
    - **Percentage Shuffle:** Keep a certain percentage of tracks at the top of the playlist.
    - **Stratified Shuffle:** Divide the playlist into sections, shuffle each section independently, and reassemble the sections in the original order.
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

- **Backend:** Flask (Python)
- **Frontend:** Tailwind CSS with custom animations
- **API:** Spotify Web API
- **Server:** Gunicorn
- **Containerization:** Docker
- **Security:** Environment validation, health checks, security scanning tools

## Project Structure

The project follows a standard Flask application structure, with a focus on modularity and separation of concerns.

```
shuffify/
├── config.py                 # Application configuration (loads from .env)
├── requirements/             # Python dependencies (base.txt, dev.txt, prod.txt)
├── run.py                    # Application entry point
├── dev_guides/               # Development documentation and critiques
├── shuffify/
│   ├── __init__.py           # App factory
│   ├── routes.py             # All web routes and view logic
│   ├── models/               # Data models (e.g., Playlist)
│   ├── spotify/              # Spotify API client
│   ├── shuffle_algorithms/   # Core shuffling logic
│   ├── templates/            # Jinja2 templates for all pages
│   └── static/               # Static assets (images, public pages)
├── Dockerfile                # Defines the application container
└── docker-compose.yml        # Orchestrates the local Docker environment
```

## Recent Updates (v2.3.5)

- **Enhanced Landing Page:** Complete UX renovation with improved conversion optimization
- **Accessibility Improvements:** ARIA labels, skip links, focus states, and screen reader support
- **Dynamic Interactions:** Progressive enhancement with scroll animations and responsive feedback
- **Trust Indicators:** Security badges and social proof elements
- **Session Management:** Added logout functionality for better user control
- **Spacing Optimization:** Improved visual flow and scrolling experience

## License

This project is licensed under the MIT License - see the `LICENSE` file for details. 