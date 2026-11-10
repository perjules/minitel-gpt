"""The shared visual assets are available to every console page."""
import re

from services import admin_ui


def test_console_pages_and_skin_assets_are_served(console):
    client, _, _ = console
    routes = (
        "/?section=dash", "/?section=ai", "/?section=services",
        "/?section=maintenance", "/services/gmail", "/users", "/pronote",
        "/admin/rose", "/test?mode=gmail", "/test?mode=conversation", "/journal",
    )
    for route in routes:
        response = client.get(route)
        assert response.status_code == 200, route
        assert b'/assets/aizone/admin-skin.css' in response.data, route
    anonymous = admin_ui.app.test_client()
    assert b'/assets/aizone/admin-skin.css' in anonymous.get('/login').data
    css = anonymous.get('/assets/aizone/admin-skin.css')
    assert css.status_code == 200
    for asset in re.findall(r'url\(([^)]+)\)', css.text):
        assert anonymous.get('/assets/aizone/' + asset.strip('"\'')).status_code == 200, asset
