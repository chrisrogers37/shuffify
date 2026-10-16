"""Deadline-aware response sockets for Requests, including trickling HTTP bodies."""

import io
from functools import lru_cache
from http.client import HTTPResponse

import requests
from requests.adapters import HTTPAdapter

from .deadline import remaining_seconds


class _DeadlineReader(io.RawIOBase):
    def __init__(self, raw, sock):
        self._raw = raw
        self._socket = sock

    def readable(self):
        return True

    def fileno(self):
        return self._raw.fileno()

    def readinto(self, buffer):
        remaining = remaining_seconds()
        if remaining is None:
            return self._raw.readinto(buffer)
        if remaining <= 0:
            raise TimeoutError('Spotify response deadline exceeded.')
        previous_timeout = self._socket.gettimeout()
        self._socket.settimeout(min(previous_timeout, remaining) if previous_timeout is not None else remaining)
        try:
            return self._raw.readinto(buffer)
        finally:
            try:
                self._socket.settimeout(previous_timeout)
            except OSError:
                pass

    def close(self):
        try:
            self._raw.close()
        finally:
            super().close()


class _ResponseSocket:
    def __init__(self, sock):
        self._socket = sock

    def makefile(self, mode):
        raw = self._socket.makefile(mode, buffering=0)
        return io.BufferedReader(_DeadlineReader(raw, self._socket))

    def __getattr__(self, name):
        return getattr(self._socket, name)


class _DeadlineResponse(HTTPResponse):
    def __init__(self, sock, *args, **kwargs):
        super().__init__(_ResponseSocket(sock), *args, **kwargs)


@lru_cache(maxsize=None)
def _deadline_pool(pool_class):
    """Retain each pool's connection behavior, including HTTPS and configured proxies."""
    if getattr(pool_class, "_spotify_deadline_pool", False):
        return pool_class

    class Connection(pool_class.ConnectionCls):
        response_class = _DeadlineResponse

    class Pool(pool_class):
        ConnectionCls = Connection
        _spotify_deadline_pool = True

    return Pool


def _configure_pools(manager):
    manager.pool_classes_by_scheme = {
        scheme: _deadline_pool(pool_class) for scheme, pool_class in manager.pool_classes_by_scheme.items()
    }


class DeadlineHTTPAdapter(HTTPAdapter):
    """Recompute the shared budget before every socket read, including header reads."""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        _configure_pools(self.poolmanager)

    def proxy_manager_for(self, *args, **kwargs):
        manager = super().proxy_manager_for(*args, **kwargs)
        _configure_pools(manager)
        return manager


def request_with_deadline(unscoped_request, method, url, **kwargs):
    """Use a bounded response reader in a scope, retaining native transport outside it."""
    if remaining_seconds() is None:
        return unscoped_request(url, **kwargs)
    with requests.Session() as session:
        adapter = DeadlineHTTPAdapter()
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session.request(method, url, **kwargs)
