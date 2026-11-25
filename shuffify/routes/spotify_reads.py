"""Spend a shared account budget at Spotify data dispatch, after cache lookup."""

from urllib.parse import urlsplit

from flask import current_app, make_response
from flask_limiter.errors import RateLimitExceeded

from shuffify.error_handlers import seconds_until_limit_resets
from shuffify.spotify.transport import before_spotify_reads


class _ReadLimitReached(BaseException):
    """Cancel this view even through service-level 'except Exception' fallbacks."""


def run_with_read_limit(view, refuse, *args, **kwargs):
    limit = current_app.extensions.get("spotify_read_limit")
    if limit is None:
        return view(*args, **kwargs)

    admitted = set()

    def charge(url):
        # Paging parameters, retries and repeat observations of the same endpoint
        # belong to the resource already admitted in this view.
        target = urlsplit(url)
        resource = (target.scheme, target.netloc, target.path)
        if resource in admitted:
            return
        try:
            with limit:
                admitted.add(resource)
        except RateLimitExceeded as error:
            raise _ReadLimitReached() from error

    with before_spotify_reads(charge):
        try:
            return view(*args, **kwargs)
        except _ReadLimitReached:
            response = make_response(refuse("Too many Spotify reads. Try again after the retry delay.", 429))
            # A full fixed window is safe if Redis denied only the reset-time lookup.
            response.retry_after = seconds_until_limit_resets() or 60
            return response
