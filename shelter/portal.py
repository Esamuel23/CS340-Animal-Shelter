# =============================================================================
# File:     portal.py
# Author:   Elijah Samuel
# Course:   CS 499 Computer Science Capstone, SNHU
# Date:     2026-10-11
# Purpose:  Employer portal (stretch strand E). A sign-in gate in front of the
#           whole Dash app with server-side sessions in MongoDB, a per-IP rate
#           limit, and a visits log for engagement reporting. Visitors type a
#           name and company; that is ATTRIBUTION, not authentication. See
#           docs/THREAT_MODEL.md.
# =============================================================================
"""Employer portal: Flask before_request gate + server-side sessions."""

from __future__ import annotations

import hashlib
import html as html_lib
import os
import re
import secrets
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from flask import Flask, Response, g, has_request_context, redirect, request

from .errors import ConfigurationError, ShelterError, ValidationError

COOKIE_NAME = "gs_portal"
SESSION_HOURS = 2
SESSIONS = "portal_sessions"
VISITS = "visits"

# Paths reachable without a session: the sign-in page itself and static
# styling/logo files (no data lives in assets/).
PUBLIC_PREFIXES = ("/portal/signin", "/assets/", "/favicon.ico")

NAME_LIMITS = (2, 60)
COMPANY_LIMITS = (2, 80)
# Letters (any language), digits, spaces, and . , ' & - ( )
_ALLOWED = re.compile(r"^[\w .,'&()\-]+$", re.UNICODE)


def utcnow() -> datetime:
    """Naive UTC, which is what pymongo stores and returns by default."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def clean_label(value: Any, field: str, limits: Tuple[int, int]) -> str:
    """Normalize and validate a visitor-typed name or company.

    NFKC folds look-alike characters, whitespace runs collapse to one space,
    and anything outside a small character set is refused (not silently
    dropped), so what is stored is exactly what the visitor saw accepted.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} is required.")
    text = " ".join(unicodedata.normalize("NFKC", value).split())
    low, high = limits
    if not low <= len(text) <= high:
        raise ValidationError(f"{field} must be {low}-{high} characters.")
    if not _ALLOWED.match(text) or "_" in text:
        raise ValidationError(f"{field} may use letters, numbers, spaces, and . , ' & - ( ) only.")
    return text


