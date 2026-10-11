"""Bounded helpers; callers authorize paths and external effects before invocation."""

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

import httpx

_PRIVATE_REQUEST = ContextVar('jarvis_extension_private_request', default=False)


class _PrivateRequestFilter(logging.Filter):
    def filter(self, record):
        # Telegram credentials are part of official request URLs. Suppress transport
        # logs only inside extension requests; other application's logs are unaffected.
        return not _PRIVATE_REQUEST.get()


for _logger in ('httpx', 'httpcore.connection', 'httpcore.http11', 'httpcore.http2',
                'httpcore.proxy', 'httpcore.socks'):
    logging.getLogger(_logger).addFilter(_PrivateRequestFilter())


class ExtensionError(ValueError):
    """A safe public error with no service payload, credential or URL."""


class PolicyDenied(ExtensionError):
    """A policy/idempotency rejection must not trigger an alternate side effect."""


def availability(*names):
    missing = [name for name in names if not os.environ.get(name)]
    return {'available': not missing, 'missing_environment': missing, 'live_verified': False}


def credential(name):
    value = os.environ.get(name, '')
    if not value or len(value) > 8192 or any(c in value for c in '\r\n'):
        raise ExtensionError(f'Configure {name} before using this adapter')
    return value


def bounded_text(value, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        raise ExtensionError('Text is empty, invalid, or exceeds the configured limit')
    return value


def post(url, *, transport=None, headers=None, json_body=None, data=None, auth=None):
    """Only called with fixed official URLs. Redirects and response bodies are bounded."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError):
        raise ExtensionError('External service URL is invalid') from None
    if (parsed.scheme != 'https' or parsed.host not in
            {'api.telegram.org', 'graph.facebook.com', 'api.twilio.com', 'bsky.social'}
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ExtensionError('Only configured official HTTPS service endpoints are allowed')
    private = _PRIVATE_REQUEST.set(True)
    deadline = time.monotonic() + 20
    try:
        with (httpx.Client(transport=transport, timeout=15, follow_redirects=False, trust_env=False) as client,
              client.stream('POST', url, headers=headers, json=json_body, data=data, auth=auth) as response):
            if not 200 <= response.status_code < 300:
                raise ExtensionError('External service rejected the request; inspect account configuration')
            chunks = bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic() > deadline:
                    raise ExtensionError('External service exceeded the total request time limit')
                chunks.extend(chunk)
                if len(chunks) > 131072:
                    raise ExtensionError('External service response exceeds the configured limit')
            result = json.loads(chunks)
            if not isinstance(result, dict):
                raise ExtensionError('External service returned an invalid receipt')
            return result
    except (httpx.HTTPError, json.JSONDecodeError, UnicodeError, TypeError):
        raise ExtensionError('External request failed; delivery may be uncertain') from None
    finally:
        _PRIVATE_REQUEST.reset(private)


def regular_file(path, max_bytes):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ExtensionError('Input must be an authorized regular file within the size limit')
    return path


def output_file(path):
    path = Path(path)
    if path.is_symlink() or not path.parent.is_dir():
        raise ExtensionError('Output must use an authorized existing directory')
    return path


class Journal:
    """Reserve before an external effect; unknown delivery is never blindly repeated."""

    def __init__(self, path):
        path = output_file(path)
        self.path = str(path)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS effects (scope TEXT, key TEXT, digest TEXT, day TEXT, '
                       'units INTEGER, state TEXT, receipt TEXT, PRIMARY KEY(scope,key))')
            db.execute('CREATE TABLE IF NOT EXISTS policies (scope TEXT PRIMARY KEY, paused INTEGER)')
        os.chmod(self.path, 0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def paused(self, scope, value):
        with self.connect() as db:
            db.execute('INSERT INTO policies VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET paused=?',
                       (scope, int(value), int(value)))

    def reserve(self, scope, key, payload, cap, units=1, unit_cap=None):
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', key):
            raise PolicyDenied('A bounded idempotency key is required')
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        day = datetime.now(UTC).date().isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT paused FROM policies WHERE scope=?', (scope,)).fetchone() == (1,):
                raise PolicyDenied('This workflow is paused')
            old = db.execute('SELECT digest,state,receipt FROM effects WHERE scope=? AND key=?',
                             (scope, key)).fetchone()
            if old:
                if old[0] != digest:
                    raise PolicyDenied('Idempotency key was already used with different content')
                if old[1] != 'sent':
                    raise ExtensionError('Previous delivery is uncertain; reconcile before trying again')
                return json.loads(old[2])
            count, total = db.execute('SELECT COUNT(*),COALESCE(SUM(units),0) FROM effects '
                                     'WHERE scope=? AND day=?', (scope, day)).fetchone()
            if count >= cap or (unit_cap is not None and total + units > unit_cap):
                raise ExtensionError('Daily outbound budget exhausted')
            db.execute('INSERT INTO effects VALUES (?,?,?,?,?,?,?)',
                       (scope, key, digest, day, units, 'pending', None))
        return None

    def complete(self, scope, key, receipt):
        with self.connect() as db:
            db.execute('UPDATE effects SET state=?,receipt=? WHERE scope=? AND key=?',
                       ('sent', json.dumps(receipt), scope, key))
        return receipt
