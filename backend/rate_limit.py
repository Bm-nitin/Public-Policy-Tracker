"""
Phase 18 - database-backed rate limiting.

WHY THE DATABASE: the backend runs under gunicorn on Render - potentially
several workers and instances, restarted on every deploy. A process-local
counter (a dict, or Flask-Limiter's default memory storage) would give each
worker its own count and forget everything on restart, i.e. be close to
useless. PostgreSQL is already shared by every worker and survives
restarts, so the counters live there (table rate_limit_events, migration
d4f1b7a92c6e). No new dependency, no Redis. What this is NOT: a CDN/WAF -
it protects the application's endpoints, it does not absorb a volumetric
flood.

MODEL: a rule is "at most `limit` events per `window_seconds` for one key".
Events are rows; the count is the rows younger than the window (a true
sliding window). Rows are stored under SHA-256(rule|key) so no raw email or
IP address sits in the table.

DESIGN CHOICES THAT MATTER:
  * Blocked requests are NOT recorded, so hammering an endpoint while
    blocked cannot extend the block - it ends exactly `window` after the
    oldest counted event. There is no permanent lock anywhere.
  * Login limits are checked BEFORE the password is verified, so a blocked
    client gets no password-guessing oracle and costs no Argon2 CPU.
  * The limit applies to an email whether or not the account exists, so
    429s reveal nothing about which accounts exist.
  * Per-account limits are sized above what ONE address can ever reach
    (the per-address cap is lower), so a single attacker cannot lock a
    victim out - it takes many addresses, i.e. the distributed attack the
    account limit exists to slow down. Residual risk (documented in the
    README): such an attacker can keep that account temporarily throttled.
  * IPv6 clients are bucketed by /64 (an attacker typically controls a
    whole /64, so per-address limits would be trivially rotated).
  * FAIL OPEN on a database error: an outage or an un-applied migration
    must not take login down. The failure is logged (exception class only)
    so it is visible; protection is simply absent until it is fixed.
  * Disabled (no-op) when RATE_LIMIT_ENABLED is false or no DATABASE_URL.

CLIENT IP: request.remote_addr. app.py wraps the WSGI app in werkzeug's
ProxyFix ONLY when TRUSTED_PROXY_COUNT > 0 (see security_http.py), which
then takes the Nth X-Forwarded-For entry from the right - the one our own
proxy appended. With 0, X-Forwarded-For is ignored completely, so a client
cannot spoof an address by sending the header.
"""

import hashlib
import ipaddress
import logging
import math
from collections import namedtuple
from datetime import datetime, timedelta, timezone

from flask import jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from config import Config
from database import db
from models import RateLimitEvent
from verification_tokens import ensure_aware_utc

logger = logging.getLogger(__name__)

Rule = namedtuple("Rule", ["limit", "window_seconds"])

# Deliberate limits. "failures" rules count only failed attempts; the rest
# count every attempt.
RULES = {
    # login - failures only
    "login:pair": Rule(5, 15 * 60),       # one address guessing one account
    "login:ip": Rule(20, 15 * 60),        # one address spraying many accounts
    "login:account": Rule(30, 60 * 60),   # many addresses vs one account (> pair cap x4 windows)
    # account creation / email-sending endpoints - every attempt
    "register:ip": Rule(10, 60 * 60),
    "resend:ip": Rule(10, 60 * 60),
    "resend:email": Rule(3, 60 * 60),
    "forgot:ip": Rule(10, 60 * 60),
    "forgot:email": Rule(3, 60 * 60),
    "reset:ip": Rule(20, 60 * 60),
    "verify:ip": Rule(60, 15 * 60),
    # authenticated password change - failures only
    "chpw:user": Rule(5, 15 * 60),
    # public Gemini-backed chat endpoint - every request (cost control)
    "chat:ip:min": Rule(20, 60),
    "chat:ip:hour": Rule(200, 60 * 60),
}

_PURGE_INTERVAL_SECONDS = 300
_last_purge = [None]
_last_error_log = [None]


def _now():
    """Single clock for the whole module (tests replace this)."""
    return datetime.now(timezone.utc)


def enabled():
    return bool(Config.RATE_LIMIT_ENABLED and Config.DATABASE_URL)


def client_ip():
    """The caller's address as seen by the app (see module docstring for
    the proxy trust model). IPv6 collapses to its /64."""
    raw = request.remote_addr or "unknown"
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return raw
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{raw}/64", strict=False))
    return str(addr)


def normalize_email_key(email):
    return email.strip().lower()[:254]


def _key_hash(rule_name, key):
    return hashlib.sha256(f"{rule_name}|{key}".encode("utf-8")).hexdigest()


def _log_store_failure(exc):
    now = _now()
    last = _last_error_log[0]
    if last is None or (now - last).total_seconds() > 60:
        _last_error_log[0] = now
        logger.error("rate limit store unavailable (%s) - failing open; "
                     "is migration d4f1b7a92c6e applied?", type(exc).__name__)


def _longest_window():
    return max(rule.window_seconds for rule in RULES.values())


def _maybe_purge(now):
    last = _last_purge[0]
    if last is not None and (now - last).total_seconds() < _PURGE_INTERVAL_SECONDS:
        return
    _last_purge[0] = now
    cutoff = now - timedelta(seconds=_longest_window())
    RateLimitEvent.query.filter(RateLimitEvent.created_at < cutoff).delete()
    db.session.commit()


def _fresh_times(rule_name, key, now):
    window = timedelta(seconds=RULES[rule_name].window_seconds)
    rows = RateLimitEvent.query.filter_by(bucket=rule_name, key_hash=_key_hash(rule_name, key)).all()
    fresh = []
    for row in rows:
        created = ensure_aware_utc(row.created_at)
        if created > now - window:
            fresh.append(created)
        else:
            db.session.delete(row)
    return fresh


def _retry_after(rule_name, fresh, now):
    window = timedelta(seconds=RULES[rule_name].window_seconds)
    return max(1, math.ceil((min(fresh) + window - now).total_seconds()))


def check(rule_name, key):
    """Seconds until `key` may try again if it is over the rule's limit
    (truthy), else 0. Records nothing."""
    if not enabled():
        return 0
    try:
        now = _now()
        _maybe_purge(now)
        fresh = _fresh_times(rule_name, key, now)
        if len(fresh) >= RULES[rule_name].limit:
            return _retry_after(rule_name, fresh, now)
        return 0
    except SQLAlchemyError as exc:
        db.session.rollback()
        _log_store_failure(exc)
        return 0


def record(rule_name, key):
    """Counts one event against the rule (used for 'failures only' rules)."""
    if not enabled():
        return
    try:
        db.session.add(RateLimitEvent(bucket=rule_name, key_hash=_key_hash(rule_name, key), created_at=_now()))
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _log_store_failure(exc)


def hit(rule_name, key):
    """check() then, if allowed, record() - for 'every attempt' rules.
    Returns the retry-after seconds if blocked, else 0."""
    blocked = check(rule_name, key)
    if blocked:
        return blocked
    record(rule_name, key)
    return 0


def check_many(pairs):
    """Largest retry-after across [(rule, key), ...] (0 = all clear)."""
    return max([check(rule, key) for rule, key in pairs] + [0])


def record_many(pairs):
    for rule, key in pairs:
        record(rule, key)


def too_many_requests(retry_after, extra=None):
    """The one 429 shape used everywhere."""
    body = {"error": "Too many requests. Please try again later.", "retry_after": int(retry_after)}
    if extra:
        body.update(extra)
    response = jsonify(body)
    response.status_code = 429
    response.headers["Retry-After"] = str(int(retry_after))
    return response
