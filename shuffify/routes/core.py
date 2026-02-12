"""
Core routes: home page, static pages, health check, authentication.
"""

import logging
from datetime import datetime, timezone

from flask import (
    session,
    redirect,
    url_for,
    request,
    flash,
    jsonify,
    render_template,
    send_from_directory,
)

from shuffify.routes import (
    main,
    is_authenticated,
    clear_session_and_show_login,
)
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    UserService,
    AuthenticationError,
    PlaylistError,
)
logger = logging.getLogger(__name__)


# =============================================================================
# Public Routes
# =============================================================================


@main.route("/")
def index():
    """Home page - shows login or dashboard based on auth state."""
    try:
        logger.debug("Index route accessed")

        if not is_authenticated():
            logger.debug("No valid token, showing login page")
            session.pop("_flashes", None)
            return render_template("index.html")

        try:
            client = AuthService.get_authenticated_client(
                session["spotify_token"]
            )
            user = AuthService.get_user_data(client)

            playlist_service = PlaylistService(client)
            playlists = playlist_service.get_user_playlists()

            algorithms = ShuffleService.list_algorithms()

            logger.debug(
                f"User {user.get('display_name', 'Unknown')} "
                f"loaded dashboard"
            )
            return render_template(
                "dashboard.html",
                playlists=playlists,
                user=user,
                algorithms=algorithms,
            )

        except (AuthenticationError, PlaylistError) as e:
            logger.error(f"Error loading dashboard: {e}")
            return clear_session_and_show_login(
                "Your session has expired. Please log in again."
            )

    except Exception as e:
        logger.error(
            f"Unexpected error in index route: {e}",
            exc_info=True,
        )
        return clear_session_and_show_login()


@main.route("/terms")
def terms():
    """Terms of Service page."""
    return send_from_directory("static/public", "terms.html")


@main.route("/privacy")
def privacy():
    """Privacy Policy page."""
    return send_from_directory("static/public", "privacy.html")


@main.route("/health")
def health():
    """Health check endpoint for Docker and monitoring."""
    return (
        jsonify({
            "status": "healthy",
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),
        }),
        200,
    )


# =============================================================================
# Authentication Routes
# =============================================================================


@main.route("/login")
def login():
    """Initiate Spotify OAuth flow."""
    try:
        # Check for legal consent
        if not request.args.get("legal_consent"):
            flash(
                "You must agree to the Terms of Service and "
                "Privacy Policy to use Shuffify.",
                "error",
            )
            return redirect(url_for("main.index"))

        # Clear any existing session data
        session.pop("spotify_token", None)
        session.pop("user_data", None)
        session.modified = True

        auth_url = AuthService.get_auth_url()
        logger.debug(
            f"Redirecting to Spotify auth: {auth_url}"
        )
        return redirect(auth_url)

    except AuthenticationError as e:
        logger.error(f"Login error: {e}")
        flash(
            "An error occurred during login. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("main.index"))


@main.route("/callback")
def callback():
    """Handle OAuth callback from Spotify."""
    logger.debug(
        f"Callback received with args: {request.args}"
    )

    # Check for OAuth errors
    error = request.args.get("error")
    if error:
        logger.error(f"OAuth error: {error}")
        flash(
            f"OAuth Error: "
            f"{request.args.get('error_description', 'Unknown error')}",
            "error",
        )
        return redirect(url_for("main.index"))

    # Get authorization code
    code = request.args.get("code")
    if not code:
        logger.error("No authorization code in callback")
        flash(
            "No authorization code received from Spotify. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("main.index"))

    try:
        # Exchange code for token
        token_data = AuthService.exchange_code_for_token(
            code
        )
        session["spotify_token"] = token_data

        # Validate by fetching user data
        client, user_data = (
            AuthService.authenticate_and_get_user(token_data)
        )
        session["user_data"] = user_data

        # Upsert user record in database (non-blocking)
        try:
            UserService.upsert_from_spotify(user_data)
        except Exception as e:
            # Database failure should NOT block login
            logger.warning(
                f"Failed to upsert user to database: {e}. "
                f"Login continues without persistence."
            )

        # Store encrypted refresh token for scheduled ops
        if token_data.get("refresh_token"):
            try:
                from shuffify.services.token_service import (
                    TokenService,
                )
                from shuffify.models.db import db as _db

                db_user = UserService.get_by_spotify_id(
                    user_data["id"]
                )
                if (
                    db_user
                    and TokenService.is_initialized()
                ):
                    db_user.encrypted_refresh_token = (
                        TokenService.encrypt_token(
                            token_data["refresh_token"]
                        )
                    )
                    _db.session.commit()
                    logger.debug(
                        f"Stored encrypted refresh token "
                        f"for user {user_data['id']}"
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to store refresh token: {e}. "
                    f"Scheduled operations may not work."
                )

        session.modified = True

        logger.info(
            f"User "
            f"{user_data.get('display_name', 'Unknown')} "
            f"authenticated successfully"
        )
        return redirect(url_for("main.index"))

    except AuthenticationError as e:
        logger.error(f"Authentication failed: {e}")
        session.pop("spotify_token", None)
        session.pop("user_data", None)
        flash(
            "Error connecting to Spotify. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("main.index"))


@main.route("/logout")
def logout():
    """Clear session and log out."""
    session.clear()
    return redirect(url_for("main.index"))
