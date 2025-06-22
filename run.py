from shuffify import create_app
from config import config

# This file exists solely for gunicorn to have a WSGI entry point
app = create_app('development')

if __name__ == '__main__':
    app.run(
        host=app.config['HOST'],
        port=app.config['PORT'],
        debug=app.config['DEBUG']
    ) 