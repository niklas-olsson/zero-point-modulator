"""Private installer context and a bounded, offline OpenJules contract probe.

The selector restricts legacy entry points in the dedicated interpreter. It is
not task authority and never changes canonical credentials, policy or state.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

from .models import ConfigurationError

SELECTOR_NAME = "openjules-managed-installation.json"
SELECTOR_LIMIT = 4096
CONFIG_LIMIT = 1_000_000
PROBE_SECONDS = 10
PROBE_REAP_SECONDS = 2
PROBE_MODULE = "src.worker.execution_context"
_CONTEXT_ERROR = "OpenJules managed installation context is missing or does not match the sealed configuration"
_PROBE_ERROR = "OpenJules does not confirm the required managed installation context"


def _present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        raise ConfigurationError(_CONTEXT_ERROR) from None
    return True


def _private_regular(path: Path, limit: int | None) -> bytes:
    # Do not block opening a FIFO before discovering that it is not regular.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                or (limit is not None and info.st_size > limit)):
            raise ConfigurationError(_CONTEXT_ERROR)
        if limit is None:
            limit = info.st_size  # Preserve the existing manifest size contract.
        content = stream.read(limit + 1)
        if len(content) > limit:
            raise ConfigurationError(_CONTEXT_ERROR)
        return content


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate JSON key")
        result[name] = value
    return result


def expected_selector(root: Path) -> bytes:
    """Derive the only permitted selector from the already verified bundle."""
    try:
        root = root.resolve(strict=True)
        config = root / "config/openjules.json"
        if config.resolve(strict=True) != config:
            raise ConfigurationError(_CONTEXT_ERROR)
        content = _private_regular(config, CONFIG_LIMIT)
        # manage() verifies the complete private manifest before any operation.
        # Check this binding again, rather than blessing changed working bytes.
        manifest = json.loads(_private_regular(root / "manifest.json", None).decode("utf-8"),
                              object_pairs_hook=_unique_object)
        digest = manifest["files"]["config/openjules.json"]
        if not isinstance(digest, str) or hashlib.sha256(content).hexdigest() != digest:
            raise ConfigurationError(_CONTEXT_ERROR)
        value = {"schema_version": 1, "managed_config_path": str(config),
                 "managed_config_sha256": digest}
        encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > SELECTOR_LIMIT:
            raise ConfigurationError(_CONTEXT_ERROR)
        return encoded
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        raise ConfigurationError(_CONTEXT_ERROR) from None


def selector_path(root: Path) -> Path:
    return root / "environments/openjules" / SELECTOR_NAME


def validate_selector(root: Path, *, required: bool = True) -> bytes | None:
    """Read only. A present foreign, malformed or changed file is never repaired."""
    expected = expected_selector(root)
    path = selector_path(root)
    try:
        environment = path.parent
        if _present(environment) and (environment.is_symlink() or not environment.is_dir()
                                             or environment.resolve(strict=True) != environment):
            raise ConfigurationError(_CONTEXT_ERROR)
        if not _present(path):
            if required:
                raise ConfigurationError(_CONTEXT_ERROR)
            return None
        actual = _private_regular(path, SELECTOR_LIMIT)
        if actual != expected:
            raise ConfigurationError(_CONTEXT_ERROR)
        return actual
    except (OSError, ValueError, RuntimeError):
        raise ConfigurationError(_CONTEXT_ERROR) from None


def publish_private(path: Path, content: bytes) -> None:
    """Publish an absent private file without replacing any existing path.

    The bootstrap lock coordinates managers. Atomic hard-link publication also
    preserves a destination created by another writer. A killed writer can
    leave its private temporary file for diagnosis; retries never reuse it.
    """
    temporary = None
    try:
        parent = path.parent
        if parent.is_symlink() or not parent.is_dir() or parent.resolve(strict=True) != parent:
            raise ConfigurationError(_CONTEXT_ERROR)
        if len(content) > SELECTOR_LIMIT:
            raise ConfigurationError(_CONTEXT_ERROR)
        if _present(path):
            if _private_regular(path, SELECTOR_LIMIT) != content:
                raise ConfigurationError(_CONTEXT_ERROR)
            return
        descriptor, name = tempfile.mkstemp(prefix=".managed-context-", dir=parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if _private_regular(path, SELECTOR_LIMIT) != content:
                raise ConfigurationError(_CONTEXT_ERROR)
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        if _private_regular(path, SELECTOR_LIMIT) != content:
            raise ConfigurationError(_CONTEXT_ERROR)
    except (OSError, ValueError, RuntimeError):
        raise ConfigurationError(_CONTEXT_ERROR) from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Retain only this owned private temporary file for diagnosis.


def publish_selector(root: Path) -> None:
    publish_private(selector_path(root), expected_selector(root))
    validate_selector(root)


def completed(root: Path, revision: str) -> bool:
    """An existing completion marker is retained evidence, never a repair hint."""
    path = root / "environments/openjules/bootstrap-complete.json"
    if not _present(path):
        return False
    try:
        value = json.loads(_private_regular(path, SELECTOR_LIMIT).decode("utf-8"),
                           object_pairs_hook=_unique_object)
        if (not isinstance(value, dict) or set(value) != {"revision", "python"}
                or value["revision"] != revision or not isinstance(value["python"], str)
                or not value["python"]):
            raise ConfigurationError(_CONTEXT_ERROR)
        return True
    except (OSError, ValueError, TypeError, RuntimeError):
        raise ConfigurationError(_CONTEXT_ERROR) from None


def probe(root: Path, checkout: Path, environment: dict[str, str], *, require_complete: bool = False,
          revision: str | None = None) -> None:
    """Use the actual installed interpreter and pinned service working directory.

    Stdout is bounded to 4 KiB, stderr is discarded, and diagnostics never echo
    child output. The 10-second execution deadline has a separate 2-second
    owned-process reap bound. No network, Docker, model or authority probe runs.
    """
    if require_complete and (revision is None or not completed(root, revision)):
        raise ConfigurationError("OpenJules bootstrap is incomplete; run bootstrap before start or doctor")
    raw = validate_selector(root)
    expected = hashlib.sha256(raw).hexdigest()
    command = [str(root / "environments/openjules/bin/python"), "-m", PROBE_MODULE,
               "--probe-managed-installation"]
    process = None
    output = bytearray()
    try:
        deadline = time.monotonic() + PROBE_SECONDS
        process = subprocess.Popen(command, cwd=checkout, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   close_fds=True, start_new_session=True)
        assert process.stdout is not None
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as watcher:
            watcher.register(process.stdout, selectors.EVENT_READ)
            finished_output = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ConfigurationError(_PROBE_ERROR)
                for key, _ in watcher.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, SELECTOR_LIMIT + 1 - len(output))
                    if not chunk:
                        watcher.unregister(key.fileobj)
                        finished_output = True
                    else:
                        output.extend(chunk)
                        if len(output) > SELECTOR_LIMIT:
                            raise ConfigurationError(_PROBE_ERROR)
                if finished_output:
                    # Observe exit without reaping the leader. Keeping its PID
                    # owned makes the later process-group kill unambiguous,
                    # including a descendant that inherited or closed stdout.
                    exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    if exited is not None:
                        break
        if exited.si_code != os.CLD_EXITED or exited.si_status != 0:
            raise ConfigurationError(_PROBE_ERROR)
        report = json.loads(output.decode("utf-8"), object_pairs_hook=_unique_object)
        if (not isinstance(report, dict)
                or set(report) != {"schema_version", "selector_sha256", "legacy_execution_allowed"}
                or type(report["schema_version"]) is not int or report["schema_version"] != 1
                or report["selector_sha256"] != expected or report["legacy_execution_allowed"] is not False):
            raise ConfigurationError(_PROBE_ERROR)
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError):
        raise ConfigurationError(_PROBE_ERROR) from None
    finally:
        original_failure = sys.exc_info()[0] is not None
        if process is not None:
            cleanup_failed = False
            # No poll()/wait() above has released this process identity.
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    cleanup_failed = True
            try:
                process.wait(timeout=PROBE_REAP_SECONDS)
            except (OSError, subprocess.SubprocessError):
                cleanup_failed = True
            finally:
                if process.stdout is not None:
                    process.stdout.close()
            if cleanup_failed and not original_failure:
                raise ConfigurationError(_PROBE_ERROR) from None
