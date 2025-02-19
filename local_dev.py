import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Ensure we're in development mode
os.environ['FLASK_ENV'] = 'development'

# Import the application instance
from app import application

if __name__ == '__main__':
    # Run in debug mode on port 8080
    application.run(host='0.0.0.0', port=8080, debug=True) 