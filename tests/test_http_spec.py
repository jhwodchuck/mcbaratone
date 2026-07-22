from pathlib import Path

import yaml

from baritone_client.http_api.spec import export_openapi_yaml, load_endpoints, parse_overview_html


def test_parse_overview_html_round_trip():
    html = Path("src/baritone_client/http_api/data/overview-tree.html").read_text(encoding="utf-8")
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


def test_export_openapi_yaml_is_valid_yaml():
    endpoints = load_endpoints()
    openapi_yaml = export_openapi_yaml(endpoints)
    loaded = yaml.safe_load(openapi_yaml)
    assert loaded["openapi"].startswith("3.")
    assert "/api/status" in loaded["paths"]

