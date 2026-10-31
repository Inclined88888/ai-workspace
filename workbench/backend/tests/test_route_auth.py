"""Every /api route must declare its auth, so a new route can't ship open by accident (audit AIW-001).

Routers carry auth as dependencies (require_user, require_device, require_user_or_cli, ...). The few routes
that authenticate inside the handler (desktop token, pairing code, sealed envelope) or must be reachable
before sign-in are listed here explicitly; adding to this list is a reviewed decision.
"""

from fastapi.routing import APIRoute

PUBLIC = {
    ("POST", "/api/anywhere/native-grab"),  # X-Desktop-Token checked in handler
    ("GET", "/api/desktop/handoff"),         # one-time code checked in handler
    ("POST", "/api/desktop/handoff"),        # desktop bearer token checked in handler
    ("GET", "/api/desktop/session"),         # desktop token checked in handler
    ("POST", "/api/login"),
    ("POST", "/api/logout"),
    ("POST", "/api/mobile/link/sealed"),     # device token inside the sealed envelope
    ("POST", "/api/mobile/pair/claim"),      # short-lived pairing code
    ("GET", "/api/mobile/ping"),
    ("POST", "/api/setup"),
    ("GET", "/api/status"),
}


def _has_dependency(dependant) -> bool:
    return any(d.call is not None or _has_dependency(d) for d in dependant.dependencies)


def _api_routes(app):
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api"):
            for m in r.methods - {"HEAD", "OPTIONS"}:
                yield m, r.path, r


def test_every_api_route_declares_auth(client):
    open_routes = sorted((m, p) for m, p, r in _api_routes(client.app) if not _has_dependency(r.dependant) and (m, p) not in PUBLIC)
    assert not open_routes, f"/api routes with no auth dependency (add one, or allowlist with a reason): {open_routes}"


def test_public_allowlist_has_no_stale_entries(client):
    present = {(m, p) for m, p, _ in _api_routes(client.app)}
    assert not PUBLIC - present, f"stale allowlist entries: {sorted(PUBLIC - present)}"


def test_signed_out_requests_to_protected_routes_are_refused(client):
    from starlette.testclient import TestClient
    anon = TestClient(client.app)
    for path in ("/api/compression/status", "/api/credentials/status", "/api/ratelimit/status", "/api/settings"):
        r = anon.get(path)
        assert r.status_code == 401, (path, r.status_code)


def test_phase1_status_endpoints_work_when_signed_in(client):
    for path in ("/api/compression/status", "/api/credentials/status", "/api/ratelimit/status"):
        assert client.get(path).status_code == 200, path


def test_pooled_credentials_are_encrypted_at_rest_and_never_listed(client):
    from app import credentials, db

    pool = credentials.get_pool("routetest")
    cred = pool.add("sk-route-test-secret-123456", name="t")
    assert pool.add("sk-route-test-secret-123456").id == cred.id  # no duplicate rows
    row = db.one("SELECT key_enc FROM credentials WHERE id = ?", cred.id)
    assert "sk-route-test-secret" not in row["key_enc"]
    assert credentials.CredentialPool("routetest").get_next().key == "sk-route-test-secret-123456"
    pool.mark_failure(cred, "401 bad key sk-route-test-secret-123456")
    assert "sk-route-test-secret" not in str(pool.list())
    pool.remove(cred.id)
