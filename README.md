# Shuffify

A Python tool to intelligently shuffle Spotify playlists while optionally keeping the first N tracks in their original position.

## Features
- Shuffle any playlist you own or can edit
- Option to keep first N tracks in their original position
- Visual progress bars for all operations
- Clean command-line interface
- Handles playlists of any size
- Supports collaborative playlists

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
pip install spotipy python-dotenv tqdm
```

4. Create a Spotify Application:
   - Visit [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   - Create a new application
   - Save your Client ID and Client Secret
   - Add `http://localhost:8888/callback` to the Redirect URIs

5. Create a `.env` file in the project root with your Spotify credentials:
```
SPOTIPY_CLIENT_ID=your_client_id_here
SPOTIPY_CLIENT_SECRET=your_client_secret_here
SPOTIPY_REDIRECT_URI=http://localhost:8888/callback
```

## Usage

1. Start Shuffify:
```bash
python main.py
```

2. Follow the prompts to:
   - Select a playlist from your library
   - Choose how many tracks (if any) to keep in their original position
   - Watch the progress as your playlist is shuffled

The program will show progress bars for:
- Loading tracks
- Processing playlist data
- Removing existing tracks
- Adding shuffled tracks

## Project Structure
```
shuffify/
├── main.py                  # Entry point
├── src/
│   ├── api/
│   │   ├── __init__.py
│   │   └── spotify_client.py  # Spotify API handling
│   └── utils/
│       ├── __init__.py
│       └── shuffify.py       # Shuffling logic
├── .env                     # Credentials (not tracked)
└── .gitignore              # Git ignore rules
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Open a Pull Request

## License

MIT License - feel free to use and modify as you wish! 