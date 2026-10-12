# =============================================================================
# File:     test_portal.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Employer portal (strand E): the gate, cookie flags, server-side
#           sessions, expiry, input sanitizing, rate limit, visits analytics.
# =============================================================================
"""Portal tests with Flask's test client and mongomock (no server needed)."""

from datetime import timedelta

import mongomock
import pytest

import app as dashboard
from shelter import ValidationError
from shelter.portal import COOKIE_NAME, EmployerPortal, RateLimiter, clean_label, hash_token, utcnow


class Clock:
    def __init__(self):
        self.now = utcnow()

    def __call__(self):
        return self.now


@pytest.fixture
def portal_env(mock_shelter):
    db = mongomock.MongoClient()["aac"]
    clock = Clock()
    portal = EmployerPortal(db, secure_cookie=True, rate_limiter=RateLimiter(max_events=5, window_seconds=60), clock=clock)
    app = dashboard.create_app(mock_shelter, portal)
    client = app.server.test_client()
    return portal, client, db, clock


def sign_in(client, name="Ada Lovelace", company="Example Co"):
    return client.post("/portal/signin", data={"name": name, "company": company})


# -- the gate ----------------------------------------------------------------------

def test_unauthenticated_root_redirects_to_signin(portal_env):
    _, client, _, _ = portal_env
    resp = client.get("/")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/portal/signin")


@pytest.mark.parametrize("path", ["/_dash-layout", "/_dash-dependencies"])
def test_unauthenticated_dash_endpoints_get_no_data(portal_env, path):
    _, client, _, _ = portal_env
    resp = client.get(path)
    assert resp.status_code == 401
    assert b"animal" not in resp.data.lower() and b"datatable" not in resp.data.lower()


def test_unauthenticated_callback_is_refused(portal_env):
    _, client, _, _ = portal_env
    resp = client.post("/_dash-update-component", json={"output": "x", "inputs": []})
    assert resp.status_code == 401


def test_signin_page_and_assets_are_public(portal_env):
    _, client, _, _ = portal_env
    page = client.get("/portal/signin")
    assert page.status_code == 200 and b"attribution, not a login" in page.data
    assert page.headers["Cache-Control"] == "no-store"
    assert client.get("/assets/style.css").status_code == 200


# -- signing in ---------------------------------------------------------------------

def test_signin_sets_hardened_cookie_and_server_side_session(portal_env):
    portal, client, db, _ = portal_env
    resp = sign_in(client)
    assert resp.status_code == 302
    cookie = resp.headers["Set-Cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=")
    for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Max-Age=7200", "Path=/"):
        assert flag in cookie
    token = cookie.split(";")[0].split("=", 1)[1]
    stored = db["portal_sessions"].find_one()
    assert stored["_id"] == hash_token(token) and stored["_id"] != token   # raw token never stored
    assert stored["expires_at"] - stored["created_at"] == timedelta(hours=2)
    assert db["visits"].count_documents({"event": "signin", "company": "Example Co"}) == 1


def test_signed_in_user_reaches_the_dashboard(portal_env):
    _, client, db, _ = portal_env
    sign_in(client)
    assert client.get("/").status_code == 200
    layout = client.get("/_dash-layout")
    assert layout.status_code == 200 and b"Signed in as Ada Lovelace (Example Co)" in layout.data
    assert db["visits"].count_documents({"event": "view"}) == 1


def test_session_expires_after_two_hours(portal_env):
    _, client, _, clock = portal_env
    sign_in(client)
    clock.now += timedelta(hours=2, seconds=1)
    assert client.get("/_dash-layout").status_code == 401


def test_forged_cookie_is_rejected(portal_env):
    _, client, _, _ = portal_env
    client.set_cookie(COOKIE_NAME, "not-a-real-session")
    assert client.get("/_dash-layout").status_code == 401


def test_signout_deletes_the_session(portal_env):
    _, client, db, _ = portal_env
    sign_in(client)
    resp = client.post("/portal/signout")
    assert resp.status_code == 302
    assert db["portal_sessions"].count_documents({}) == 0
    assert client.get("/_dash-layout").status_code == 401


# -- input and abuse --------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,company",
    [
        ("<script>alert(1)</script>", "Example Co"),
        ("Ada", "{\"$ne\": null}"),
        ("A", "Example Co"),                     # too short
        ("Ada", "x" * 81),                       # too long
        ("", ""),
        ("Ada", "Evil_Corp"),
    ],
)
def test_bad_names_are_refused(portal_env, name, company):
    _, client, db, _ = portal_env
    resp = sign_in(client, name, company)
    assert resp.status_code == 400
    assert b"<script>" not in resp.data      # error page escapes what it echoes
    assert db["portal_sessions"].count_documents({}) == 0


def test_labels_are_normalized():
    assert clean_label("  Ada \t  Lovelace ", "Name", (2, 60)) == "Ada Lovelace"
    assert clean_label("Ｅｘａｍｐｌｅ Co.", "Company", (2, 80)) == "Example Co."   # NFKC folds full-width
    assert clean_label("José O'Neil-Smith", "Name", (2, 60)) == "José O'Neil-Smith"
    with pytest.raises(ValidationError):
        clean_label(None, "Name", (2, 60))


def test_rate_limit_per_ip(portal_env):
    _, client, _, _ = portal_env
    codes = [sign_in(client, f"Visitor {i}", "Example Co").status_code for i in range(6)]
    assert codes[:5] == [302] * 5 and codes[5] == 429


def test_rate_limiter_window_slides():
    t = [0.0]
    rl = RateLimiter(max_events=2, window_seconds=10, clock=lambda: t[0])
    assert rl.allow("ip") and rl.allow("ip") and not rl.allow("ip")
    assert rl.allow("other-ip")
    t[0] = 11
    assert rl.allow("ip")


# -- analytics ---------------------------------------------------------------------------

def test_engagement_pipeline_groups_by_company(portal_env):
    portal, client, _, clock = portal_env
    sign_in(client, "Ada Lovelace", "Example Co")
    client.get("/")
    clock.now += timedelta(minutes=5)
    sign_in(client, "Grace Hopper", "Example Co")
    sign_in(client, "Alan Turing", "Other Org")
    report = {r["company"]: r for r in portal.engagement()}
    assert report["Example Co"]["visitors"] == 2
    assert report["Example Co"]["events"] == 3 and report["Example Co"]["page_views"] == 1
    assert report["Example Co"]["last_seen"] > report["Example Co"]["first_seen"]
    assert report["Other Org"]["visitors"] == 1
    assert [v["name"] for v in portal.company_history("Example Co")][0] == "Grace Hopper"


def test_security_headers_present(portal_env):
    _, client, _, _ = portal_env
    resp = client.get("/portal/signin")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
