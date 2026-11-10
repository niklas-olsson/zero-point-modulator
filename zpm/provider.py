"""The OpenJules lifecycle wire adapter; tokens are resolved only for requests."""

from __future__ import annotations

import json
import os
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

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


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request: Request, file: Any, code: int, message: str, headers: Any, new_url: str) -> None:
        return None


class OpenJulesProvider:
    def __init__(self, config: ProviderConfig):
        self.config = config
        self._opener = build_opener(_NoRedirects())

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None, query: dict[str, Any] | None = None) -> Any:
        token = os.environ.get(self.config.token_ref[len("env:"):])
        if not token:
            raise ProviderError("provider credential reference is unresolved")
        url = self.config.endpoint + "/api/workers" + path
        if query:
            url += "?" + urlencode(query)
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, method=method, headers={"Authorization": "Bearer " + token, "Accept": "application/json", "Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ProviderError("provider response exceeds the supported size")
            return json.loads(raw)
        except HTTPError as exc:
            # Provider exception bodies can contain credentials or subprocess output.
            # Status is sufficient here; do not persist or print that untrusted body.
            status = exc.code
            exc.close()
            raise ProviderError(f"OpenJules returned HTTP {status}") from None
        except (URLError, TimeoutError, OSError):
            raise ProviderError("OpenJules transport unavailable; operation outcome may be unknown") from None
        except (ValueError, UnicodeError):
            raise ProviderError("OpenJules returned an invalid JSON response") from None

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
