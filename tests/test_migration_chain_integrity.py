"""The Alembic revision graph is unambiguous, and the guard that says so works.

Two branches can hand-pick the same revision id and git merges them silently,
because the filenames differ (#527). The app refuses to boot unless the schema
is exactly at head (#503), so an ambiguous head is a failed deploy rather than
a migration warning.

`scripts/check-migration-chain.py` is the implementation and runs in CI beside
the lint step. This module is a thin wrapper over it -- one source of truth,
two entry points -- plus the mutation tests that keep the guard honest. A check
nobody has watched fail is not a check.
"""

import pathlib
import subprocess
import sys
import textwrap

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-migration-chain.py"
VERSIONS_DIR = REPO_ROOT / "migrations" / "versions"


def run_guard(versions_dir):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--versions-dir", str(versions_dir)],
        capture_output=True,
        text=True,
    )


def write_migration(directory, revision, down_revision, name=None):
    filename = f"{name or revision}_migration.py"
    down = "None" if down_revision is None else f'"{down_revision}"'
    (directory / filename).write_text(
        textwrap.dedent(
            f'''
            """A migration."""
            revision = "{revision}"
            down_revision = {down}
            branch_labels = None
            depends_on = None


            def upgrade():
                pass


            def downgrade():
                pass
            '''
        ).lstrip()
    )
    return filename


@pytest.fixture
def chain(tmp_path):
    """A sound three-migration chain: a -> b -> c."""
    write_migration(tmp_path, "aaa111", None)
    write_migration(tmp_path, "bbb222", "aaa111")
    write_migration(tmp_path, "ccc333", "bbb222")
    return tmp_path


class TestRealMigrations:
    """The repository's own revision graph is sound."""

    def test_the_committed_chain_passes(self):
        result = run_guard(VERSIONS_DIR)
        assert result.returncode == 0, result.stderr

    def test_the_script_exists_and_is_executable(self):
        assert SCRIPT.is_file()


class TestGuardDetectsDefects:
    """Each assertion is load-bearing -- watched failing, not assumed to work."""

    def test_sound_chain_passes(self, chain):
        assert run_guard(chain).returncode == 0

    def test_duplicate_revision_id_fails(self, chain):
        """The actual near-miss: two branches pick the same id off one parent."""
        write_migration(chain, "ccc333", "bbb222", name="parallel_branch")

        result = run_guard(chain)

        assert result.returncode == 1
        assert "Duplicate revision id 'ccc333'" in result.stderr
        # The message must name the files, not just the id -- reconstructing
        # which two collided is the slow part at 2am.
        assert "ccc333_migration.py" in result.stderr
        assert "parallel_branch_migration.py" in result.stderr

    def test_fork_fails(self, chain):
        """Distinct ids, same parent: no duplicate, but still ambiguous."""
        write_migration(chain, "ddd444", "bbb222")

        result = run_guard(chain)

        assert result.returncode == 1
        assert "Fork: revision 'bbb222'" in result.stderr

    def test_two_heads_are_listed_by_id(self, chain):
        write_migration(chain, "ddd444", "bbb222")

        result = run_guard(chain)

        assert "Expected exactly 1 head, found 2" in result.stderr
        assert "ccc333" in result.stderr and "ddd444" in result.stderr

    def test_broken_chain_fails(self, chain):
        """A down_revision pointing at a revision that does not exist."""
        write_migration(chain, "eee555", "nonexistent999")

        result = run_guard(chain)

        assert result.returncode == 1
        assert "nonexistent999" in result.stderr

    def test_cycle_fails(self, tmp_path):
        """A loop produces zero heads; say so specifically rather than by count."""
        write_migration(tmp_path, "aaa111", "ccc333")
        write_migration(tmp_path, "bbb222", "aaa111")
        write_migration(tmp_path, "ccc333", "bbb222")

        result = run_guard(tmp_path)

        assert result.returncode == 1
        assert "Cycle" in result.stderr

    def test_unparseable_revision_is_reported_not_skipped(self, chain):
        """A typo'd assignment reads as 'not a migration' and would vanish."""
        (chain / "typo_migration.py").write_text('revison = "fff666"\ndown_revision = "ccc333"\n')

        result = run_guard(chain)

        assert result.returncode == 1
        assert "no parseable" in result.stderr
        assert "typo_migration.py" in result.stderr

    def test_empty_directory_fails_rather_than_passing_vacuously(self, tmp_path):
        result = run_guard(tmp_path)

        assert result.returncode == 1
        assert "No migrations found" in result.stderr


class TestGuardMessageQuality:
    """The failure has to explain itself; a bare exit code is not enough."""

    def test_duplicate_message_explains_the_silent_merge(self, chain):
        write_migration(chain, "ccc333", "bbb222", name="parallel_branch")

        stderr = run_guard(chain).stderr

        assert "Git merges this cleanly" in stderr

    def test_head_message_explains_the_deploy_consequence(self, chain):
        write_migration(chain, "ddd444", "bbb222")

        stderr = run_guard(chain).stderr

        assert "refuses to boot" in stderr
