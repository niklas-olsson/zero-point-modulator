"""Prepare and operate an explicit, private, single-host installation.

Preparation is offline. It writes configuration, not a readiness receipt, and
does not install dependencies or start any service. The generated manager uses
separate interpreters and working directories for the two ``src`` packages.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit

from .models import Binding, ConfigurationError, ProviderConfig, identifier
from .store import Store


SOURCE_FILES = {
    "zpm": "pyproject.toml",
    "openjules": "src/worker/managed_service.py",
    "polyphony": "src/liminal_bridge/coordinator_service.py",
}
MANAGED_ENTRYPOINTS = {
    "zpm": ("pyproject.toml", "zpm/deployment.py"),
    "openjules": ("src/worker/managed_service.py", "src/worker/docker_runtime.py", "src/polyphony/managed_client.py"),
    "polyphony": ("src/liminal_bridge/coordinator_service.py", "src/liminal_bridge/managed_service.py"),
}
SECRET_ENV = {"zpm": "OPENJULES_WORKER_TOKEN", "openjules": "POLYPHONY_RUNTIME_TOKEN"}
MODEL_TOKEN_ENV = "ZPM_MODEL_PROVIDER_TOKEN"
MODEL_ENTRYPOINTS = ("src/worker/model_broker.py", "src/agent/managed_runner.py")
CHILD_ENTRYPOINTS = {
    "openjules": ("src/worker/child_supervisor.py", "src/worker/child_types.py",
                  "src/worker/child_broker.py", "src/agent/child_tools.py", "src/agent/managed_root_runner.py"),
    "polyphony": ("src/core/collaboration/child_grants.py", "src/liminal_bridge/child_service.py"),
}
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_ENV = re.compile(r"[A-Z_][A-Z0-9_]*")
_MODEL_ENV = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "AZURE_OPENAI_API_KEY"}


def _object(value: Any, fields: set[str], name: str, required: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or set(value) - fields or (required or fields) - set(value):
        raise ConfigurationError(f"{name} has missing or unsupported fields")
    return value


def _number(value: Any, name: str, minimum: float, maximum: float, integer: bool = False):
    kinds = (int,) if integer else (int, float)
    if type(value) not in kinds or not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} is outside its supported bounds")
    return value


def _strings(value: Any, name: str, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value) or len(value) > 100:
        raise ConfigurationError(f"{name} must be a bounded list")
    if any(not isinstance(item, str) or not item or len(item.encode()) > 512 or "\x00" in item for item in value):
        raise ConfigurationError(f"{name} contains an invalid value")
    if len(set(value)) != len(value):
        raise ConfigurationError(f"{name} contains duplicates")
    return value


def _path(value: Any, name: str) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute() or "\x00" in value:
        raise ConfigurationError(f"{name} must be an absolute local path")
    return Path(value).resolve(strict=True)


def _git(checkout: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", str(checkout), *args],
            check=True, capture_output=True, text=True, timeout=15,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise ConfigurationError("cannot verify the requested local Git checkout") from None


def _checkout(value: Any, name: str) -> dict:
    data = _object(value, {"checkout", "revision"}, name)
    path = _path(data["checkout"], name + " checkout")
    revision = data["revision"]
    if not isinstance(revision, str) or not _SHA.fullmatch(revision):
        raise ConfigurationError(f"{name} requires a full immutable Git revision")
    if _git(path, "rev-parse", "--show-toplevel") != str(path) or _git(path, "rev-parse", "HEAD") != revision:
        raise ConfigurationError(f"{name} checkout does not match the declared revision")
    if _git(path, "status", "--porcelain", "--untracked-files=all"):
        raise ConfigurationError(f"{name} checkout must be clean, including untracked files")
    return {"checkout": str(path), "revision": revision}


def _model_policy(value: Any) -> dict:
    required = {"endpoint", "model", "token_ref"}
    defaults = {"max_calls": 25, "max_output_tokens": 2048, "max_total_output_tokens": 32768,
                "request_timeout_seconds": 30, "max_request_bytes": 524288,
                "max_response_bytes": 2097152, "token_parameter": "max_tokens"}
    data = defaults | _object(value, required | set(defaults), "model_policy", required)
    endpoint = data["endpoint"]
    if not isinstance(endpoint, str) or not endpoint or any(character.isspace() for character in endpoint) or "\x00" in endpoint:
        raise ConfigurationError("model endpoint must be an explicit completions URL")
    try:
        parts = urlsplit(endpoint)
        valid = (parts.scheme in {"https", "http"} and parts.hostname
                 and parts.username is None and parts.password is None and not parts.query and not parts.fragment
                 and parts.path.endswith("/chat/completions")
                 and (parts.scheme == "https" or parts.hostname in {"127.0.0.1", "::1", "localhost"}))
        parts.port
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError("model endpoint requires HTTPS or loopback HTTP without URL credentials")
    model = data["model"]
    if not isinstance(model, str) or not model or len(model) > 256 or "\x00" in model or any(character.isspace() for character in model):
        raise ConfigurationError("model_policy requires an explicit bounded model identifier")
    reference = data["token_ref"]
    if (not isinstance(reference, str) or not reference.startswith("env:") or not _ENV.fullmatch(reference[4:])
            or reference[4:] in SECRET_ENV.values()):
        raise ConfigurationError("model token_ref must name a provider environment reference, not a control credential")
    for name, minimum, maximum in (
        ("max_calls", 1, 1000), ("max_output_tokens", 1, 32768),
        ("max_total_output_tokens", 1, 10_000_000), ("max_request_bytes", 1024, 524288),
        ("max_response_bytes", 1024, 2097152),
    ):
        _number(data[name], "model " + name, minimum, maximum, integer=True)
    timeout = data["request_timeout_seconds"]
    if type(timeout) not in {int, float} or not 0 < timeout <= 120:
        raise ConfigurationError("model request_timeout_seconds must be positive and at most 120")
    if not isinstance(data["token_parameter"], str) or data["token_parameter"] not in {"max_tokens", "max_completion_tokens"}:
        raise ConfigurationError("model token_parameter is unsupported")
    return data


def _child_policy(value: Any, runtime: dict, root_seconds: float, root_capabilities: list[str]) -> dict:
    required = {"max_children", "max_child_seconds", "max_child_turns", "capabilities"}
    defaults = {"uid_start": 60000, "command": ["python3", "-I", "-S", "/opt/openjules/managed_runner.py"],
                "verify_argv": [], "max_result_bytes": 4_000_000}
    data = defaults | _object(value, required | set(defaults), "child_policy", required)
    count = _number(data["max_children"], "child max_children", 1, 64, integer=True)
    seconds = data["max_child_seconds"]
    if type(seconds) not in {int, float} or not 0 < seconds <= min(86400, root_seconds):
        raise ConfigurationError("child duration must be positive and no longer than the approved root attempt")
    _number(data["max_child_turns"], "child max_child_turns", 1, 1000, integer=True)
    capabilities = _strings(data["capabilities"], "child capabilities", False)
    if not set(capabilities) <= set(root_capabilities):
        raise ConfigurationError("child capabilities exceed the approved root bundle")
    data["capabilities"] = sorted(capabilities)
    uid = _number(data["uid_start"], "child uid_start", 1, 65535 - count + 1, integer=True)
    if any(uid <= protected < uid + count for protected in (runtime["uid"], 65534)):
        raise ConfigurationError("child UID range overlaps a protected root or watchdog identity")
    for name in ("command", "verify_argv"):
        command = data[name]
        if (not isinstance(command, list) or (name == "command" and not command) or len(command) > 128
                or any(not isinstance(item, str) or not item or "\x00" in item or len(item) > 4096 for item in command)):
            raise ConfigurationError("child " + name + " must be bounded explicit argv")
    _number(data["max_result_bytes"], "child max_result_bytes", 1024, 4_000_000, integer=True)
    return data


def parse_configuration(value: Any) -> dict:
    required = {"schema_version", "deployment_id", "binding", "max_workers", "sources", "source", "image", "command", "policy"}
    data = _object(value, required | {"ports", "runtime", "environment_refs", "model_policy", "child_policy"}, "deployment configuration", required)
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ConfigurationError("deployment schema_version must be 1")
    deployment = identifier(data["deployment_id"], "deployment_id")
    binding = Binding.parse(data["binding"])
    if len(binding.repository.encode()) > 512:
        raise ConfigurationError("managed repository identity exceeds the authority bound")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", binding.profile):
        raise ConfigurationError("managed Docker profile name contains unsupported characters")
    maximum = _number(data["max_workers"], "max_workers", 1, 2**63 - 1, True)
    sources = _object(data["sources"], set(SOURCE_FILES), "sources")
    sources = {name: _checkout(source, name) for name, source in sources.items()}
    for name, entrypoints in MANAGED_ENTRYPOINTS.items():
        if any(not (Path(sources[name]["checkout"]) / entrypoint).is_file() for entrypoint in entrypoints):
            raise ConfigurationError(f"{name} revision lacks the required managed entry point")
    source = _checkout(data["source"], "repository source")
    if len(source["revision"]) != 40:
        raise ConfigurationError("the managed Docker baseline requires a SHA-1 Git repository")
    image = data["image"]
    if not isinstance(image, str) or not re.fullmatch(r"(?:sha256:|[^\s@]+@sha256:)[0-9a-f]{64}", image):
        raise ConfigurationError("image must use a full sha256 image ID or repository digest")
    command = data["command"]
    if not isinstance(command, list) or not command or len(command) > 128 or any(
        not isinstance(item, str) or not item or "\x00" in item or len(item) > 4096 for item in command
    ) or not command[0]:
        raise ConfigurationError("command must be bounded explicit argv")
    policy = _object(data["policy"], {"checks", "reviewers", "max_attempts", "budget_seconds", "attempt_seconds", "capabilities"}, "policy")
    checks = _strings(policy["checks"], "checks")
    reviewers = _strings(policy["reviewers"], "reviewers")
    if reviewers != ["operator"]:
        raise ConfigurationError("single-host bundle supports the operator reviewer principal; provision other principals separately")
    capabilities = _strings(policy["capabilities"], "capabilities", False)
    attempts = _number(policy["max_attempts"], "max_attempts", 1, 1000, True)
    budget = _number(policy["budget_seconds"], "budget_seconds", 1, 604800)
    seconds = _number(policy["attempt_seconds"], "attempt_seconds", 1, budget)
    defaults = {"uid": 65532, "memory_bytes": 536870912, "cpu_quota": 100000, "pids_limit": 128,
                "max_seconds": 300, "max_archive_bytes": 16777216}
    runtime = data.get("runtime", {})
    if not isinstance(runtime, dict) or set(runtime) - set(defaults):
        raise ConfigurationError("runtime contains unsupported fields")
    runtime = defaults | runtime
    bounds = {"uid": (1, 65535), "memory_bytes": (16777216, 1 << 40), "cpu_quota": (1000, 1 << 31),
              "pids_limit": (8, 65536), "max_seconds": (1, 86400), "max_archive_bytes": (1024, 1 << 30)}
    for name, limits in bounds.items():
        _number(runtime[name], name, *limits, integer=True)
    if runtime["uid"] == 65534:
        raise ConfigurationError("uid 65534 is reserved for the trusted runtime watchdog")
    if seconds > runtime["max_seconds"]:
        raise ConfigurationError("attempt_seconds must not exceed the runtime deadline")
    ports = {"polyphony": 8766, "openjules": 8000}
    supplied_ports = data.get("ports", {})
    if not isinstance(supplied_ports, dict) or set(supplied_ports) - set(ports):
        raise ConfigurationError("ports contains unsupported fields")
    ports.update(supplied_ports)
    for name, port in ports.items():
        _number(port, name + " port", 1024, 65535, True)
    if len(set(ports.values())) != len(ports):
        raise ConfigurationError("services require distinct ports")
    environment = data.get("environment_refs", {})
    if not isinstance(environment, dict) or any(
        not isinstance(key, str) or key not in _MODEL_ENV or not isinstance(ref, str)
        or not ref.startswith("env:") or not _ENV.fullmatch(ref[4:]) or ref[4:] in SECRET_ENV.values()
        for key, ref in environment.items()
    ):
        raise ConfigurationError("environment_refs must map approved model variables to environment references")
    model = {}
    if "model_policy" in data:
        model["model_policy"] = _model_policy(data["model_policy"])
        if environment:
            raise ConfigurationError("model-backed profiles cannot inject guest environment_refs")
        if any(not (Path(sources["openjules"]["checkout"]) / name).is_file() for name in MODEL_ENTRYPOINTS):
            raise ConfigurationError("OpenJules revision lacks the required managed model entry points")
    if "child_policy" in data:
        if "model_policy" not in model:
            raise ConfigurationError("child_policy requires an explicit model_policy")
        model["child_policy"] = _child_policy(data["child_policy"], runtime, seconds, capabilities)
        for name, entrypoints in CHILD_ENTRYPOINTS.items():
            if any(not (Path(sources[name]["checkout"]) / entrypoint).is_file() for entrypoint in entrypoints):
                raise ConfigurationError(f"{name} revision lacks the required managed child entry points")
    return {"schema_version": 1, "deployment_id": deployment, "binding": binding.to_dict(), "max_workers": maximum,
            "sources": sources, "source": source, "image": image, "command": command, "ports": ports,
            "runtime": runtime, "environment_refs": environment, **model,
            "policy": {"checks": checks, "reviewers": reviewers, "max_attempts": attempts,
                       "budget_seconds": budget, "attempt_seconds": seconds, "capabilities": capabilities}}


def _write(path: Path, value: Any, executable: bool = False) -> None:
    content = value if isinstance(value, str) else json.dumps(value, sort_keys=True, indent=2) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700 if executable else 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(configuration: Any, directory: str | Path) -> dict:
    data = parse_configuration(configuration)
    root = Path(directory).absolute()
    if root.exists() or root.is_symlink():
        raise ConfigurationError("deployment directory must not already exist")
    root = root.parent.resolve(strict=True) / root.name
    for item in [*data["sources"].values(), data["source"]]:
        source = Path(item["checkout"])
        if root == source or source in root.parents or root in source.parents:
            raise ConfigurationError("deployment data must be outside all source checkouts")
    root.mkdir(mode=0o700)
    for name in ("config", "secrets", "state", "state/openjules", "state/polyphony", "environments"):
        (root / name).mkdir(mode=0o700)
    deployment = data["deployment_id"]
    repository = data["binding"]["repository"]
    profile = data["binding"]["profile"]
    scope = {"deployment_id": deployment, "repository": repository}
    tokens = {name: secrets.token_urlsafe(48) for name in ("owner", "reviewer", "ingress", "runtime", "zpm")}
    credentials = {
        tokens["owner"]: {"identity": "operator", "role": "owner"},
        tokens["reviewer"]: {"identity": "operator", "role": "reviewer", "deployment_id": deployment, "repositories": [repository]},
        tokens["ingress"]: {"identity": "ingress", "role": "ingress", "deployment_id": deployment, "repositories": [repository]},
        tokens["runtime"]: {"identity": "openjules-runtime", "role": "runtime", "deployment_id": deployment, "repositories": [repository]},
    }
    _write(root / "secrets/polyphony.json", credentials)
    _write(root / "secrets/openjules.json", {tokens["zpm"]: [scope]})
    _write(root / "secrets/environment.json", {SECRET_ENV["zpm"]: tokens["zpm"], SECRET_ENV["openjules"]: tokens["runtime"]})
    _write(root / "secrets/operator.json", {"owner": tokens["owner"], "reviewer": tokens["reviewer"], "ingress": tokens["ingress"]})
    policy = data["policy"]
    canonical_profile = {
        "base": data["source"]["revision"], "attempt_seconds": policy["attempt_seconds"], "capabilities": policy["capabilities"],
    }
    if "child_policy" in data:
        child = data["child_policy"]
        canonical_profile.update({name: child[name] for name in ("max_children", "max_child_seconds", "max_child_turns")})
        canonical_profile["child_capabilities"] = child["capabilities"]
    _write(root / "config/polyphony.json", {
        "schema_version": 1, "deployment_id": deployment, "repositories": {repository: {
            "checks": policy["checks"], "reviewers": policy["reviewers"], "max_attempts": policy["max_attempts"],
            "budget_seconds": policy["budget_seconds"], "profiles": {profile: canonical_profile},
        }},
    })
    openjules = {
        "schema_version": 1, "host": "127.0.0.1", "port": data["ports"]["openjules"], "poll_interval_seconds": 1,
        "state_directory": str(root / "state/openjules/runtime"),
        "lifecycle_database": str(root / "state/openjules/lifecycle.sqlite3"),
        "credentials_file": str(root / "secrets/openjules.json"),
        "authority": {"endpoint": f"http://127.0.0.1:{data['ports']['polyphony']}", "token_ref": "env:" + SECRET_ENV["openjules"]},
        "profiles": [{"name": profile, **scope, "source_path": data["source"]["checkout"],
                      "base_commit": data["source"]["revision"], "image": data["image"], "command": data["command"],
                      **data["runtime"], "environment_refs": {
                          name: "env:ZPM_MODEL_" + name for name in data["environment_refs"]
                      }}],
    }
    if "model_policy" in data:
        openjules["models"] = [{**scope, "profile": profile, "policy": {
            **data["model_policy"], "token_ref": "env:" + MODEL_TOKEN_ENV,
        }}]
    if "child_policy" in data:
        openjules["children"] = [{**scope, "profile": profile, "policy": data["child_policy"]}]
    _write(root / "config/openjules.json", openjules)
    _write(root / "config/provider.json", ProviderConfig.parse({
        "schema_version": 1, "endpoint": f"http://127.0.0.1:{data['ports']['openjules']}",
        "token_ref": "env:" + SECRET_ENV["zpm"], "max_workers": data["max_workers"], "timeout_seconds": 10,
    }).__dict__)
    _write(root / "config/repository.json", data["binding"])
    with Store(root / "state/zpm.sqlite3", deployment) as store:
        store.enroll(data["binding"])
    (root / "state/zpm.sqlite3").chmod(0o600)
    manager = '''#!/usr/bin/env python3
import json
from pathlib import Path
import sys
root = Path(__file__).resolve().parent
manifest = json.loads((root / "manifest.json").read_text())
sys.path.insert(0, manifest["configuration"]["sources"]["zpm"]["checkout"])
from zpm.deployment import manage
raise SystemExit(manage(root, sys.argv[1:]))
'''
    _write(root / "manage.py", manager, executable=True)
    _write(root / "README.txt", _BUNDLE_README)
    sealed = [str(path.relative_to(root)) for folder in ("config", "secrets") for path in sorted((root / folder).iterdir())]
    sealed.extend(["manage.py", "README.txt"])
    manifest = {"schema_version": 1, "configuration": data, "directory": str(root),
                "files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sealed}}
    _write(root / "manifest.json", manifest)
    return {"prepared": True, "directory": str(root), "deployment_id": deployment,
            "repository": repository, "services_started": False, "remote_access_checked": False,
            "next": ["python3 manage.py bootstrap", "python3 manage.py start", "python3 manage.py doctor"]}


def verify(directory: str | Path) -> dict:
    root = Path(directory).resolve(strict=True)
    if not root.is_dir() or root.stat().st_mode & 0o077:
        raise ConfigurationError("deployment directory must be private (mode 0700)")
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_mode & 0o077:
        raise ConfigurationError("manifest must be a private regular file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise ConfigurationError("cannot read the deployment manifest") from None
    if not isinstance(manifest, dict):
        raise ConfigurationError("deployment manifest must be an object")
    if manifest.get("schema_version") != 1 or manifest.get("directory") != str(root):
        raise ConfigurationError("unsupported or moved deployment bundle")
    data = parse_configuration(manifest.get("configuration"))
    for name in ("config", "secrets", "state", "state/openjules", "state/polyphony", "environments"):
        path = root / name
        if path.is_symlink() or not path.is_dir() or path.stat().st_mode & 0o077:
            raise ConfigurationError("deployment directories must remain private")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ConfigurationError("deployment manifest has no integrity records")
    for name, digest in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or root / relative == manifest_path:
            raise ConfigurationError("invalid deployment manifest path")
        path = root / relative
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise ConfigurationError("bundle files must be private regular files")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ConfigurationError("deployment configuration integrity mismatch")
    return {"valid": True, "deployment_id": data["deployment_id"], "directory": str(root),
            "remote_access_checked": False, "runtime_readiness": "not_checked"}


def _environment(root: Path, service: str, data: dict) -> dict[str, str]:
    result = {name: value for name, value in os.environ.items() if name in {
        "PATH", "LANG", "LC_ALL", "LC_CTYPE", "SSL_CERT_FILE", "SSL_CERT_DIR",
    }}
    result.update(PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1")
    if service in SECRET_ENV:
        name = SECRET_ENV[service]
        result[name] = json.loads((root / "secrets/environment.json").read_text())[name]
    if service == "openjules":
        for target, reference in data["environment_refs"].items():
            name = reference.removeprefix("env:")
            if not os.environ.get(name):
                raise ConfigurationError("a configured model environment reference is unavailable")
            # Source reference names must not reintroduce PYTHONPATH,
            # LD_PRELOAD or other process controls into the trusted service.
            result["ZPM_MODEL_" + target] = os.environ[name]
        if "model_policy" in data:
            name = data["model_policy"]["token_ref"].removeprefix("env:")
            token = os.environ.get(name, "")
            if not token or any(character.isspace() for character in token):
                raise ConfigurationError("the configured model provider reference is unavailable or invalid")
            result[MODEL_TOKEN_ENV] = token
    return result


def _zpm_command(root: Path, data: dict, operation: str) -> list[str]:
    return [str(root / "environments/zpm/bin/python"), "-m", "zpm", "--state", str(root / "state/zpm.sqlite3"),
            "--deployment-id", data["deployment_id"], operation, "--provider", str(root / "config/provider.json")]


def _command(root: Path, service: str, data: dict) -> list[str]:
    python = str(root / "environments" / service / "bin/python")
    if service == "polyphony":
        return [python, "-m", "src.liminal_bridge.coordinator_service", "--db", str(root / "state/polyphony/authority.sqlite3"),
                "--credentials", str(root / "secrets/polyphony.json"), "--managed-config", str(root / "config/polyphony.json"),
                "--port", str(data["ports"]["polyphony"])]
    if service == "openjules":
        return [python, "-m", "src.worker.managed_service", "--config", str(root / "config/openjules.json")]
    return _zpm_command(root, data, "reconcile") + ["--watch"]


def _bootstrap_locked(root: Path, data: dict) -> dict:
    # Explicit dependency installation, separate from offline preparation.
    for service in SOURCE_FILES:
        checkout = Path(data["sources"][service]["checkout"])
        env_root = root / "environments" / service
        marker = env_root / "bootstrap-complete.json"
        if marker.exists():
            continue
        environment = _environment(root, "bootstrap", data)
        subprocess.run([sys.executable, "-m", "venv", str(env_root)], env=environment, check=True)
        python = str(env_root / "bin/python")
        install = [python, "-m", "pip", "install", "--disable-pip-version-check"]
        install += [str(checkout)] if service == "zpm" else ["-r", str(checkout / "requirements.txt")]
        subprocess.run(install, cwd=checkout, env=environment, check=True)
        resolved = subprocess.run([python, "-m", "pip", "freeze"], env=environment, check=True, capture_output=True, text=True)
        (env_root / "resolved-requirements.txt").write_text(resolved.stdout, encoding="utf-8")
        _write(marker, {"revision": data["sources"][service]["revision"], "python": sys.version})
    return {"installed": True, "services_started": False, "remote_access_checked": False}


@contextmanager
def _manager_lock(root: Path):
    import fcntl

    with (root / "state/stack.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigurationError("this deployment manager or bootstrap is already running") from None
        yield


def _bootstrap(root: Path, data: dict) -> dict:
    with _manager_lock(root):
        return _bootstrap_locked(root, data)


def _start(root: Path, data: dict) -> int:
    processes = []
    def stop_on_signal(signum, frame):
        raise KeyboardInterrupt

    with _manager_lock(root):
        previous = signal.signal(signal.SIGTERM, stop_on_signal)
        try:
            for service in ("polyphony", "openjules", "zpm"):
                processes.append(subprocess.Popen(_command(root, service, data), cwd=data["sources"][service]["checkout"],
                                                  env=_environment(root, service, data), start_new_session=True))
            while all(process.poll() is None for process in processes):
                time.sleep(0.25)
            return 1
        except KeyboardInterrupt:
            return 130
        finally:
            signal.signal(signal.SIGTERM, previous)
            # Stopping a service does not delete worker state, containers or
            # artifacts, and never asserts that a task process has terminated.
            for process in reversed(processes):
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)


def _doctor(root: Path, data: dict) -> dict:
    command = _zpm_command(root, data, "check")
    result = subprocess.run(command, cwd=data["sources"]["zpm"]["checkout"], env=_environment(root, "zpm", data),
                            capture_output=True, text=True, timeout=45)
    if result.returncode:
        return {"ready": False, "reason": "scoped OpenJules capability check failed", "remote_access_checked": True}
    report = json.loads(result.stdout)
    rows = report.get("repositories", [])
    ready = (report.get("deployment_id") == data["deployment_id"] and bool(rows)
             and all(row.get("validation", {}).get("ready") is True for row in rows))
    return {"ready": ready, "remote_access_checked": True, "capabilities": report}


def manage(directory: str | Path, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage a prepared single-host ZPM deployment")
    parser.add_argument("operation", choices=("verify", "bootstrap", "start", "doctor"))
    args = parser.parse_args(argv)
    try:
        report = verify(directory)
        root = Path(report["directory"])
        data = json.loads((root / "manifest.json").read_text())["configuration"]
        if args.operation == "start":
            return _start(root, data)
        if args.operation == "bootstrap":
            report = _bootstrap(root, data)
        elif args.operation == "doctor":
            report = _doctor(root, data)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 3 if args.operation == "doctor" and not report["ready"] else 0
    except ConfigurationError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        # Service and package-manager output is not copied into diagnostics.
        print(json.dumps({"error": "deployment operation failed; verify source pins, private configuration, dependencies and service availability"}), file=sys.stderr)
        return 2


_BUNDLE_README = """This is a private single-host deployment, not a portable export.

