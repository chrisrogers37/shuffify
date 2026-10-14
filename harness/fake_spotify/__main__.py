"""Serve the fake Spotify: ``python -m harness.fake_spotify --port 8900``.

It listens on 127.0.0.1 only, and every link it writes points there.
"""

import argparse
import sys

from werkzeug.serving import make_server

from .app import create_app

HOST = "127.0.0.1"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Serve the fake Spotify on 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument(
        "--redirect-uri", action="append", default=[], dest="redirect_uris",
        help="an OAuth redirect URI to accept, as registered with a real Spotify app (repeatable)",
    )
    args = parser.parse_args(argv)

    origin = f"http://{HOST}:{args.port}"
    server = make_server(HOST, args.port, create_app(redirect_uris=args.redirect_uris, origin=origin),
                         threaded=True)
    print(f"fake Spotify on {origin}", flush=True)
    server.serve_forever()  # returns on Ctrl-C
    return 0


if __name__ == "__main__":
    sys.exit(main())
