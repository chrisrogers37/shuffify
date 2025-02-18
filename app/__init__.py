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
        config_name = os.getenv('FLASK_ENV', 'development')
    
    logger.debug(f"Creating app with config: {config_name}")
    logger.debug(f"Current directory: {os.getcwd()}")
    logger.debug(f"Directory contents: {os.listdir('.')}")
    
    app = Flask(__name__)
    
    # Load config
    app.config.from_object(config[config_name])
    
    # Initialize Flask-Session
    Session(app)
    
    # Ensure the session directory exists
    os.makedirs(app.config['SESSION_FILE_DIR'], exist_ok=True)
    
    # Register blueprints
    try:
        from .routes import main as main_blueprint
        app.register_blueprint(main_blueprint)
        logger.debug("Successfully registered main blueprint")
    except Exception as e:
        logger.error(f"Error registering blueprint: {str(e)}")
        raise
    
    return app 