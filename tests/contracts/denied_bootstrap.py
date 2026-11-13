"""Run the installed bootstrap after the trial has denied IP networking.

The parent invokes the system unshare and setpriv tools. No repository code
executes with elevated privileges. Service execution happens later in the
ordinary runner network namespace so the canonical loopback trial still works.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import re
import resource
import socket
import stat
import subprocess
import sys
import time


def require(value, message):
    if not value:
        raise ValueError(message)


def network_evidence(parent_namespace, uid, gid, parent_pid_namespace=None):
    require(uid > 0 and gid > 0 and os.getuid() == os.geteuid() == uid and
            os.getgid() == os.getegid() == gid and 0 not in os.getgroups(),
            "bootstrap namespace did not drop to the ordinary runner identity")
    current = os.readlink("/proc/self/ns/net")
    require(re.fullmatch(r"net:\[\d+\]", parent_namespace) and current != parent_namespace,
            "bootstrap is not in a separate network namespace")
    if parent_pid_namespace is not None:
        require(re.fullmatch(r"pid:\[\d+\]", parent_pid_namespace) and
                os.readlink("/proc/self/ns/pid") != parent_pid_namespace and os.getpid() == 1,
                "bootstrap helper is not the isolated process-namespace init")
        require(os.readlink("/proc/self") == str(os.getpid()),
                "bootstrap proc mount does not match its process namespace")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    require(status.get("NoNewPrivs", "").strip() == "1" and
            all(int(status[name].strip(), 16) == 0 for name in ("CapEff", "CapPrm", "CapAmb", "CapBnd")),
            "bootstrap namespace retained process privileges")
    interfaces = sorted(name for _, name in socket.if_nameindex())
    require(interfaces == ["lo"], "bootstrap namespace has an unexpected network interface")
    require(len(Path("/proc/net/route").read_text().strip().splitlines()) <= 1,
            "bootstrap namespace retains an IPv4 route")
    probes = []
    for family, address in ((socket.AF_INET, "192.0.2.1"), (socket.AF_INET, "127.0.0.1"),
                            (socket.AF_INET6, "2001:db8::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as client:
                client.settimeout(1)
                result = client.connect_ex((address, 443))
        except OSError as exc:
            result = exc.errno
        require(result in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EADDRNOTAVAIL,
                           errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT},
                "bootstrap IP probe did not establish a denied network path")
        probes.append({"family": "ipv4" if family == socket.AF_INET else "ipv6",
                       "destination": address, "errno": result})
    return {"network_namespace_distinct": True, "process_namespace_init": parent_pid_namespace is not None,
            "proc_matches_pid_namespace": parent_pid_namespace is not None,
            "ordinary_runner_uid": uid, "ordinary_runner_gid": gid,
            "no_new_privileges": True, "capabilities_empty": True, "interfaces": interfaces,
            "ip_probes": probes, "scope": "IP networking denied during bootstrap; later services use explicit loopback"}


def storage_bytes(directory):
    logical, allocated, entries, links = 0, 0, 0, 0
    if not directory.exists():
        return {"logical_regular_file_bytes": 0, "allocated_bytes": 0, "entries": 0, "symlinks_not_followed": 0}
    pending = [directory]
    while pending:
        path = pending.pop()
        info = path.lstat()
        entries += 1
        require(entries <= 200000, "bootstrap storage inventory exceeds its entry bound")
        allocated += info.st_blocks * 512
        if stat.S_ISREG(info.st_mode):
            logical += info.st_size
        elif stat.S_ISLNK(info.st_mode):
            links += 1
        elif stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        else:
            require(False, "bootstrap storage contains an unexpected nonregular path")
    return {"logical_regular_file_bytes": logical, "allocated_bytes": allocated,
            "entries": entries, "symlinks_not_followed": links}


def run(manager, bundle, evidence_path, parent_namespace, uid, gid, parent_pid_namespace=None):
    require(not evidence_path.exists(), "bootstrap network evidence must be new")
    evidence = {"bootstrap_returncode": None, "bootstrap_outcome": "not_started"}
    started = time.monotonic_ns()
    try:
        evidence.update(network_evidence(parent_namespace, uid, gid, parent_pid_namespace))
    except (ValueError, OSError) as exc:
        evidence.update(bootstrap_outcome="isolation_refused", error_type=type(exc).__name__)
        evidence_path.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        raise
    require(manager == bundle / "manage.py" and manager.is_file() and not manager.is_symlink(),
            "expected the generated deployment manager")
    hostile = evidence_path.parent / (evidence_path.stem + "-ambient")
    hostile.mkdir(mode=0o700)
    configuration = hostile / "pip.conf"
    configuration.write_text("[global]\nindex-url = http://127.0.0.1:9/unapproved\n"
                             "extra-index-url = https://unapproved.invalid/simple\n"
                             "find-links = https://unapproved.invalid/wheels\n", encoding="utf-8")
    environment = dict(os.environ)
    target = hostile / "unapproved-target"
    environment.update(HOME=str(hostile), PIP_CONFIG_FILE=str(configuration),
                       PIP_INDEX_URL="http://127.0.0.1:9/unapproved",
                       PIP_EXTRA_INDEX_URL="https://unapproved.invalid/simple",
                       PIP_FIND_LINKS="https://unapproved.invalid/wheels",
                       PIP_TARGET=str(target), PIP_CACHE_DIR=str(hostile / "unapproved-cache"),
                       PIP_REQUIRE_VIRTUALENV="true")
    evidence["hostile_ambient_pip_configuration"] = True
    evidence["bootstrap_outcome"] = "started"
    # Retain proven isolation before executing package code. A forced external
    # stop may prevent final reporting; it cannot leave namespace descendants.
    evidence_path.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    try:
        result = subprocess.run([sys.executable, str(manager), "bootstrap"], cwd=bundle, env=environment,
                                check=False, timeout=870)
        evidence["bootstrap_returncode"] = result.returncode
        evidence["bootstrap_outcome"] = "completed" if result.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        evidence["bootstrap_outcome"] = "timed_out"
        raise
    except (OSError, ValueError) as exc:
        evidence.update(bootstrap_outcome="failed", error_type=type(exc).__name__)
        raise
    finally:
        evidence["elapsed_ns"] = time.monotonic_ns() - started
        evidence["peak_waited_child_rss_bytes"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024
        evidence["rss_scope"] = "Linux maximum RSS of the largest waited child; not a sum of concurrent process memory"
        evidence["ambient_target_unused"] = not target.exists()
        evidence["descendant_cleanup"] = "kernel terminates all process-namespace descendants when this init exits"
        # Save outcome first so a failed storage observation cannot erase it.
        evidence_path.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        try:
            evidence["installed_storage"] = {name: storage_bytes(bundle / "environments" / name)
                                               for name in ("zpm", "openjules", "polyphony")}
            evidence["retained_environment_and_build_storage"] = storage_bytes(bundle / "environments")
            evidence["sealed_input_storage"] = storage_bytes(bundle / "dependencies")
        except (ValueError, OSError) as exc:
            evidence["storage_observation_error"] = type(exc).__name__
        evidence_path.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    require(evidence["bootstrap_returncode"] == 0 and not target.exists() and
            "storage_observation_error" not in evidence, "network-denied sealed bootstrap failed")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manager", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--parent-network-namespace", required=True)
    parser.add_argument("--parent-pid-namespace", required=True)
    parser.add_argument("--uid", type=int, required=True)
    parser.add_argument("--gid", type=int, required=True)
    args = parser.parse_args()
    run(args.manager, args.bundle, args.evidence, args.parent_network_namespace, args.uid, args.gid,
        args.parent_pid_namespace)


if __name__ == "__main__":
    main()
