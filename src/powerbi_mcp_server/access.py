import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import certifi
from impala.dbapi import connect

log = logging.getLogger("powerbi_mcp_server.audit")

CACHE_TTL_SECONDS = 300
MAX_PROBE_WORKERS = 6
DENIAL_MARKERS = ("authorizationexception", "does not have privileges", "could not resolve table reference")

Probe = Callable[[str, str], tuple[bool, bool]]


def classify_error(message: str) -> tuple[bool, bool]:
    """
    Decide what a failed probe means.

    A missing privilege or an unresolvable table is a definite "no" and safe to cache. Any other failure
    (network, warehouse suspended, timeout) also means "no" because the checker fails closed, but it says
    nothing about the user's rights, so it must not be cached.

    :param message str: Error text raised by the Impala driver
    :return: (allowed, cacheable), allowed is always False here
    """
    lowered = message.lower()
    return False, any(marker in lowered for marker in DENIAL_MARKERS)


def impala_probe(acting_as_user: str, name: str) -> tuple[bool, bool]:
    """
    Ask Impala whether acting_as_user may query one table or view, by running a zero-row SELECT as that user.

    Uses the same delegation as the SQL server: the service user logs in with LDAP and adds doAs, so Ranger
    authorizes the statement as the acting user. The object name comes from the committed index and was
    validated as db.name when the index was built.

    :param acting_as_user str: Trusted username to delegate to
    :param name str: Object as db.name
    :return: (allowed, cacheable)
    """
    proxy_password = os.environ.get("IMPALA_PROXY_PASSWORD", "")
    connection = None
    try:
        connection = connect(
            host=os.environ["IMPALA_HOST"],
            port=int(os.environ.get("IMPALA_PORT", "443")),
            use_ssl=True,
            ca_cert=os.environ.get("IMPALA_CA_CERT") or certifi.where(),
            verify_cert=os.environ.get("IMPALA_VERIFY_TLS", "true").lower() != "false",
            use_http_transport=True,
            http_path=f"{os.environ.get('IMPALA_HTTP_PATH', 'cliservice').rstrip('/')}?doAs={quote(acting_as_user, safe='')}",
            auth_mechanism="LDAP",
            user=os.environ["IMPALA_PROXY_USER"],
            password=proxy_password,
        )
        connection.cursor().execute(f"SELECT 1 FROM {name} LIMIT 0")
        return True, True
    except Exception as exc:  # noqa: BLE001 - every driver failure must end in a deny
        message = f"{type(exc).__name__}: {exc}".replace(proxy_password, "***") if proxy_password else str(exc)
        allowed, cacheable = classify_error(message)
        log.info("probe_denied acting_as_user=%s object=%s cacheable=%s detail=%s", acting_as_user, name, cacheable, message[:300])
        return allowed, cacheable
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001 - closing must never hide the result
                pass


class AccessChecker:
    """
    Decides which of a set of tables and views a user may query, with a short per-user cache.
    """

    def __init__(self, probe: Probe = impala_probe, ttl_seconds: float = CACHE_TTL_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._probe = probe
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[tuple[str, str], tuple[bool, float]] = {}
        self._lock = threading.Lock()

    def allowed_objects(self, acting_as_user: str, names: set[str]) -> set[str]:
        """
        Return the subset of names the user may query, probing uncached ones in parallel.

        :param acting_as_user str: Trusted username the probes run as
        :param names set: Objects as db.name
        :return: The permitted subset, anything unknown or failing is left out
        """
        now = self._clock()
        permitted: set[str] = set()
        missing: list[str] = []
        with self._lock:
            for name in names:
                cached = self._cache.get((acting_as_user, name))
                if cached is not None and cached[1] > now:
                    if cached[0]:
                        permitted.add(name)
                else:
                    missing.append(name)
        if missing:
            with ThreadPoolExecutor(max_workers=MAX_PROBE_WORKERS) as pool:
                results = list(pool.map(lambda n: self._probe(acting_as_user, n), missing))
            expires = self._clock() + self._ttl
            with self._lock:
                for name, (allowed, cacheable) in zip(missing, results):
                    if cacheable:
                        self._cache[(acting_as_user, name)] = (allowed, expires)
                    if allowed:
                        permitted.add(name)
        return permitted
