from shuffify import create_app
import os

# This file exists solely for gunicorn to have a WSGI entry point
app = create_app(os.getenv('FLASK_ENV', 'development'))

if __name__ == '__main__':
    app.run(
        host=os.getenv('FLASK_HOST', '0.0.0.0'),
        port=int(os.getenv('FLASK_PORT', 8000)),
        debug=app.debug
    ) 