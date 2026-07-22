import responses

from baritone_client.http_api.client import BaritoneClient, BaritoneError


@responses.activate
def test_login_sets_token():
    responses.add(
        responses.POST,
        "http://example.com/api/login",
        json={"token": "abc123", "expires_in": 300},
        status=200,
    )
    client = BaritoneClient("http://example.com")
    data = client.login("user", "pass")
    assert data["token"] == "abc123"
    assert client.token == "abc123"


@responses.activate
def test_goto_sends_payload():
    responses.add(
        responses.POST,
        "http://example.com/api/path/goto",
        json={"task_id": "t1", "state": "submitted"},
        status=200,
    )
    client = BaritoneClient("http://example.com", token="abc")
    payload = client.goto(1, 64, 2, dimension="overworld")
    assert payload["task_id"] == "t1"
    sent = responses.calls[0].request
    assert sent.headers["Authorization"] == "Bearer abc"


@responses.activate
def test_error_raises_baritone_error():
    responses.add(
        responses.GET,
        "http://example.com/api/status",
        json={"error": "no session"},
        status=401,
    )
    client = BaritoneClient("http://example.com", token="bad")
    try:
        client.status()
    except BaritoneError as exc:
        assert exc.status_code == 401
        assert exc.payload == {"error": "no session"}
    else:
        raise AssertionError("Expected BaritoneError to be raised")