def hash_token(token: str) -> str:
    """Sessions are stored by SHA-256 of the cookie value, never the value itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RateLimiter:
    """Sliding-window limit per key (client IP). In-memory, one process."""

    def __init__(self, max_events: int = 5, window_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.max_events = max_events
        self.window = window_seconds
        self.clock = clock
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._events.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.max_events:
                return False
            q.append(now)
            if len(self._events) > 10_000:  # keep memory bounded
                for k in [k for k, v in self._events.items() if not v]:
                    del self._events[k]
            return True


class EmployerPortal:
    """Gate the whole Flask server behind a sign-in form.

    db is a pymongo (or mongomock) Database holding the portal_sessions and
    visits collections.
    """

    def __init__(
        self,
        db: Any,
        secure_cookie: bool = True,
        session_hours: float = SESSION_HOURS,
        rate_limiter: Optional[RateLimiter] = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.sessions = db[SESSIONS]
        self.visits = db[VISITS]
        self.secure_cookie = secure_cookie
        self.lifetime = timedelta(hours=session_hours)
        self.rate_limiter = rate_limiter or RateLimiter()
        self.clock = clock

    @classmethod
    def from_env(cls, shelter) -> "EmployerPortal":
        secure = os.environ.get("PORTAL_SECURE_COOKIE", "1")
        if secure not in ("0", "1"):
            raise ConfigurationError("PORTAL_SECURE_COOKIE must be 0 or 1.")
        db = shelter.client[shelter.settings.db_name]
        return cls(db, secure_cookie=secure == "1")

    # -- session store ---------------------------------------------------------

    def start_session(self, name: str, company: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        self.sessions.insert_one({
            "_id": hash_token(token),
            "name": name,
            "company": company,
            "created_at": now,
            "expires_at": now + self.lifetime,  # TTL index removes it after this
        })
        self.record_visit(name, company, "signin")
        return token

    def lookup(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        if not token or len(token) > 200:
            return None
        doc = self.sessions.find_one({"_id": hash_token(token)})
        if not doc or doc["expires_at"] <= self.clock():
            # TTL cleanup runs about once a minute, so expiry is also checked here.
            return None
        return doc

    def end_session(self, token: Optional[str]) -> None:
        if token and len(token) <= 200:
            self.sessions.delete_one({"_id": hash_token(token)})

    def record_visit(self, name: str, company: str, event: str) -> None:
        self.visits.insert_one({"company": company, "name": name, "event": event, "timestamp": self.clock()})

    # -- reporting -------------------------------------------------------------

    @staticmethod
    def engagement_pipeline() -> List[Dict[str, Any]]:
        """Views and visitors per company with first/last seen. Counting in MongoDB."""
        return [
            {"$group": {
                "_id": "$company",
                "events": {"$sum": 1},
                "page_views": {"$sum": {"$cond": [{"$eq": ["$event", "view"]}, 1, 0]}},
                "visitors": {"$addToSet": "$name"},
                "first_seen": {"$min": "$timestamp"},
                "last_seen": {"$max": "$timestamp"},
            }},
            {"$project": {
                "_id": 0, "company": "$_id", "events": 1, "page_views": 1,
                "visitors": {"$size": "$visitors"}, "first_seen": 1, "last_seen": 1,
            }},
            {"$sort": {"last_seen": -1, "company": 1}},
        ]

    def engagement(self) -> List[Dict[str, Any]]:
        return list(self.visits.aggregate(self.engagement_pipeline()))

    def company_history(self, company: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Recent visits for one company; served by the {company: 1, timestamp: -1} index."""
        company = clean_label(company, "Company", COMPANY_LIMITS)
        return list(self.visits.find({"company": company}, {"_id": 0}).sort("timestamp", -1).limit(limit))

    # -- Flask wiring -----------------------------------------------------------

    def current_user(self) -> Optional[Dict[str, Any]]:
        return getattr(g, "portal_user", None) if has_request_context() else None

    def install(self, app) -> None:
        """Attach to a Dash app (or a bare Flask app) before any request is served."""
        server: Flask = getattr(app, "server", app)

        @server.before_request
        def _gate():
            path = request.path
            if any(path == p or path.startswith(p) for p in PUBLIC_PREFIXES):
                return None
            try:
                user = self.lookup(request.cookies.get(COOKIE_NAME))
            except ShelterError:
                return Response("Service unavailable.", status=503, mimetype="text/plain")
            except Exception:  # database errors: fail closed, no details
                return Response("Service unavailable.", status=503, mimetype="text/plain")
            if user is None:
                if request.method == "GET" and path == "/":
                    return redirect("/portal/signin", code=302)
                # Dash's JSON endpoints and everything else: no data, no hints.
                return Response("Sign in required.", status=401, mimetype="text/plain")
            g.portal_user = {"name": user["name"], "company": user["company"]}
            if request.method == "GET" and path == "/":
                try:
                    self.record_visit(user["name"], user["company"], "view")
                except Exception:
                    pass  # analytics must never block the page
            return None

        @server.after_request
        def _headers(resp):
            resp.headers.setdefault("X-Content-Type-Options", "nosniff")
            resp.headers.setdefault("Referrer-Policy", "same-origin")
            resp.headers.setdefault("X-Frame-Options", "DENY")
            if request.path.startswith("/portal/"):
                resp.headers["Cache-Control"] = "no-store"
            return resp

        @server.route("/portal/signin", methods=["GET", "POST"])
        def portal_signin():
            if request.method == "GET":
                return self._signin_page()
            ip = request.remote_addr or "unknown"
            if not self.rate_limiter.allow(ip):
                return self._signin_page("Too many sign-in attempts. Wait a minute and try again.", 429)
            try:
                name = clean_label(request.form.get("name"), "Name", NAME_LIMITS)
                company = clean_label(request.form.get("company"), "Company", COMPANY_LIMITS)
            except ValidationError as exc:
                return self._signin_page(str(exc), 400)
            try:
                token = self.start_session(name, company)
            except Exception:
                return self._signin_page("Sign-in is unavailable right now.", 503)
            resp = redirect("/", code=302)
            resp.set_cookie(
                COOKIE_NAME,
                token,
                max_age=int(self.lifetime.total_seconds()),
                httponly=True,
                secure=self.secure_cookie,
                samesite="Lax",
                path="/",
            )
            return resp

        @server.route("/portal/signout", methods=["POST"])
        def portal_signout():
            try:
                self.end_session(request.cookies.get(COOKIE_NAME))
            except Exception:
                pass
            resp = redirect("/portal/signin", code=302)
            resp.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=self.secure_cookie, samesite="Lax")
            return resp

    @staticmethod
    def _signin_page(error: str = "", status: int = 200) -> Response:
        err = f'<p class="error">{html_lib.escape(error)}</p>' if error else ""
        body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · Grazioso Salvare Dashboard</title><link rel="stylesheet" href="/assets/style.css"></head>
<body><div class="portal-card">
<img src="/assets/grazioso_salvare_logo.svg" alt="Grazioso Salvare logo" style="height:64px">
<h1>Rescue dashboard preview</h1>
<p>Tell me who you are to open the dashboard. Your name and company are logged so I can see who visited.</p>
{err}
<form class="portal-form" method="post" action="/portal/signin">
<label for="name">Your name</label><input id="name" name="name" maxlength="{NAME_LIMITS[1]}" required autocomplete="name">
<label for="company">Company</label><input id="company" name="company" maxlength="{COMPANY_LIMITS[1]}" required autocomplete="organization">
<button type="submit">Open dashboard</button>
</form>
<p class="note">This is attribution, not a login. No password is asked for and nothing you type is verified.
The session lasts 2 hours.</p>
</div></body></html>"""
        return Response(body, status=status, mimetype="text/html")
