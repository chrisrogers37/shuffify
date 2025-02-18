import os
import sys
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

logger.debug(f"Python version: {sys.version}")
logger.debug(f"Python path: {sys.path}")
logger.debug(f"Current directory: {os.getcwd()}")
logger.debug(f"Directory contents: {os.listdir('.')}")

from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run() 