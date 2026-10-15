"""Authored notices exchanged through the session after OAuth redirects."""

AUTH_NOTICE_MESSAGES = {
    "access_denied": "Spotify access was not granted, so you have not been logged in.",
    "oauth_failed": "Spotify sign-in did not complete. Please try again.",
    "missing_code": "No authorization code received from Spotify. Please try again.",
    "invalid_state": "Authentication failed due to a security check. Please try again.",
    "authentication_failed": "Error connecting to Spotify. Please try again.",
    "consent_required": "You must agree to the Terms of Service and Privacy Policy to use Shuffify.",
    "login_failed": "An error occurred during login. Please try again.",
}


def auth_notice(code):
    """Resolve a known notice code without accepting arbitrary session or query text."""
    if not isinstance(code, str) or code not in AUTH_NOTICE_MESSAGES:
        return None
    return {"code": code, "message": AUTH_NOTICE_MESSAGES[code], "category": "error"}
