# Shuffify

A modern playlist management tool for music creators and playlist managers, built with Flask and Tailwind CSS.

## Features

- **Multiple Shuffle Algorithms**:
  - Basic Shuffle: Standard random shuffle with fixed start option
  - Balanced Shuffle: Ensures fair representation from all playlist parts
  - Percentage Shuffle: Allows shuffling specific portions of playlists
  - Vibe Shuffle: Creates smooth transitions using Spotify's audio features
- Keep certain tracks in their original position
- Visual progress tracking
- Undo functionality
- Collaborative playlist support
- Responsive design with modern glassmorphism effects
- Decorative music note patterns in background
- Smooth hover and transition effects

## Project Structure

```
shuffify/
├── app/                    # Application code
│   ├── static/            # Static assets (CSS, JS, images)
│   ├── templates/         # HTML templates
│   ├── services/          # Business logic services
│   ├── spotify/           # Spotify API integration
│   ├── utils/             # Utility functions
│   ├── __init__.py        # Application factory
│   └── routes.py          # Route definitions
├── requirements/          # Dependency management
│   ├── base.txt          # Core dependencies
│   ├── dev.txt           # Development dependencies
│   └── prod.txt          # Production dependencies
├── config.py             # Configuration settings
├── run.py                # Application entry point
├── Dockerfile            # Docker configuration
├── docker-compose.yml    # Docker Compose configuration
└── tailwind.config.js    # Tailwind CSS configuration
```

## Development Workflow

### Prerequisites

- Python 3.8+
- Node.js (for Tailwind CSS)
- Spotify Developer Account

### Getting Started

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
   pip install -r requirements/dev.txt
   ```

4. Set up your Spotify credentials in `config.py`

5. Run the development server:
   ```bash
   python run.py
   ```

### Requirements Management

The project uses a structured approach to requirements management:

- `requirements/base.txt`: Core dependencies required for the application to run
- `requirements/dev.txt`: Development dependencies (includes base.txt)
- `requirements/prod.txt`: Production dependencies (includes base.txt)

To add a new dependency:
1. Add it to `base.txt` if it's required for core functionality
2. Add it to `dev.txt` if it's only needed for development
3. Add it to `prod.txt` if it's only needed for production

### Docker Development

To run the application using Docker:

```bash
docker-compose up --build
```

## Tech Stack

- **Backend**: Flask
- **Frontend**: Tailwind CSS
- **Containerization**: Docker
- **Authentication**: Spotify OAuth 2.0
- **Database**: SQLite (development), PostgreSQL (production)

## Architecture

The application follows clean architecture principles:

- **Presentation Layer**: Templates and static assets
- **Application Layer**: Routes and services
- **Domain Layer**: Business logic and models
- **Infrastructure Layer**: External services and utilities

## Security

- Secure session management
- OAuth 2.0 authentication
- Environment-based configuration
- Input validation and sanitization

## Future Enhancements

- Stratified shuffling (shuffle within sections)
- Shuffle by release date
- Export/import functionality

## Acknowledgments

- Developed by Christopher Rogers
- Built with Flask, Spotify Web API, Spotipy, and Tailwind CSS 