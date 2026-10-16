"""Check the selected container's local HTTP readiness without credentials."""

import json
import os
import sys
from urllib.request import ProxyHandler, build_opener


def main():
    role = os.getenv("SHUFFIFY_PROCESS_ROLE", "api")
    if role not in {"api", "worker"}:
        return 1
    try:
        port = int(os.getenv("WORKER_CONTROL_PORT", "5001") if role == "worker" else os.getenv("PORT", "8000"))
        if not 1 <= port <= 65535:
            return 1
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=2) as reply:
            value = json.loads(reply.read(4097))
            expected = "ready" if role == "worker" else "healthy"
            return 0 if reply.status == 200 and value.get("status") == expected else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
