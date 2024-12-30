# Shuffify

A Python application that intelligently shuffles your Spotify playlists with just a few clicks.

## Features

- Easy Spotify OAuth2 authentication
- View and select from your Spotify playlists
- Smart shuffling algorithm
- Instant playlist updates
- Secure token management

## Prerequisites

- Python 3.8 or higher
- Spotify Developer Account
- Registered Spotify Application

## Setup

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

4. Create a Spotify Application:
   - Visit [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   - Create a new application
   - Save your Client ID and Client Secret
   - Add `http://localhost:8888/callback` to the Redirect URIs

5. Set up your environment:
```bash
cp .env.example .env
```

6. Update the `.env` file with your Spotify credentials:
```
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://localhost:8888/callback
```

## Usage

1. Start Shuffify:
```bash
python main.py
```

2. Follow the authentication process in your browser
3. Choose a playlist to shuffle
4. Enjoy your freshly shuffled playlist!

## Running Tests

```bash
pytest tests/
```

## Project Structure

- `src/`: Source code
  - `auth/`: Authentication handling
  - `api/`: Spotify API integration
  - `utils/`: Utility functions
- `tests/`: Test suite
- `main.py`: Application entry point

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Open a Pull Request

## License

MIT License - feel free to use and modify as you wish! 