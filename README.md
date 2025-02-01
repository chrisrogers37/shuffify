# Shuffify

A modern web application built with Streamlit that lets you intelligently shuffle your Spotify playlists while keeping selected tracks in place.

## Features
- 🎵 Shuffle any playlist you own or can edit
- 🔒 Keep first N tracks in their original position
- 📊 Visual progress tracking for all operations
- 🔄 Undo shuffle operations (within 1 hour)
- 👥 Support for collaborative playlists
- 🎨 Clean, modern Spotify-themed UI
- 📱 Responsive design that works on all devices

## Prerequisites
- Python 3.8 or higher
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
   - Add `http://localhost:8501/` as a redirect URI
   - Copy your Client ID

5. Configure Streamlit secrets:
   - Create `.streamlit/secrets.toml` with your Spotify credentials:
   ```toml
   SPOTIPY_CLIENT_ID = "your-client-id"
   SPOTIPY_REDIRECT_URI = "http://localhost:8501/"
   ```

6. Run the application:
```bash
streamlit run streamlit_app.py
```

## Usage

1. Open the application in your browser (typically http://localhost:8501)
2. Click "Connect with Spotify" to authorize the application
3. Select a playlist from your library
4. Choose how many tracks (if any) to keep in their original position
5. Click "Shuffle Playlist" to randomize the order
6. Use the "Re-shuffle" or "Restore Original Order" options as needed

## Project Structure
```
shuffify/
├── streamlit_app.py        # Main Streamlit application
├── src/
│   ├── api/
│   │   ├── __init__.py
│   │   └── spotify_client.py  # Spotify API handling
│   └── utils/
│       ├── __init__.py
│       └── shuffify.py        # Shuffling logic
├── .streamlit/
│   ├── config.toml        # Streamlit configuration
│   └── secrets.toml       # Spotify credentials (not tracked)
├── requirements.txt       # Python dependencies
└── .gitignore            # Git ignore rules
```

## Development

### Setting Up Development Environment

1. Install development dependencies:
```bash
pip install -r requirements.txt
```

2. Install pre-commit hooks:
```bash
pre-commit install
```

### Running Tests

```bash
pytest
```

### Code Style

This project follows PEP 8 guidelines. We use:
- Black for code formatting
- isort for import sorting
- flake8 for linting

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

- Built with [Streamlit](https://streamlit.io/)
- Powered by [Spotify Web API](https://developer.spotify.com/documentation/web-api/)
- Uses [Spotipy](https://spotipy.readthedocs.io/) for Spotify API interactions 