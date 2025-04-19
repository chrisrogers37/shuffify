import os
import logging
from flask import Flask
from flask_session import Session
from config import config

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def create_app(config_name=None):
    """Create and configure the Flask application."""
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'production')
    
    # Ensure config_name is a string
    if not isinstance(config_name, str):
        config_name = 'development'  # Default to development if not a string
    
    app = Flask(__name__)
    
    # Load config
    app.config.from_object(config[config_name])
    
    # Initialize Flask-Session
    Session(app)
    
    # Ensure the session directory exists
    os.makedirs(app.config['SESSION_FILE_DIR'], exist_ok=True)
    
    # Register blueprints
    from app.routes import main as main_blueprint
    app.register_blueprint(main_blueprint)
    
    return app

# Create the application instance
application = create_app() 