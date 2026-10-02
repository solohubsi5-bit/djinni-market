from __future__ import annotations

import threading
import time
from urllib.parse import urlencode

import requests

from . import config

_local = threading.local()
_lock = threading.Lock()
_blocked_streak = 0
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/128.0 Safari/537.36',
    'Accept-Language': 'uk-UA,uk;q=0.9',
}


class Blocked(RuntimeError):
    pass


def _session() -> requests.Session:
    if not hasattr(_local, 's'):
        _local.s = requests.Session()
        _local.s.headers.update(HEADERS)
    return _local.s


def _note(blocked: bool):
    global _blocked_streak
    with _lock:
        _blocked_streak = _blocked_streak + 1 if blocked else 0


def breaker_open() -> bool:
    return _blocked_streak >= config.BREAKER_CONSECUTIVE


def url_for(params: dict) -> str:
    q = {k: v for k, v in params.items() if v}
    return config.BASE_URL + ('?' + urlencode(q) if q else '')


def get(params: dict) -> str:
    url = url_for(params)
    for attempt in range(config.MAX_RETRIES + 1):
        if breaker_open():
            raise Blocked(f'breaker open, skipped {url}')
        try:
            r = _session().get(url, timeout=config.TIMEOUT_S)
            if r.status_code == 200:
                _note(False)
                time.sleep(config.DELAY_S)
                return r.text
            if r.status_code in (403, 429):
                _note(True)
            elif r.status_code not in (500, 502, 503, 504):
                raise RuntimeError(f'HTTP {r.status_code} for {url}')
            wait = int(r.headers.get('Retry-After', 0) or 0) or 5 * 2 ** attempt
        except requests.RequestException:
            wait = 5 * 2 ** attempt
        if attempt == config.MAX_RETRIES:
            break
        time.sleep(wait)
    raise RuntimeError(f'giving up on {url}')
