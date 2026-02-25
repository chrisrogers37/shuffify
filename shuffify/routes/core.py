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
    get_db_user,
    log_activity,
)
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    UserService,
    LoginHistoryService,
    DashboardService,
    AuthenticationError,
    PlaylistError,
)
from shuffify.services.playlist_preference_service import (
    PlaylistPreferenceService,
)
from shuffify.enums import ActivityType

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

            # Fetch personalized dashboard data (non-blocking)
            dashboard_data = {}
            preferences = {}
            visible_playlists = playlists
            hidden_playlists = []
            try:
                db_user = get_db_user()
                if db_user:
                    dashboard_data = (
                        DashboardService.get_dashboard_data(
                            user_id=db_user.id,
                            last_login_at=getattr(
                                db_user,
                                "last_login_at",
                                None,
                            ),
                        )
                    )
                    preferences = (
                        PlaylistPreferenceService
                        .get_user_preferences(db_user.id)
                    )
                    if preferences:
                        visible_playlists, hidden_playlists = (
                            PlaylistPreferenceService
                            .apply_preferences(
                                playlists, preferences
                            )
                        )
            except Exception as e:
                logger.warning(
                    "Failed to load dashboard data: "
                    "%s. Rendering without "
                    "personalization.",
                    e,
                )

            logger.debug(
                f"User {user.get('display_name', 'Unknown')} "
                f"loaded dashboard"
            )
            return render_template(
                "dashboard.html",
                playlists=visible_playlists,
                hidden_playlists=hidden_playlists,
                user=user,
                algorithms=algorithms,
                dashboard=dashboard_data,
                preferences=preferences,
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
    from shuffify import is_db_available

    db_healthy = is_db_available()
    overall_status = "healthy" if db_healthy else "degraded"

    return (
        jsonify({
            "status": overall_status,
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
        _, user_data = (
            AuthService.authenticate_and_get_user(token_data)
        )
        session["user_data"] = user_data

        # Upsert user record in database (non-blocking)
        is_new_user = False
        try:
            result = UserService.upsert_from_spotify(
                user_data
            )
            is_new_user = result.is_new
        except Exception as e:
            # Database failure should NOT block login
            logger.warning(
                f"Failed to upsert user to database: {e}. "
                f"Login continues without persistence."
            )

        session["is_new_user"] = is_new_user

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

        # Record login event (non-blocking)
        try:
            db_user = UserService.get_by_spotify_id(
                user_data["id"]
            )
            if db_user:
                flask_session_id = getattr(
                    session, "sid", None
                )
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
        try:
            db_user = UserService.get_by_spotify_id(
                user_data["id"]
            )
            if db_user:
                log_activity(
                    user_id=db_user.id,
                    activity_type=ActivityType.LOGIN,
                    description=(
                        "Logged in via Spotify OAuth"
                    ),
                )
        except Exception:
            pass

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
    # Record logout event before clearing session
    try:
        user_data = session.get("user_data")
        if user_data and user_data.get("id"):
            db_user = UserService.get_by_spotify_id(
                user_data["id"]
            )
            if db_user:
                flask_session_id = getattr(
                    session, "sid", None
                )
                LoginHistoryService.record_logout(
                    user_id=db_user.id,
                    session_id=flask_session_id,
                )
    except Exception as e:
        logger.warning(
            f"Failed to record logout: {e}. "
            f"Logout continues."
        )

    # Log logout activity (non-blocking)
    try:
        user_data = session.get("user_data", {})
        spotify_id = user_data.get("id")
        if spotify_id:
            db_user = UserService.get_by_spotify_id(
                spotify_id
            )
            if db_user:
                log_activity(
                    user_id=db_user.id,
                    activity_type=ActivityType.LOGOUT,
                    description="Logged out",
                )
    except Exception:
        pass

    session.clear()
    return redirect(url_for("main.index"))
