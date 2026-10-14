import os

from shuffify import create_app

# This file exists solely for gunicorn to have a WSGI entry point
# Defaults to development so `python run.py` is a working local server.
# Production does not rely on this default -- the Dockerfile sets
# APP_CONFIG=production explicitly. create_app() defaults to production for
# the same reason in reverse: an embedder that forgets to choose should get
# the safe config, while a developer running this file should not have to.
app = create_app(os.getenv("APP_CONFIG", "development"))

if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("FLASK_PORT", 8000)),
        debug=app.debug,
        use_reloader=os.getenv("FLASK_USE_RELOADER", "true").lower() == "true",
    )
