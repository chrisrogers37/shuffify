# Shuffify

A powerful playlist management tool for music creators and playlist managers.

## Features
- 🎵 Multiple shuffle algorithms for different use cases:
  - Basic shuffle with option to keep tracks fixed at start
  - Vibe-based shuffle using Spotify's audio features
  - Balanced shuffle ensuring fair representation
  - Percentage-based shuffle for partial reordering
- 🔒 Keep first N tracks in their original position
- 📊 Visual progress tracking for all operations
- 🔄 Undo shuffle operations
- 👥 Support for collaborative playlists
- 🎨 Clean, modern Spotify-themed UI
- 📱 Responsive design that works on all devices

## Project Structure

```
shuffify/
├── app/                    # Application code
│   ├── __init__.py        # App initialization
│   ├── routes.py          # Route handlers
│   ├── spotify/           # Spotify integration
│   ├── utils/             # Utility functions
│   └── templates/         # HTML templates
├── docker/               # Docker configuration
├── requirements/         # Dependency management
│   ├── base.txt         # Base requirements
│   ├── dev.txt          # Development requirements
│   └── prod.txt         # Production requirements
├── .env.example         # Environment variables template
├── .gitignore           # Git ignore rules
├── docker-compose.yml   # Docker compose configuration
├── Dockerfile           # Docker build configuration
└── README.md           # This file
```

## Shuffle Algorithms

Shuffify provides multiple algorithms for different use cases. See [app/utils/shuffle_algorithms/README.md](app/utils/shuffle_algorithms/README.md) for detailed documentation.

### Quick Overview
- **Basic Shuffle**: Standard random shuffle with fixed start option
- **Vibe Shuffle**: Creates smooth transitions using audio features
- **Balanced Shuffle**: Ensures fair representation from all playlist parts
- **Percentage Shuffle**: Shuffles only a portion of the playlist

## Development Workflow

### Getting Started

1. Clone the repository
2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # or `venv\Scripts\activate` on Windows
   ```
3. Install dependencies:
   ```bash
   # For development:
   pip install -r requirements/dev.txt
   
   # For production:
   pip install -r requirements/prod.txt
   ```
4. Copy `.env.example` to `.env` and configure
5. Run the development server:
   ```bash
   python run.py
   ```

### Requirements Management

The project uses a structured requirements approach:
- `requirements/base.txt`: Core dependencies needed by both development and production
- `requirements/dev.txt`: Additional development tools (testing, linting, etc.)
- `requirements/prod.txt`: Production-specific dependencies (e.g., gunicorn)

When adding new dependencies:
1. Add core dependencies to `base.txt`
2. Add development tools to `dev.txt`
3. Add production-specific packages to `prod.txt`

### Docker Development

```bash
# Build and start containers
docker-compose up --build
```

## Deployment

### Production (DigitalOcean)
1. Set up environment variables in DigitalOcean
2. Deploy using Docker:
   ```bash
   docker-compose up --build
   ```

### Development (Local)
1. Use local `.env` file
2. Run with Docker or Python directly

## Tech Stack
- **Backend**: Flask (Python)
- **Frontend**: Tailwind CSS
- **Authentication**: Spotify OAuth
- **Deployment**: Digital Ocean App Platform
- **Container**: Docker
- **WSGI Server**: Gunicorn

## Architecture

The application follows a clean architecture pattern with the following components:

```
shuffify/
├── app/                    # Application package
│   ├── __init__.py        # App initialization
│   ├── routes.py          # URL routes and views
│   ├── spotify/           # Spotify API handling
│   │   ├── __init__.py
│   │   └── client.py      # Spotify client wrapper
│   ├── utils/             # Utility functions
│   │   ├── __init__.py
│   │   └── shuffify.py    # Playlist shuffling logic
│   └── templates/         # HTML templates
├── config.py              # Configuration settings
└── run.py                # WSGI entry point
```

## Security

- Industry-standard security practices
- Secure session management
- HTTPS encryption
- OAuth 2.0 authentication

## Future Enhancements

- [ ] Smart shuffling based on:
  - Track tempo (BPM)
  - Key compatibility
  - Energy levels
  - Genre transitions
- [ ] Playlist analytics
- [ ] Multiple shuffle modes
- [ ] Batch operations
- [ ] Shuffle presets
- [ ] Export/import playlist orders

## Acknowledgments

- Developed by Christopher Rogers
- Built with [Flask](https://flask.palletsprojects.com/)
- Powered by [Spotify Web API](https://developer.spotify.com/documentation/web-api/)
- Uses [Spotipy](https://spotipy.readthedocs.io/) for Spotify API interactions
- Styled with [Tailwind CSS](https://tailwindcss.com/) 