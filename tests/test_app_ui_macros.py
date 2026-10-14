"""Shared task-view and Spotify-link macros (macros/app_ui.html)."""

from flask import render_template_string

TASK = (
    '{% from "macros/app_ui.html" import task_panel %}'
    '{% call(section) task_panel("tool", "Tool", full=FULL) %}'
    '{% if section == "body" %}<p>Body</p>{% elif section == "footer" %}FOOTER{% endif %}'
    "{% endcall %}"
)


def _render(app, source, **context):
    with app.test_request_context("/"):
        return render_template_string(source, **context)


def test_a_task_whose_footer_branch_renders_nothing_has_no_footer(app):
    with_footer = _render(app, TASK.replace("FOOTER", "<button>Save</button>").replace("FULL", "false"))
    assert '<footer class="sh-panel-footer"><button>Save</button></footer>' in with_footer
    # Whitespace alone, as a caller's {% endif %}{% endcall %} layout can leave, is not a footer.
    without = _render(app, TASK.replace("FOOTER", "\n   \n").replace("FULL", "false"))
    assert "<footer" not in without and "<p>Body</p>" in without


def test_a_full_width_task_is_marked_for_the_panels_layout(app):
    assert "data-panel-full" in _render(app, TASK.replace("FOOTER", "").replace("FULL", "true"))
    assert "data-panel-full" not in _render(app, TASK.replace("FOOTER", "").replace("FULL", "false"))


def test_the_spotify_link_is_the_official_logo_opening_a_new_tab(app):
    html = _render(
        app,
        '{% from "macros/app_ui.html" import spotify_link %}{{ spotify_link(url, name, group) }}',
        url="https://open.spotify.com/playlist/abc", name="<Mix & Match>", group="view",
    )
    assert 'href="https://open.spotify.com/playlist/abc"' in html
    assert 'target="_blank" rel="noopener noreferrer"' in html
    assert 'class="sh-spotify-link"' in html and 'data-workshop-compat="view"' in html
    assert 'aria-label="Open &lt;Mix &amp; Match&gt; on Spotify (new tab)"' in html
    assert 'class="sh-spotify-logo" src="/static/images/spotify-logo-white.svg" alt="Spotify"' in html
    plain = _render(app, '{% from "macros/app_ui.html" import spotify_link %}{{ spotify_link("u", "n") }}')
    assert "data-workshop-compat" not in plain
