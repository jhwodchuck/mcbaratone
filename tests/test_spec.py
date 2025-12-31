from pathlib import Path

from baritone_client.spec import load_endpoints, parse_overview_html


def test_parse_overview_html_round_trip():
    html = Path("baritone_client/data/overview-tree.html").read_text(encoding="utf-8")
    endpoints = parse_overview_html(html)
    assert endpoints, "Expected endpoints to be parsed from HTML"
    names = {ep.name for ep in endpoints}
    assert "login" in names
    assert "goto" in names


def test_load_endpoints_merges_json_and_html():
    endpoints = load_endpoints()
    names = {ep.name for ep in endpoints}
    assert "settings-set" in names
    assert all(ep.path.startswith("/api") for ep in endpoints)
