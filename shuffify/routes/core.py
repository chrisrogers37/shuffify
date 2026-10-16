"""
OAuth authentication and process health routes.
"""

import hmac
import logging
import secrets
from datetime import datetime, timezone

from flask import (
    current_app,
    jsonify,
    redirect,
    request,
    session,
)

from shuffify.enums import ActivityType
from shuffify.routes import (
    auth_bp,
    get_db_user,
    log_activity,
    site_bp,
)
from shuffify.services import (
    AuthenticationError,
    AuthService,
    LoginHistoryService,
    UserService,
)
from shuffify.services.auth_notice import auth_notice

logger = logging.getLogger(__name__)


def _auth_notice(code):
    """Store one authored OAuth notice for the session API."""
    if auth_notice(code) is not None:
        session["auth_notice"] = code


# =============================================================================
# Process Health
# =============================================================================


@site_bp.route("/health")
def health():
    """Health check endpoint for Docker and monitoring."""
    from shuffify import is_db_available

    db_healthy = is_db_available()

    return (
        jsonify(
            {
                "status": "healthy" if db_healthy else "degraded",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ),
        200,
    )


# =============================================================================
# Authentication Routes
# =============================================================================


@auth_bp.route("/login")
def login():
    """Initiate Spotify OAuth flow."""
    try:
        # Check for legal consent
        if not request.args.get("legal_consent"):
            _auth_notice("consent_required")
            return redirect("/")

        # Clear any existing session data
        session.pop("spotify_token", None)
        session.pop("user_data", None)

        state = secrets.token_urlsafe(32)
        session["oauth_state"] = state
        session.modified = True

        auth_url = AuthService.get_auth_url(state=state)
        logger.debug(f"Redirecting to Spotify auth: {auth_url}")
        return redirect(auth_url)

    except AuthenticationError as e:
        logger.error(f"Login error: {e}")
        _auth_notice("login_failed")
        return redirect("/")


@auth_bp.route("/callback")
def callback():
    """Handle OAuth callback from Spotify."""
    logger.debug(
        "Callback received (code=%s, state=%s, error=%s)",
        "present" if request.args.get("code") else "absent",
        "present" if request.args.get("state") else "absent",
        request.args.get("error", "none"),
    )

    # Check for OAuth errors
    error = request.args.get("error")
    if error:
        logger.error("OAuth error: %r", error[:100])
        _auth_notice("access_denied" if error == "access_denied" else "oauth_failed")
        return redirect("/")

    # Get authorization code
    code = request.args.get("code")
    if not code:
        logger.error("No authorization code in callback")
        _auth_notice("missing_code")
        return redirect("/")

    # Validate CSRF state parameter
    callback_state = request.args.get("state")
    stored_state = session.pop("oauth_state", None)
    if not stored_state or not hmac.compare_digest(stored_state, callback_state or ""):
        logger.error("OAuth state mismatch: possible CSRF attack")
        _auth_notice("invalid_state")
        return redirect("/")

    try:
        # Exchange code for token
        token_data = AuthService.exchange_code_for_token(code)

        # Validate by fetching user data
        _, user_data = AuthService.authenticate_and_get_user(token_data)

        # Regenerate session ID to prevent session fixation attacks.
        # Flask-Session's regenerate() deletes the old server-side
        # record, issues a new SID, and marks the session modified.
        # We then clear stale pre-auth data before writing auth state.
        current_app.session_interface.regenerate(session)
        session.clear()
        session["spotify_token"] = token_data
        session["user_data"] = user_data

        # Upsert user record in database (non-blocking)
        is_new_user = False
        db_user = None
        try:
            result = UserService.upsert_from_spotify(user_data)
            is_new_user = result.is_new
            db_user = result.user
            previous = result.previous_login_at
            session["previous_login_at"] = previous.isoformat() if previous else None
        except Exception as e:
            # Database failure should NOT block login
            logger.warning(
                f"Failed to upsert user to database: {e}. "
                f"Login continues without persistence."
            )

        session["is_new_user"] = is_new_user

        # Store encrypted refresh token for scheduled ops
        if db_user and token_data.get("refresh_token"):
            try:
                UserService.store_refresh_token(
                    db_user,
                    token_data["refresh_token"],
                )
                logger.debug(
                    f"Stored encrypted refresh token for user {user_data['id']}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to store refresh token: {e}. "
                    f"Scheduled operations may not work."
                )

        # Record login event (non-blocking)
        if db_user:
            try:
                flask_session_id = getattr(session, "sid", None)
                LoginHistoryService.record_login(
                    user_id=db_user.id,
                    request=request,
                    session_id=flask_session_id,
                    login_type="oauth_initial",
                )
            except Exception as e:
                # Login history failure should NOT block login
                logger.warning(
                    f"Failed to record login history: {e}. "
                    f"Login continues without history tracking."
                )

        session.modified = True

        # Log login activity (non-blocking)
        if db_user:
            log_activity(
                user_id=db_user.id,
                activity_type=ActivityType.LOGIN,
                description=("Logged in via Spotify OAuth"),
            )

        logger.info(
            f"User "
            f"{user_data.get('display_name', 'Unknown')} "
            f"authenticated successfully"
        )
        return redirect("/")

    except AuthenticationError as e:
        if "not registered" in str(e):
            logger.warning(f"Authentication failed: {e}")
        else:
            logger.error(f"Authentication failed: {e}")
        session.pop("spotify_token", None)
        session.pop("user_data", None)
        _auth_notice("authentication_failed")
        return redirect("/")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Clear session and log out.

    POST-only and CSRF-protected (via the app-wide CSRFProtect): logout is a
    state-changing operation, so a GET link or cross-site `<img src>` must not
    be able to force-logout users.
    """
    # Best-effort token revocation (fire-and-forget)
    try:
        token_data = session.get("spotify_token")
        if token_data:
            access_token = token_data.get("access_token")
            if access_token:
                AuthService.revoke_access_token(access_token)
    except Exception as e:
        logger.warning("Token revocation failed during logout: %s", e)

    # Record logout event and activity before clearing session
    try:
        db_user = get_db_user()
        if db_user:
            flask_session_id = getattr(session, "sid", None)
            LoginHistoryService.record_logout(
                user_id=db_user.id,
                session_id=flask_session_id,
            )
            log_activity(
                user_id=db_user.id,
                activity_type=ActivityType.LOGOUT,
                description="Logged out",
            )
    except Exception as e:
        logger.warning("Failed to record logout: %s", e)

    session.clear()
    return redirect("/")