Run these commands from this directory, using Python 3.11 or newer on Linux:
  python3 manage.py verify
  python3 manage.py bootstrap
  python3 manage.py start

In a second terminal:
  python3 manage.py doctor

Prepare and verify are offline integrity checks. They do not prove readiness.
Bootstrap explicitly installs dependencies into three separate environments.
Start runs all services in the foreground and stops them together on Ctrl-C.
Doctor checks real authenticated repository-scoped OpenJules capabilities.
It does not run a coding task or verify independent review or forge publication.

The host requires a local Docker Engine accessible to the trusted OpenJules
service account, and the explicitly pinned image must already be present.
The Docker socket is host-control authority. Never give it to worker containers.
The approved command runs without network access. Model credentials referenced
in configuration must be supplied to the start command's environment. Optional
model_policy credentials resolve only in the trusted OpenJules service, which
brokers inference through a worker-owned Unix socket. Model-backed profiles
reject guest environment_refs. Control credentials never enter task containers.
An optional child_policy requires model_policy, matching canonical child grants
and a new approved profile. Child expiry or cancellation retires the whole
worker. Children share root allowances and do not add ZPM replicas.
The model socket requires rootful Linux Docker with the approved task UID mapping.
Doctor does not contact the provider or prove model compatibility or quality.

All listeners bind loopback. Runtime, ingress, owner, reviewer and ZPM tokens
are separate private files under secrets/. Do not commit or publish this bundle.
The owner and reviewer tokens identify the same operator, not independent people.
Authority acceptance is separate from GitHub PR checks, merging and release.

Keep state/, secrets/ and source checkouts across restarts. Do not copy live
SQLite WAL files, create a second manager with the same deployment identity,
move source checkouts, edit approved profiles in place, or remove retained Docker
containers while an execution/capture outcome is unresolved. To stop capacity,
set the ZPM target to zero, reconcile and verify that reservations release first.
Stopping the stack only stops its services; it does not prove task termination.

Source revisions are pinned and checked before each operation. Dependency
versions are recorded after installation in each environment's
resolved-requirements.txt; upstream ranges are not claimed to be a lockfile.
"""
