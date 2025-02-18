import os
import sys
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Add the current directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
    logger.debug(f"Added {current_dir} to Python path")

logger.debug(f"Python version: {sys.version}")
logger.debug(f"Python path: {sys.path}")
logger.debug(f"Current directory: {os.getcwd()}")
logger.debug(f"Directory contents: {os.listdir('.')}")
logger.debug(f"App directory contents: {os.listdir('app')}")

try:
    from app import create_app
    logger.debug("Successfully imported create_app")
    app = create_app()
    logger.debug("Successfully created app")
except Exception as e:
    logger.error(f"Error creating app: {str(e)}")
    raise

if __name__ == '__main__':
    app.run() 