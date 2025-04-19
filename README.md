# Shuffify

A powerful playlist management tool for music creators and playlist managers.

## Project Structure

```
shuffify/
├── app/                    # Application code
│   ├── __init__.py        # App initialization
│   ├── routes.py          # Route handlers
│   ├── spotify/           # Spotify integration
│   └── utils/             # Utility functions
├── tests/                 # Test suite
├── docs/                  # Documentation
├── .github/              # GitHub workflows
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

## Development Workflow

### Branch Strategy

- `main`: Production-ready code
- `develop`: Integration branch for features
- `feature/*`: New features
- `release/*`: Release preparation
- `hotfix/*`: Urgent production fixes

### Versioning

We use [Semantic Versioning](https://semver.org/):
- MAJOR.MINOR.PATCH
- Example: 1.0.0

### Getting Started

1. Clone the repository
2. Create a virtual environment
3. Install dependencies:
   ```bash
   pip install -r requirements/dev.txt
   ```
4. Copy `.env.example` to `.env` and configure
5. Run the development server:
   ```bash
   python run.py
   ```

### Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app tests/
```

### Docker Development

```bash
# Build and start containers
docker-compose up --build

# Run tests in container
docker-compose run --rm app pytest
```

## Release Process

1. Create a release branch from develop
2. Update version numbers
3. Update CHANGELOG.md
4. Create pull request to main
5. Create GitHub release
6. Deploy to production

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit a pull request

## License

MIT License

## Features
- 🎵 Shuffle any playlist you own or can edit
- 🔒 Keep first N tracks in their original position
- 📊 Visual progress tracking for all operations
- 🔄 Undo shuffle operations (within 1 hour)
- 👥 Support for collaborative playlists
- 🎨 Clean, modern Spotify-themed UI
- 📱 Responsive design that works on all devices

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

- Built with [Flask](https://flask.palletsprojects.com/)
- Powered by [Spotify Web API](https://developer.spotify.com/documentation/web-api/)
- Uses [Spotipy](https://spotipy.readthedocs.io/) for Spotify API interactions
- Styled with [Tailwind CSS](https://tailwindcss.com/) 