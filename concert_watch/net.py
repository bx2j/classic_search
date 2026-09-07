"""HTTP 계층.

이 PC는 사내 TLS 검사 프록시가 있어서 certifi 번들만 쓰는 requests가
'self-signed certificate in certificate chain'으로 실패한다.
truststore를 주입하면 OS(윈도우) 인증서 저장소를 쓰게 되어 해결된다.
"""
from __future__ import annotations  # py3.9에서도 3.10 문법 어노테이션 허용

import time
import logging

log = logging.getLogger(__name__)

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # 없어도 동작은 하되 사내망에서 실패할 수 있음
    log.debug("truststore 미설치 - 사내 프록시 환경이면 pip install truststore")

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) concert-watch/0.1"

_session = requests.Session()
_session.headers.update({"User-Agent": UA})

_last_hit: dict[str, float] = {}
MIN_INTERVAL = 1.0  # 같은 호스트에 대한 최소 요청 간격(초)


def _polite(url: str) -> None:
    host = url.split("/")[2] if "://" in url else url
    prev = _last_hit.get(host, 0.0)
    wait = MIN_INTERVAL - (time.monotonic() - prev)
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.monotonic()


def get(url: str, *, params: dict | None = None, timeout: int = 30,
        retries: int = 3, **kw) -> requests.Response:
    """예의 있는 GET. 호스트별 rate limit + 지수 백오프 재시도."""
    last: Exception | None = None
    for attempt in range(retries):
        _polite(url)
        try:
            r = _session.get(url, params=params, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"GET 실패: {url}") from last


def get_text(url: str, **kw) -> str:
    r = get(url, **kw)
    r.encoding = r.encoding or "utf-8"
    return r.text
