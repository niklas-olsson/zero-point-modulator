"""The OpenJules lifecycle wire adapter; tokens are resolved only for requests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Protocol
from urllib.parse import quote, urlencode

from .models import ConfigurationError, ProviderConfig


class ProviderError(RuntimeError):
    pass


class WorkerProvider(Protocol):
    def capabilities(self, deployment_id: str, repository: str, profile: str) -> dict[str, Any]: ...
    def ensure(self, spec: dict[str, Any]) -> dict[str, Any]: ...
    def discover(self, deployment_id: str, repository: str, launch_key: str | None = None) -> list[dict[str, Any]]: ...
    def observe(self, deployment_id: str, repository: str, worker_id: str) -> dict[str, Any]: ...
    def drain(self, deployment_id: str, repository: str, worker_id: str, request_key: str) -> dict[str, Any]: ...
    def stop(self, deployment_id: str, repository: str, worker_id: str, request_key: str) -> dict[str, Any]: ...


def _transport_environment() -> dict[str, str]:
    # urllib uses these proxy settings and OpenSSL uses the certificate paths.
    # Do not pass model, authority or other unrelated process credentials.
    return {name: value for name, value in os.environ.items()
            if name.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "request_method"}
            or name in {"SSL_CERT_FILE", "SSL_CERT_DIR"}}


class OpenJulesProvider:
    def __init__(self, config: ProviderConfig):
        # Direct dataclass construction must not bypass the endpoint boundary.
        self.config = ProviderConfig.parse(config.__dict__)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None, query: dict[str, Any] | None = None) -> Any:
        token = os.environ.get(self.config.token_ref[len("env:"):])
        if not token:
            raise ProviderError("provider credential reference is unresolved")
        deadline = time.monotonic() + self.config.timeout_seconds
        url = self.config.endpoint + "/api/workers" + path
        if query:
            url += "?" + urlencode(query)
        request = json.dumps({"method": method, "url": url, "payload": payload,
                              "token": token, "timeout": self.config.timeout_seconds}).encode("utf-8")
        process = None
        try:
            # A socket timeout is reset by each received byte and cannot bound
            # DNS or trickled headers. Isolate the blocking stdlib transport so
            # expiry stops its I/O, rather than abandoning a live worker thread.
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", str(Path(__file__).with_name("_http_transport.py"))],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=_transport_environment(), close_fds=True,
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, self.config.timeout_seconds)
            raw, _ = process.communicate(request, timeout=remaining)
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, self.config.timeout_seconds)
            if process.returncode == 3 and len(raw) == 3 and raw.isdigit():
                raise ProviderError(f"OpenJules returned HTTP {int(raw)}")
            if process.returncode == 4:
                raise ProviderError("provider response exceeds the supported size")
            if process.returncode != 0 or len(raw) > 1_000_000:
                raise ProviderError("OpenJules transport unavailable; operation outcome may be unknown")
            return json.loads(raw)
        except subprocess.TimeoutExpired:
            raise ProviderError("OpenJules total request deadline exceeded; operation outcome may be unknown") from None
        except (TimeoutError, OSError):
            raise ProviderError("OpenJules transport unavailable; operation outcome may be unknown") from None
        except (ValueError, UnicodeError):
            raise ProviderError("OpenJules returned an invalid JSON response") from None
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                # Reap on every path, including interruption. No helper may
                # issue a late request after this call releases controller use.
                process.communicate()

    def capabilities(self, deployment_id: str, repository: str, profile: str) -> dict[str, Any]:
        return self._request("GET", "/capabilities", query={"deployment_id": deployment_id, "repository": repository, "profile": profile})

    def ensure(self, spec: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/ensure", payload=spec)

    def discover(self, deployment_id: str, repository: str, launch_key: str | None = None) -> list[dict[str, Any]]:
        query = {"deployment_id": deployment_id, "repository": repository}
        if launch_key is not None:
            query["launch_key"] = launch_key
        response = self._request("GET", "", query=query)
        if not isinstance(response, dict) or type(response.get("schema_version")) is not int or response["schema_version"] != 1 or not isinstance(response.get("workers"), list):
            raise ConfigurationError("unsupported provider discovery response")
        return response["workers"]

    def observe(self, deployment_id: str, repository: str, worker_id: str) -> dict[str, Any]:
        return self._request("GET", "/" + quote(worker_id, safe=""), query={"deployment_id": deployment_id, "repository": repository})

    def _mutate(self, operation: str, deployment_id: str, repository: str, worker_id: str, request_key: str) -> dict[str, Any]:
        return self._request("POST", "/" + quote(worker_id, safe="") + "/" + operation, payload={"schema_version": 1, "deployment_id": deployment_id, "repository": repository, "request_key": request_key})

    def drain(self, deployment_id: str, repository: str, worker_id: str, request_key: str) -> dict[str, Any]:
        return self._mutate("drain", deployment_id, repository, worker_id, request_key)

    def stop(self, deployment_id: str, repository: str, worker_id: str, request_key: str) -> dict[str, Any]:
        return self._mutate("stop", deployment_id, repository, worker_id, request_key)
