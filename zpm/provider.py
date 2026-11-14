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
        for value in (payload, query):
            if value is not None and (
                ("schema_version" in value and (type(value["schema_version"]) is not int or value["schema_version"] != 1))
                or set(value) & {"destination_binding", "destination_id", "journal_incarnation",
                                 "policy_binding_id", "policy_binding_version", "protocol_version"}
            ):
                raise ConfigurationError("legacy provider cannot send destination-bound requests")
        return self._send(method, path, payload, query)

    def _send(self, method, path, payload=None, query=None, *, base_path="/api/workers", strict=False):
        """The shared bounded transport, after each version's request validation."""
        token = os.environ.get(self.config.token_ref[len("env:"):])
        if not token:
            raise ProviderError("provider credential reference is unresolved")
        deadline = time.monotonic() + self.config.timeout_seconds
        url = self.config.endpoint + base_path + path
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
            if strict:
                from .destination_contract import decode_response
                return decode_response(raw)
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
        if not isinstance(spec, dict) or type(spec.get("schema_version")) is not int or spec["schema_version"] != 1:
            raise ConfigurationError("legacy provider ensure requires schema_version 1")
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


class DestinationProvider(OpenJulesProvider):
    """Explicit full v2 client; the legacy provider never gains bound dispatch."""

    def _request(self, method, path, payload=None, query=None):
        return self._send(method, path, payload, query, base_path="/api/v2/workers", strict=True)

    @staticmethod
    def _query(deployment_id, repository, binding, profile=None):
        from .registry import destination_binding

        result = {"schema_version": 2, "deployment_id": deployment_id,
                  "repository": repository, **destination_binding(binding)}
        if profile is not None:
            result["profile"] = profile
        return result

    def capabilities(self, deployment_id, repository, profile, binding):
        return self._request("GET", "/capabilities", query=self._query(deployment_id, repository, binding, profile))

    def execution_contract(self, deployment_id, repository, profile, binding):
        return self._request("GET", "/execution-contract", query={
            **self._query(deployment_id, repository, binding, profile), "contract_version": 1,
        })

    def ensure(self, spec):
        from .destination_contract import QUALIFICATION, exact_object, integer, qualification
        from .registry import destination_binding

        exact_object(spec, {"schema_version", "deployment_id", "repository", "slot_id", "generation",
                            "launch_key", "profile", "destination_binding"} | QUALIFICATION)
        integer(spec["schema_version"], 2, 2)
        destination_binding(spec["destination_binding"])
        qualification({key: spec[key] for key in QUALIFICATION})
        return self._request("POST", "/ensure", payload=spec)

    def discover(self, deployment_id, repository, profile, binding, *, launch_key=None, original=None):
        from .destination_contract import discovery, qualification

        query = {**self._query(deployment_id, repository, binding, profile), "contract_version": 1}
        if launch_key is not None:
            query.update(qualification(original), launch_key=launch_key)
        elif original is not None:
            raise ConfigurationError("full keyed discovery requires the original launch key")
        expected = {"deployment_id": deployment_id, "repository": repository, "profile": profile}
        if launch_key is not None:
            expected["launch_key"] = launch_key
        return discovery(self._request("GET", "", query=query), binding=binding,
                         original=original, expected=expected)

    def observe(self, deployment_id, repository, worker_id, binding):
        return self._request("GET", "/" + quote(worker_id, safe=""),
                             query=self._query(deployment_id, repository, binding))

    def _bound_mutate(self, action, deployment_id, repository, worker_id, request_key, binding):
        from .registry import destination_binding

        return self._request("POST", "/" + quote(worker_id, safe="") + "/" + action, payload={
            "schema_version": 2, "deployment_id": deployment_id, "repository": repository,
            "request_key": request_key, "destination_binding": destination_binding(binding),
        })

    def drain(self, deployment_id, repository, worker_id, request_key, binding):
        return self._bound_mutate("drain", deployment_id, repository, worker_id, request_key, binding)

    def stop(self, deployment_id, repository, worker_id, request_key, binding):
        return self._bound_mutate("stop", deployment_id, repository, worker_id, request_key, binding)
