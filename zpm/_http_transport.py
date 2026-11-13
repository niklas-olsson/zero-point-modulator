"""Private, one-request stdlib transport killed by the parent's total deadline.

The request and bearer credential arrive only on stdin. Stdout contains either
the bounded response body or an HTTP status; diagnostics and error bodies are
never returned. This module also runs as an isolated script from an installed
wheel, without importing the caller's checkout or site configuration.
"""

from __future__ import annotations

from http.client import HTTPException
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def main() -> int:
    try:
        value = json.loads(sys.stdin.buffer.read())
        data = None if value["payload"] is None else json.dumps(value["payload"]).encode("utf-8")
        request = Request(value["url"], data=data, method=value["method"], headers={
            "Authorization": "Bearer " + value["token"], "Accept": "application/json", "Content-Type": "application/json",
        })
        handlers = [_NoRedirects()]
        if urlsplit(value["url"]).scheme == "http":
            # Configuration permits plaintext only to literal loopback. Ambient
            # proxies must not move that bearer-bearing request off this host.
            handlers.append(ProxyHandler({}))
        with build_opener(*handlers).open(request, timeout=value["timeout"]) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            return 4
        sys.stdout.buffer.write(raw)
        return 0
    except HTTPError as exc:
        # Never forward untrusted error bodies, URLs or exception text.
        status = exc.code
        exc.close()
        sys.stdout.buffer.write(str(status).encode("ascii"))
        return 3
    except (HTTPException, URLError, TimeoutError, OSError, ValueError, UnicodeError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
