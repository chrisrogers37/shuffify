# Shuffify

A modern web application built with Flask that lets you intelligently shuffle your Spotify playlists while keeping selected tracks in place.

## Features
- 🎵 Shuffle any playlist you own or can edit
- 🔒 Keep first N tracks in their original position
- 📊 Visual progress tracking for all operations
- 🔄 Undo shuffle operations (within 1 hour)
- 👥 Support for collaborative playlists
- 🎨 Clean, modern Spotify-themed UI
- 📱 Responsive design that works on all devices

## Prerequisites
- Python 3.9 or higher
- Spotify Developer Account
- Registered Spotify Application

## Quick Start

1. Clone the repository:
```bash
git clone https://github.com/yourusername/shuffify.git
cd shuffify
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up your Spotify Developer credentials:
   - Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   - Create a new application
   - Add `http://localhost:8000/callback` as a redirect URI
   - Copy your Client ID and Client Secret

5. Configure environment variables:
   - Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
   - Edit `.env` with your Spotify credentials and a secure secret key

6. Run the application:
```bash
# Development mode
flask run --port=8000

# Or using Docker
docker-compose up --build
```

## Usage

1. Open the application in your browser (http://localhost:8000)
2. Click "Connect with Spotify" to authorize the application
3. Select a playlist from your library
4. Choose how many tracks (if any) to keep in their original position
5. Click "Shuffle" to randomize the order
6. Use the "Undo" option if you want to restore the original order

## Project Structure
```
shuffify/
├── app/                    # Application package
│   ├── __init__.py        # App initialization
│   ├── routes.py          # URL routes and views
│   ├── spotify/           # Spotify API handling
│   │   ├── __init__.py
│   │   └── client.py
│   ├── utils/             # Utility functions
│   │   ├── __init__.py
│   │   └── shuffify.py
│   └── templates/         # HTML templates
│       ├── base.html
│       ├── index.html
│       └── dashboard.html
├── config.py              # Configuration settings
├── requirements.txt       # Python dependencies
├── run.py                # Application entry point
├── Dockerfile            # Docker configuration
└── docker-compose.yml    # Docker Compose configuration
```

## Development

### Setting Up Development Environment

1. Install development dependencies:
```bash
pip install -r requirements.txt
```

2. Run in development mode:
```bash
export FLASK_ENV=development
flask run --port=8000
```

### Docker Development

Run with Docker Compose for a containerized development environment:
```bash
docker-compose up --build
```

### Code Style

This project follows PEP 8 guidelines. We use:
- Black for code formatting
- isort for import sorting
- flake8 for linting

## Deployment

### Digital Ocean App Platform

1. Fork this repository to your GitHub account
2. Create a new app in Digital Ocean App Platform
3. Connect your GitHub repository
4. Configure environment variables:
   - `FLASK_ENV=production`
   - `SPOTIFY_CLIENT_ID`
   - `SPOTIFY_CLIENT_SECRET`
   - `SPOTIFY_REDIRECT_URI` (your app's URL + /callback)
   - `SECRET_KEY`
5. Deploy!

### Manual Deployment

The application includes a Dockerfile for easy deployment to any container platform:

```bash
docker build -t shuffify .
docker run -p 8000:8000 --env-file .env shuffify
```

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

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Open a Pull Request

## License

MIT License - See [LICENSE](LICENSE) for details.

## Acknowledgments

- Built with [Flask](https://flask.palletsprojects.com/)
- Powered by [Spotify Web API](https://developer.spotify.com/documentation/web-api/)
- Uses [Spotipy](https://spotipy.readthedocs.io/) for Spotify API interactions
- Styled with [Tailwind CSS](https://tailwindcss.com/) 