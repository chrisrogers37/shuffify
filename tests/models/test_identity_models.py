"""Workspaces and Spotify connections: constraints, defaults and the unchanged users table."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from shuffify.models.db import SpotifyConnection, User, Workspace, db
from tests.postgres_server import LOCAL_HOSTS

REFRESH_TOKEN = "encrypted-refresh-token-fake"
IDENTITY_TABLES = [User.__table__, Workspace.__table__, SpotifyConnection.__table__]


@pytest.fixture
def user(db_app):
    return add_user(spotify_id="spotify-user-1", display_name="Test User")


def add_user(**values):
    person = User(**values)
    db.session.add(person)
    db.session.commit()
    return person


def add_workspace(user, **values):
    workspace = Workspace(user_id=user.id, **values)
    db.session.add(workspace)
    db.session.commit()
    return workspace


def add_connection(workspace, **values):
    values.setdefault("client_id", "test-client-id")
    connection = SpotifyConnection(workspace_id=workspace.id, **values)
    db.session.add(connection)
    db.session.commit()
    return connection


def refused(add):
    """The text of the IntegrityError that ``add`` raises."""
    with pytest.raises(IntegrityError) as raised:
        add()
    db.session.rollback()
    return str(raised.value)


class TestUniqueness:
    def test_google_sub_is_unique(self, db_app):
        add_user(spotify_id="spotify-user-1", google_sub="google-sub-1")

        message = refused(lambda: add_user(spotify_id="spotify-user-2", google_sub="google-sub-1"))

        assert "google_sub" in message

    def test_users_without_google_sub_coexist(self, db_app):
        db.session.add_all([User(spotify_id="spotify-user-1"), User(spotify_id="spotify-user-2")])
        db.session.commit()

        assert User.query.filter(User.google_sub.is_(None)).count() == 2

    def test_a_workspace_has_one_connection(self, user):
        workspace = add_workspace(user)
        add_connection(workspace)

        message = refused(lambda: add_connection(workspace, client_id="test-client-id-2"))

        assert "workspace_id" in message

    def test_spotify_id_is_unique_across_connections(self, user):
        first, second = add_workspace(user), add_workspace(user)
        add_connection(first, spotify_id="spotify-user-1")

        message = refused(lambda: add_connection(second, spotify_id="spotify-user-1"))

        assert "spotify_id" in message

    def test_connections_without_spotify_id_coexist(self, user):
        first, second = add_workspace(user), add_workspace(user)
        add_connection(first)
        add_connection(second)

        assert SpotifyConnection.query.filter(SpotifyConnection.spotify_id.is_(None)).count() == 2

    def test_a_user_has_several_workspaces(self, user):
        add_workspace(user)
        add_workspace(user)

        assert Workspace.query.filter_by(user_id=user.id).count() == 2

    def test_connections_in_different_workspaces_may_share_a_client_id(self, user):
        first, second = add_workspace(user), add_workspace(user)
        add_connection(first, client_id="test-client-id")
        add_connection(second, client_id="test-client-id")

        assert SpotifyConnection.query.filter_by(client_id="test-client-id").count() == 2


class TestChecks:
    def test_unknown_status_is_refused(self, user):
        workspace = add_workspace(user)

        message = refused(lambda: add_connection(workspace, status="unknown"))

        assert "ck_spotify_connections_status" in message

    def test_unknown_reason_is_refused(self, user):
        workspace = add_workspace(user)

        message = refused(lambda: add_connection(workspace, status="broken", broken_reason="unknown"))

        assert "ck_spotify_connections_broken_reason" in message

    def test_broken_without_a_reason_is_refused(self, user):
        workspace = add_workspace(user)

        message = refused(lambda: add_connection(workspace, status="broken"))

        assert "ck_spotify_connections_broken_has_reason" in message

    @pytest.mark.parametrize("status", ["unchecked", "ok"])
    def test_a_reason_on_a_row_that_is_not_broken_is_refused(self, user, status):
        workspace = add_workspace(user)

        message = refused(lambda: add_connection(workspace, status=status, broken_reason="app_rejected"))

        assert "ck_spotify_connections_broken_has_reason" in message

    @pytest.mark.parametrize("reason", ["app_rejected", "access_revoked", "user_not_allowed"])
    def test_broken_with_each_reason_is_accepted(self, user, reason):
        connection = add_connection(add_workspace(user), status="broken", broken_reason=reason)

        assert (connection.status, connection.broken_reason) == ("broken", reason)


class TestDefaults:
    def test_workspace_name(self, user):
        assert add_workspace(user).name == "My workspace"

    def test_new_connection(self, user):
        before = datetime.now(timezone.utc)

        connection = add_connection(add_workspace(user))
        db.session.expire_all()
        saved = db.session.get(SpotifyConnection, connection.id)

        assert saved.credential_generation == 0
        assert saved.status == "unchecked"
        assert saved.broken_reason is None
        assert saved.status_changed_at >= before
        assert saved.spotify_id is None

    def test_repr_leaves_out_the_refresh_token(self, user):
        connection = add_connection(add_workspace(user), encrypted_refresh_token=REFRESH_TOKEN)

        assert REFRESH_TOKEN not in repr(connection)
        assert "SpotifyConnection" in repr(connection)


class TestUsersAreUnchanged:
    def test_a_spotify_user_inserts_and_loads_as_before(self, db_app):
        db.session.add(User(spotify_id="spotify-user-1", encrypted_refresh_token=REFRESH_TOKEN))
        db.session.commit()
        db.session.expire_all()

        saved = User.query.filter_by(spotify_id="spotify-user-1").one()

        assert saved.encrypted_refresh_token == REFRESH_TOKEN
        assert saved.google_sub is None
        assert saved.email_verified is False

    def test_users_keep_their_spotify_columns(self):
        names = set(User.__table__.columns.keys())

        assert {"spotify_id", "country", "spotify_product", "spotify_uri", "encrypted_refresh_token"} <= names
        assert not User.__table__.columns["spotify_id"].nullable


def exercise_cascade(connection):
    """Deleting a person removes their workspaces, and a workspace takes its connection with it."""
    db.metadata.create_all(connection, tables=IDENTITY_TABLES)
    users, workspaces, connections = IDENTITY_TABLES
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    stamps = {"created_at": now, "updated_at": now}
    for user_id in (1, 2):
        connection.execute(users.insert().values(
            id=user_id, spotify_id=f"spotify-user-{user_id}", login_count=0, is_active=True,
            email_verified=False, **stamps,
        ))
        connection.execute(workspaces.insert().values(id=user_id, user_id=user_id, name="My workspace", **stamps))
        connection.execute(connections.insert().values(
            id=user_id, workspace_id=user_id, client_id="test-client-id", status="unchecked",
            status_changed_at=now, credential_generation=0, **stamps,
        ))

    connection.execute(users.delete().where(users.c.id == 1))

    assert connection.scalars(sa.select(workspaces.c.id)).all() == [2]
    assert connection.scalars(sa.select(connections.c.id)).all() == [2]
    connection.execute(workspaces.delete().where(workspaces.c.id == 2))
    assert connection.scalars(sa.select(connections.c.id)).all() == []
    assert connection.scalars(sa.select(users.c.id)).all() == [2]


@pytest.mark.parametrize("backend", ["sqlite", pytest.param("postgres", marks=pytest.mark.integration)])
def test_deleting_a_user_cascades_to_workspaces_and_connections(backend, request):
    url = request.getfixturevalue("postgres_url") if backend == "postgres" else "sqlite://"
    assert backend == "sqlite" or sa.make_url(url).host in LOCAL_HOSTS
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            if backend == "postgres":
                schema = "identity_cascade_" + uuid4().hex
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            else:
                # SQLite enforces foreign keys only once this pragma is on, per connection.
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            exercise_cascade(connection)
            transaction.rollback()
    finally:
        engine.dispose()
