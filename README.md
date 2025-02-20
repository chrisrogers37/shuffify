# Shuffify

A modern web application built with Flask that lets you intelligently shuffle your Spotify playlists while keeping selected tracks in place. Try it out at [shuffify.app](https://orca-app-6xudp.ondigitalocean.app/)!

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