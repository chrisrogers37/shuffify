"""The test PostgreSQL for the tests that need the real server: locks, LISTEN and transactional DDL.

``tests/conftest.py`` registers this module (``pytest_plugins``), so ``postgres_url`` needs no
import. The tests that use it carry the ``integration`` marker, which the unit run deselects; CI
runs them in its PostgreSQL integration step, where the runner's PostgreSQL binaries are on PATH.

The fixture yields the URL of a server on this machine, from one of two places:

* ``SHUFFIFY_TEST_POSTGRES_URL``, an isolated local database that the caller already runs; or
* a throwaway cluster that ``initdb`` and ``pg_ctl`` from PATH start on a free loopback port for
  one test module and stop after it.

With neither, the tests skip. A ``--fail-on-skip`` run (tests/fail_on_skip.py) fails on that skip
instead of passing without having tested PostgreSQL.
"""

import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa

# The only hosts a test PostgreSQL may be on. A test that creates schemas or runs DDL asserts it too.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@pytest.fixture(scope="module")
def postgres_url():
    supplied = os.environ.get("SHUFFIFY_TEST_POSTGRES_URL")
    if supplied:
        assert sa.make_url(supplied).host in LOCAL_HOSTS, "Use a local PostgreSQL test server."
        yield supplied
        return
    initdb = shutil.which("initdb")
    pg_bin = Path(initdb).parent if initdb else None
    if pg_bin is None or not (pg_bin / "pg_ctl").is_file():
        pytest.skip("Local PostgreSQL binaries or SHUFFIFY_TEST_POSTGRES_URL are required.")
    with tempfile.TemporaryDirectory(prefix="shuffify-queue-pg-") as directory:
        data = Path(directory) / "data"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        subprocess.run([str(pg_bin / "initdb"), "-D", str(data), "--auth=trust", "--no-locale", "-E", "UTF8"],
                       check=True, capture_output=True)
        # Loopback TCP only, and the Unix socket inside the temporary directory.
        subprocess.run([str(pg_bin / "pg_ctl"), "-D", str(data), "-l", str(Path(directory) / "postgres.log"),
                        "-o", f"-h 127.0.0.1 -p {port} -k {directory}", "-w", "start"],
                       check=True, capture_output=True)
        try:
            yield f"postgresql+psycopg2://{os.environ.get('USER', 'postgres')}@127.0.0.1:{port}/postgres"
        finally:
            subprocess.run([str(pg_bin / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"],
                           check=True, capture_output=True)
