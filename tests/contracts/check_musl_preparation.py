#!/usr/bin/env python3
"""Closed, native Alpine publication-primitive qualification, not a service stack.

The host needs Python 3.12, Git, OpenSSL, Docker, e2fsprogs, util-linux and sudo.
Only named loop mounts need host sudo. Downloaded inputs are verified before
Docker imports or executes them. Docker network=none retains local loopback.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import ctypes
import hashlib
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import secrets
import select
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import urllib.request
import zlib

CAPS = {"transfer_bytes": 128 * 1024**2, "acquisition_seconds": 300,
        "decoded_bytes": 512 * 1024**2, "operations_seconds": 900,
        "observed_owned_bytes": 3 * 1024**3, "container_memory_bytes": 512 * 1024**2,
        "container_pids": 128, "filesystem_bytes": 256 * 1024**2,
        "command_output_bytes": 1024**2, "http_metadata_reservation_per_request": 65536}
BASE_URL = "https://dl-cdn.alpinelinux.org/alpine/v3.22/"
HEX = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9+_.@-]*\Z")
REPOSITORY = Path(__file__).resolve().parents[2]


class Refused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def exact_keys(value, keys, context):
    require(isinstance(value, dict) and set(value) == set(keys), f"invalid {context} fields")


def load_inputs(path):
    require(path.stat().st_size <= 128 * 1024, "input manifest is too large")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    result = json.loads(path.read_text(), object_pairs_hook=unique)
    exact_keys(result, {"schema", "purpose", "base", "keys", "index_provenance", "packages",
                        "expected_installed", "runtime", "caps"}, "manifest")
    require(result["schema"] == "zpm-native-musl-preparation-v1", "unsupported input schema")
    require(result["purpose"] == "native-publication-primitive-only", "incorrect fixture scope")
    require(result["caps"] == CAPS, "fixture resource caps differ")
    require(result["runtime"] == {"architecture": "x86_64", "python": "3.12.16", "musl": "1.2.5-r13",
                                  "alpine": "3.22.8", "pointer_bytes": 8, "long_bytes": 8}, "runtime identity differs")
    base = result["base"]
    exact_keys(base, {"filename", "url", "size", "sha256", "libc_sha256"}, "base")
    require(base["filename"] == "alpine-minirootfs-3.22.8-x86_64.tar.gz" and
            base["url"] == BASE_URL + "releases/x86_64/" + base["filename"], "unexpected base origin")
    require(base["size"] == 3647469 and base["sha256"] ==
            "2d7f46af9b7e86ad64ebeba3fe9b75b62dd5b259e708afb2ab21268f627696e5", "unapproved base")
    require(isinstance(base["libc_sha256"], str) and HEX.fullmatch(base["libc_sha256"]), "invalid libc digest")
    keys = result["keys"]
    require(isinstance(keys, list) and 1 <= len(keys) <= 5, "invalid key inventory")
    names = set()
    for key in keys:
        exact_keys(key, {"filename", "size", "sha256"}, "key")
        require(NAME.fullmatch(key["filename"]) and key["filename"].endswith(".rsa.pub") and
                key["filename"] not in names and key["size"] in {451, 800} and HEX.fullmatch(key["sha256"]), "invalid key pin")
        names.add(key["filename"])
    provenance = result["index_provenance"]
    exact_keys(provenance, {"url", "size", "sha256", "signature_key", "signature_verified",
                            "selection", "source", "acquisition_reserved_bytes"}, "index provenance")
    require(provenance["url"] == BASE_URL + "main/x86_64/APKINDEX.tar.gz" and
            provenance["size"] == 500505 and provenance["sha256"] ==
            "89d2b25d10085227d3c4ff0fbb25b3e39d18a84e5bd53b65b3e889c4eea0400b" and
            provenance["signature_verified"] is True and provenance["signature_key"] in names,
            "unreviewed signed-index provenance")
    packages = result["packages"]
    require(isinstance(packages, list) and 1 <= len(packages) <= 48, "invalid package count")
    package_names = set()
    for row in packages:
        exact_keys(row, {"name", "version", "architecture", "index_architecture", "filename", "url", "size",
                        "sha256", "control_checksum", "data_sha256", "signature_key", "installed_size"}, "package")
        require(NAME.fullmatch(row["name"]) and NAME.fullmatch(row["version"]) and row["name"] not in package_names,
                "invalid or repeated package identity")
        require(row["filename"] == f'{row["name"]}-{row["version"]}.apk' and
                row["url"] == BASE_URL + "main/x86_64/" + row["filename"], "unexpected package origin")
        require(row["architecture"] in {"x86_64", "noarch"} and row["index_architecture"] == "x86_64",
                "incompatible APK architecture")
        require(type(row["size"]) is int and 0 < row["size"] <= 32 * 1024**2 and
                type(row["installed_size"]) is int and 0 <= row["installed_size"] <= CAPS["decoded_bytes"], "invalid package bounds")
        require(HEX.fullmatch(row["sha256"]) and HEX.fullmatch(row["data_sha256"]) and
                re.fullmatch(r"Q1[A-Za-z0-9+/]{27}=", row["control_checksum"]) and row["signature_key"] in names,
                "invalid package integrity record")
        package_names.add(row["name"])
    inventory = result["expected_installed"]
    require(isinstance(inventory, dict) and len(inventory) == len(packages) + 16, "invalid installed inventory")
    for name, record in inventory.items():
        exact_keys(record, {"version", "control_checksum"}, "installed package")
        require(NAME.fullmatch(name) and NAME.fullmatch(record["version"]) and
                re.fullmatch(r"Q1[A-Za-z0-9+/]{27}=", record["control_checksum"]), "invalid installed identity")
    for row in packages:
        require(inventory[row["name"]] == {"version": row["version"], "control_checksum": row["control_checksum"]},
                "selected and installed inventories differ")
    require(sum(row["size"] for row in packages) + base["size"] +
            (len(packages) + 1) * CAPS["http_metadata_reservation_per_request"] + provenance["size"] <= CAPS["transfer_bytes"],
            "declared acquisition exceeds cap")
    require(sum(row["installed_size"] for row in packages) <= CAPS["decoded_bytes"], "declared decoded input exceeds cap")
    return result


def gzip_streams(raw, expected, decoded_limit=CAPS["decoded_bytes"]):
    result, total, remaining = [], 0, raw
    while remaining:
        require(len(result) < expected, "unexpected gzip stream or trailing bytes")
        try:
            decoder = zlib.decompressobj(31)
            value = decoder.decompress(remaining, decoded_limit - total + 1)
        except zlib.error as error:
            raise Refused("invalid gzip stream") from error
        require(decoder.eof and not decoder.unconsumed_tail, "incomplete or oversized gzip stream")
        consumed = len(remaining) - len(decoder.unused_data)
        total += len(value)
        require(consumed > 0 and total <= decoded_limit, "decoded input cap exceeded")
        result.append((remaining[:consumed], value))
        remaining = decoder.unused_data
    require(len(result) == expected, "missing gzip stream")
    return result


def tar_entries(raw, metadata_only=False):
    entries = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for member in archive:
                name = member.name.removeprefix("./")
                path = PurePosixPath(name)
                require(name and not name.startswith("/") and ".." not in path.parts and
                        str(path) == name and name not in entries and not member.issparse() and
                        not any(key.startswith("GNU.sparse") for key in member.pax_headers), "unsafe APK tar member")
                require(member.isfile() or (not metadata_only and (member.isdir() or member.issym() or member.islnk())),
                        "unsupported APK tar member type")
                require(0 <= member.size <= CAPS["decoded_bytes"], "oversized APK member")
                if member.issym() or member.islnk():
                    # Rooted and ../ links are normal in Alpine. Resolve them
                    # lexically within the archive root, never on the host.
                    require(bool(member.linkname), "empty APK link")
                    destination = [] if member.linkname.startswith("/") or member.islnk() else list(path.parent.parts)
                    for component in PurePosixPath(member.linkname).parts:
                        if component in {"/", "."}:
                            continue
                        if component == "..":
                            require(bool(destination), "APK link escapes root")
                            destination.pop()
                        else:
                            destination.append(component)
                stream = archive.extractfile(member) if member.isfile() else None
                entries[name] = (member, stream.read() if stream else None)
                require(len(entries) <= 100000, "too many APK members")
            require(not any(raw[archive.offset:]), "unparsed tar payload")
    except (tarfile.TarError, ValueError, KeyError) as error:
        raise Refused("invalid APK tar") from error
    return entries


def check_file(path, row):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == row["size"], "input file size differs")
    value = path.read_bytes()
    require(digest(value) == row["sha256"], "input file digest differs")
    return value


def verify_base(path, inputs, key_directory):
    parts = gzip_streams(check_file(path, inputs["base"]), 1)
    entries = tar_entries(parts[0][1])
    key_directory.mkdir()
    for row in inputs["keys"]:
        name = "etc/apk/keys/" + row["filename"]
        require(name in entries and entries[name][0].isfile(), "base trust key is absent")
        data = entries[name][1]
        require(len(data) == row["size"] and digest(data) == row["sha256"], "base trust key differs")
        (key_directory / row["filename"]).write_bytes(data)
    require(digest(entries["lib/ld-musl-x86_64.so.1"][1]) == inputs["base"]["libc_sha256"], "base libc differs")
    return len(parts[0][1])


def verify_package(path, row, keys, run):
    parts = gzip_streams(check_file(path, row), 3)
    signature_entries = tar_entries(parts[0][1], metadata_only=True)
    expected_name = ".SIGN.RSA." + row["signature_key"]
    require(set(signature_entries) == {expected_name}, "unexpected APK signature inventory")
    signature = signature_entries[expected_name][1]
    require(128 <= len(signature) <= 1024, "invalid RSA signature size")
    control_checksum = "Q1" + base64.b64encode(hashlib.sha1(parts[1][0]).digest()).decode()
    require(control_checksum == row["control_checksum"], "signed index control checksum differs")
    with tempfile.TemporaryDirectory(prefix="signature-", dir=keys.parent) as directory:
        signature_path = Path(directory) / "signature"
        control_path = Path(directory) / "control.gz"
        signature_path.write_bytes(signature)
        control_path.write_bytes(parts[1][0])
        output = run(["openssl", "dgst", "-sha1", "-verify", str(keys / row["signature_key"]),
                      "-signature", str(signature_path), str(control_path)], timeout=10)
        require(output.strip() == "Verified OK", "APK control signature verification failed")
    control = tar_entries(parts[1][1], metadata_only=True)
    require(".PKGINFO" in control and set(control) <= {".PKGINFO", ".pre-install", ".post-install", ".pre-upgrade",
            ".post-upgrade", ".pre-deinstall", ".post-deinstall", ".trigger"}, "unexpected APK control inventory")
    fields = {}
    for line in control[".PKGINFO"][1].decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        require(" = " in line, "invalid APK control field")
        key, value = line.split(" = ", 1)
        fields.setdefault(key, []).append(value)
    for key, value in (("pkgname", row["name"]), ("pkgver", row["version"]), ("arch", row["architecture"]),
                       ("datahash", row["data_sha256"])):
        require(fields.get(key) == [value], "authenticated APK identity differs")
    require(digest(parts[2][0]) == row["data_sha256"], "authenticated APK data hash differs")
    tar_entries(parts[2][1])
    return sum(len(value) for compressed, value in parts)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise Refused("input origin redirected")


class Trial:
    def __init__(self, root, inputs):
        self.root, self.inputs = root, inputs
        self.started = time.monotonic()
        self.deadline = self.started + CAPS["acquisition_seconds"] + CAPS["operations_seconds"]
        self.native_deadline = None
        self.peak_owned_bytes = 0
        self.images, self.containers, self.loops, self.mounts = [], [], [], []
        self.loop_backings = []
        self.image_ids = {}
        self.prefix = "zpm-musl-" + secrets.token_hex(6)
        self.cleanup_errors = []
        self.image_sizes, self.container_sizes = {}, {}
        home = root / "host-home"
        home.mkdir()
        config = root / "docker-config"
        config.mkdir()
        (config / "config.json").write_text("{}\n")
        tmp = root / "host-tmp"
        tmp.mkdir()
        self.env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": str(home), "TMPDIR": str(tmp), "LC_ALL": "C",
                    "DOCKER_HOST": "unix:///var/run/docker.sock", "DOCKER_CONFIG": str(config),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1"}

    def owned_bytes(self):
        # Logical own image sizes intentionally double-count shared layers. This
        # is conservative owned-artifact accounting, not daemon physical usage.
        total = 0
        def unreadable(error):
            raise Refused("owned storage could not be measured") from error
        for directory, dirs, files in os.walk(self.root, onerror=unreadable):
            # Each loop file's entire fixed capacity is already counted. Do not
            # double-walk its mounted contents (including root-only lost+found).
            dirs[:] = [name for name in dirs if Path(directory) / name not in self.mounts]
            for name in files:
                path = Path(directory) / name
                info = path.lstat()
                if not path.is_symlink():
                    total += info.st_size
        total += sum(self.image_sizes.values()) + sum(self.container_sizes.values())
        self.peak_owned_bytes = max(total, self.peak_owned_bytes)
        require(total <= CAPS["observed_owned_bytes"], "observed owned-artifact storage cap exceeded")
        return total

    def run(self, command, timeout=120, cwd=None, env=None, tolerate=False, account=True, binary=False):
        deadline = min(self.deadline, time.monotonic() + timeout)
        output = bytearray()
        process = subprocess.Popen(command, cwd=cwd, env=env or self.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        failed = None
        try:
            while True:
                require(time.monotonic() < deadline, "command deadline exceeded")
                if account:
                    self.owned_bytes()
                ready, _, _ = select.select([process.stdout], [], [], 0.25)
                if ready:
                    value = os.read(process.stdout.fileno(), 65536)
                    output.extend(value)
                    require(len(output) <= CAPS["command_output_bytes"], "command output cap exceeded")
                    if not value and process.poll() is not None:
                        break
                elif process.poll() is not None:
                    break
            status = process.wait(timeout=1)
            require(tolerate or status == 0, "command failed: " + Path(command[0]).name)
        except BaseException as error:
            failed = error
        finally:
            # Always terminate our entire process group, including descendants
            # whose original leader has already exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            process.stdout.close()
        if failed is not None:
            raise failed
        return bytes(output) if binary else output.decode("utf-8", "replace")

    def docker(self, *args, **kwargs):
        return self.run(["docker", *args], **kwargs)

    def measure_docker(self):
        for identity in self.images:
            row, = json.loads(self.docker("image", "inspect", identity, account=False, timeout=15))
            require(row["Id"] == self.image_ids[identity] and type(row["Size"]) is int and row["Size"] >= 0, "image measurement missing")
            self.image_sizes[identity] = row["Size"]
        for name in self.containers:
            row, = json.loads(self.docker("container", "inspect", "--size", name, account=False, timeout=15))
            require(type(row.get("SizeRw")) is int and row["SizeRw"] >= 0, "container measurement missing")
            self.container_sizes[name] = row["SizeRw"]
        self.owned_bytes()

    def acquire(self):
        started = time.monotonic()
        rows = [self.inputs["base"], *self.inputs["packages"]]
        reserved = sum(row["size"] for row in rows) + len(rows) * CAPS["http_metadata_reservation_per_request"]
        # Charge the reviewed signed-index acquisition too, although CI never
        # fetches or resolves against that mutable URL.
        reserved += self.inputs["index_provenance"]["size"] + CAPS["http_metadata_reservation_per_request"]
        require(reserved <= CAPS["transfer_bytes"], "acquisition reservation exceeds cap")
        inputs = self.root / "inputs"
        inputs.mkdir()
        admitted = self.root / "admitted-inputs.json"
        admitted.write_text(json.dumps(self.inputs))
        acquisition_end = started + CAPS["acquisition_seconds"]
        prior_deadline = self.deadline
        self.deadline = min(self.deadline, acquisition_end)
        # DNS, slow reads and thread-pool shutdown all live in this owned group.
        # The parent enforces one total deadline and kills/reaps the group.
        self.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--fetch", str(admitted),
                  "--destination", str(inputs)], timeout=CAPS["acquisition_seconds"])
        keys = self.root / "keys"
        decoded = verify_base(inputs / self.inputs["base"]["filename"], self.inputs, keys)
        for row in self.inputs["packages"]:
            decoded += verify_package(inputs / row["filename"], row, keys, self.run)
            require(decoded <= CAPS["decoded_bytes"], "aggregate decoded-input cap exceeded")
        require(time.monotonic() - started <= CAPS["acquisition_seconds"], "verified acquisition deadline exceeded")
        self.deadline = prior_deadline
        self.owned_bytes()
        return {"status": "verified-before-execution", "base_sha256": self.inputs["base"]["sha256"],
                "packages": len(self.inputs["packages"]), "payload_bytes": sum(row["size"] for row in rows),
                "reserved_transfer_bytes": reserved, "decoded_bytes": decoded,
                "metadata_scope": "64 KiB reserved per request; includes reviewed index; excludes transport framing/TLS",
                "seconds": round(time.monotonic() - started, 3), "index_resolution_at_runtime": False}

    def snapshot(self):
        revision = self.run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY).strip()
        require(re.fullmatch(r"[0-9a-f]{40}", revision), "invalid source revision")
        source = self.root / "source"
        source.mkdir()
        inventory = []
        listing = self.run(["git", "ls-tree", "-r", "--full-tree", revision], cwd=REPOSITORY)
        for line in listing.splitlines():
            record, name = line.split("\t", 1)
            mode, kind, identity = record.split()
            if not (name.startswith("zpm/") or name.startswith("tests/")):
                continue
            require(kind == "blob" and mode in {"100644", "100755"} and ".." not in PurePosixPath(name).parts,
                    "unsupported source snapshot member")
            # git show produces text here; preserve exact bytes through UTF-8.
            data = self.run(["git", "show", f"{revision}:{name}"], cwd=REPOSITORY, binary=True)
            require(hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest() == identity, "source blob bytes differ")
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            inventory.append({"path": name, "git_blob": identity, "size": len(data)})
        require((source / "tests/test_preparation_recovery.py").is_file(), "source test snapshot incomplete")
        require((source / "tests/contracts/check_musl_preparation.py").read_bytes() == Path(__file__).read_bytes(),
                "host harness differs from the exact source snapshot")
        self.owned_bytes()
        return source, {"revision": revision, "files": len(inventory),
                        "inventory_sha256": digest(json.dumps(inventory, sort_keys=True).encode()),
                        "execution": "exact tracked Git blobs; dirty and untracked worktree bytes excluded"}

    def create_container(self, name, image, command, mounts, user=None):
        args = ["create", "--name", name, "--network", "none", "--memory", str(CAPS["container_memory_bytes"]),
                "--memory-swap", str(CAPS["container_memory_bytes"]), "--pids-limit", str(CAPS["container_pids"]),
                "--security-opt", "no-new-privileges", "--label", "org.zpm.native-primitive=" + self.root.name]
        if user is not None:
            args += ["--user", user, "--read-only", "--cap-drop", "ALL", "--env", "HOME=/scratch/home",
                     "--env", "TMPDIR=/scratch/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1", "--workdir", "/source"]
        for mount in mounts:
            args += ["--mount", mount]
        args += [image, *command]
        self.containers.append(name)  # Custody before creation, including CLI failure.
        self.docker(*args)
        row, = json.loads(self.docker("container", "inspect", name))
        host = row["HostConfig"]
        require(host["NetworkMode"] == "none" and host["Memory"] == CAPS["container_memory_bytes"] and
                host["MemorySwap"] == CAPS["container_memory_bytes"] and host["PidsLimit"] == CAPS["container_pids"] and
                not host["Privileged"], "container bounds differ")
        if user is not None:
            require(host["ReadonlyRootfs"] and host["CapDrop"] == ["ALL"] and row["Config"]["User"] == user,
                    "test container isolation differs")
        return name

    def start(self, name):
        # Poll named container sizes while attached execution progresses. A CLI
        # process timeout is followed by named Docker cleanup in finally.
        output_path = self.root / (name + ".log")
        with output_path.open("wb") as output:
            process = subprocess.Popen(["docker", "start", "--attach", name], env=self.env, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while process.poll() is None:
                    require(time.monotonic() < self.deadline, "native operations deadline exceeded")
                    require(output_path.stat().st_size <= CAPS["command_output_bytes"], "container output cap exceeded")
                    self.measure_docker()
                    time.sleep(0.5)
                require(process.wait() == 0, "container attached command failed")
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        row, = json.loads(self.docker("container", "inspect", name))
        require(row["State"]["Status"] == "exited" and row["State"]["ExitCode"] == 0 and
                not row["State"]["OOMKilled"], "native container failed or exceeded memory cap")
        self.measure_docker()

    def cleanup(self):
        # Cleanup gets its own short deadline even after the candidate expires.
        self.deadline = min(time.monotonic() + 90, self.native_deadline or float("inf"))
        for name in reversed(self.containers):
            try:
                self.docker("container", "rm", "--force", name, timeout=15, account=False)
                require(not self.docker("container", "ls", "--all", "--filter", "name=^/" + name + "$",
                                        "--format", "{{.Names}}", timeout=15, account=False).strip(), "container removal unconfirmed")
            except Exception:
                self.cleanup_errors.append("container:" + name)
        for mount in reversed(self.mounts):
            try:
                observed = self.run(["findmnt", "--json", "--mountpoint", str(mount)], timeout=15, tolerate=True, account=False)
                if observed.strip():
                    self.run(["sudo", "-n", "umount", str(mount)], timeout=15, account=False)
                require(not self.run(["findmnt", "--json", "--mountpoint", str(mount)], timeout=15,
                                     tolerate=True, account=False).strip(), "mount cleanup remains uncertain")
            except Exception:
                self.cleanup_errors.append("mount:" + mount.name)
        try:
            # Reconcile by fresh owned backing-file identity even if the attach
            # command timed out after creating a device but before reporting it.
            attached = (json.loads(self.run(["sudo", "-n", "losetup", "--json", "--list", "--output", "NAME,BACK-FILE"],
                                           timeout=15, account=False)).get("loopdevices", []) if self.loop_backings else [])
            for row in attached:
                if row.get("back-file") in {str(path) for path in self.loop_backings}:
                    require(re.fullmatch(r"/dev/loop[0-9]+", row["name"]), "unexpected reconciled loop device")
                    if row["name"] not in self.loops:
                        self.loops.append(row["name"])
        except Exception:
            self.cleanup_errors.append("loop-custody-reconciliation")
        for loop in reversed(self.loops):
            try:
                self.run(["sudo", "-n", "losetup", "--detach", loop], timeout=15, account=False)
            except Exception:
                self.cleanup_errors.append("loop:" + loop)
        if self.loop_backings:
            try:
                attached = json.loads(self.run(["sudo", "-n", "losetup", "--json", "--list", "--output", "NAME,BACK-FILE"],
                                              timeout=15, account=False)).get("loopdevices", [])
                require(not any(row.get("back-file") in {str(path) for path in self.loop_backings} for row in attached),
                        "owned loop remains attached")
            except Exception:
                self.cleanup_errors.append("loop-removal-unconfirmed")
        for image in reversed(self.images):
            try:
                self.docker("image", "rm", image, timeout=15, account=False)
                require(not self.docker("image", "ls", "--filter", "reference=" + image, "--format", "{{.ID}}",
                                        timeout=15, account=False).strip(), "image removal unconfirmed")
            except Exception:
                self.cleanup_errors.append("image:" + image)
        return {"confirmed": not self.cleanup_errors, "errors": self.cleanup_errors,
                "scope": "only named candidate containers, images, loop mounts and devices; no shared prune"}


def fetch_inputs(inputs, directory):
    # This is a dedicated owned process, so these documented parser bounds do
    # not alter other HTTP consumers. Status plus at most 31 header lines fit
    # within the 64 KiB reservation, including the request's short headers.
    http.client._MAXLINE = 2000
    http.client._MAXHEADERS = 30
    rows = [inputs["base"], *inputs["packages"]]
    def fetch(row):
        request = urllib.request.Request(row["url"], headers={"Accept-Encoding": "identity", "User-Agent": "ZPM-musl-primitive/1"})
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=30) as response:
            require(response.status == 200 and response.geturl() == row["url"] and
                    len(str(response.headers).encode()) <= CAPS["http_metadata_reservation_per_request"], "unexpected input response")
            data = response.read(row["size"] + 1)
        require(len(data) == row["size"] and digest(data) == row["sha256"], "acquired bytes differ")
        (directory / row["filename"]).write_bytes(data)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, rows))


def installed_inventory(path):
    result = {}
    for stanza in path.read_text().strip().split("\n\n"):
        fields = {}
        for line in stanza.splitlines():
            if line.startswith(("P:", "V:", "C:")):
                key, value = line.split(":", 1)
                require(key not in fields, "duplicate installed package identity")
                fields[key] = value
        require(set(fields) == {"P", "V", "C"} and fields["P"] not in result, "invalid installed package identity")
        result[fields["P"]] = {"version": fields["V"], "control_checksum": fields["C"]}
    return result


def read_native_result(path, filesystem, revision):
    require(path.is_file() and not path.is_symlink(), "native result is absent")
    require(path.stat().st_size <= 128 * 1024, "native result exceeds safe report cap")
    result = json.loads(path.read_text())
    require(isinstance(result, dict) and result.get("filesystem") == filesystem and
            result.get("source_revision") == revision, "native result filesystem/source differs")
    return result


def inside(report_path, filesystem, revision):
    # This executes inside the genuine distro interpreter, after the host has
    # authenticated all bytes. No libc masking is used for the initial witness.
    inputs = load_inputs(REPOSITORY / "tests/contracts/musl-preparation-inputs.json")
    libc = ctypes.CDLL(None, use_errno=True)
    require(sys.platform == "linux" and platform.machine() == "x86_64" and os.getuid() != 0 and
            ctypes.sizeof(ctypes.c_void_p) == ctypes.sizeof(ctypes.c_long) == 8, "native LP64/user witness failed")
    require(not hasattr(libc, "renameat2") and hasattr(libc, "syscall"), "natural missing-wrapper witness failed")
    require(platform.python_version() == inputs["runtime"]["python"], "distro Python version differs")
    require(Path("/etc/alpine-release").read_text().strip() == "3.22.8", "Alpine release differs")
    require(digest(Path("/lib/ld-musl-x86_64.so.1").read_bytes()) == inputs["base"]["libc_sha256"], "native libc bytes differ")
    require(installed_inventory(Path("/lib/apk/db/installed")) == inputs["expected_installed"], "installed closure differs")
    mounts = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
    actual = [row[row.index("-") + 1] for row in mounts if row[4] == "/scratch"]
    require(actual == [filesystem], "actual mounted filesystem differs")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    require(int(status["CapEff"].strip(), 16) == 0 and status["NoNewPrivs"].strip() == "1", "native process privilege differs")
    sys.path.insert(0, str(REPOSITORY))
    tests = unittest.defaultTestLoader.loadTestsFromName("tests.test_preparation_recovery")
    rows = []
    class Results(unittest.TestResult):
        def startTest(self, test):
            super().startTest(test)
            self.begin = time.monotonic()
        def stopTest(self, test):
            rows.append({"test": test.id(), "seconds": round(time.monotonic() - self.begin, 4)})
            super().stopTest(test)
    result = Results()
    tests.run(result)
    report = {"status": "passed" if result.wasSuccessful() and not result.skipped else "failed",
              "filesystem": filesystem, "source_revision": revision, "python": sys.version,
              "python_sha256": digest(Path(sys.executable).resolve().read_bytes()),
              "libc_sha256": inputs["base"]["libc_sha256"], "musl_package": inputs["runtime"]["musl"],
              "kernel": platform.release(), "machine": platform.machine(), "uid": os.getuid(),
              "pointer_bytes": ctypes.sizeof(ctypes.c_void_p), "long_bytes": ctypes.sizeof(ctypes.c_long),
              "natural_renameat2_wrapper": False, "natural_syscall": True, "effective_capabilities": 0,
              "no_new_privileges": True, "network_mode": "none (container loopback remains available)",
              "interfaces": sorted(path.name for path in Path("/sys/class/net").iterdir()),
              "installed_packages": len(inputs["expected_installed"]), "tests_run": result.testsRun,
              "native_recovery_tests": sum(".PreparationRecoveryTests." in row["test"] for row in rows),
              "forced_syscall_recovery_tests": sum(".SyscallPreparationRecoveryTests." in row["test"] for row in rows),
              "binding_refusal_tests": sum(".AtomicRenameBindingTests." in row["test"] for row in rows),
              "skips": len(result.skipped), "failures": [test.id() for test, _ in result.failures],
              "errors": [test.id() for test, _ in result.errors], "tests": rows,
              "fixture_scope": "native host-kernel primitive/recovery, separate forced-syscall and binding-mock cases; repository fixtures, no service installation or browser guest"}
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    require(result.testsRun >= 23 and report["status"] == "passed", "preparation recovery suite failed")


def run_trial(inputs_path, root):
    inputs = load_inputs(inputs_path)
    require(not root.exists(), "work directory must be absent")
    require(sys.platform == "linux" and platform.machine() == "x86_64" and os.getuid() != 0, "host must be nonroot Linux x86_64")
    root.mkdir(mode=0o700, parents=True)
    trial = Trial(root, inputs)
    report = {"schema": "zpm-native-musl-preparation-result-v1", "status": "failed",
              "inputs_sha256": digest(inputs_path.read_bytes()), "caps": CAPS, "filesystems": []}
    failure = None
    try:
        report["host_tools"] = {"python": sys.version, "openssl": trial.run(["openssl", "version"]).strip(),
                                "git": trial.run(["git", "--version"]).strip(),
                                "docker": trial.docker("version", "--format", "{{json .}}")}
        report["acquisition"] = trial.acquire()
        # Reserve the final 90 seconds of the 15-minute native phase for cleanup.
        native_started = time.monotonic()
        trial.native_deadline = native_started + CAPS["operations_seconds"]
        trial.deadline = trial.native_deadline - 90
        source, report["source"] = trial.snapshot()
        require((source / "tests/contracts/musl-preparation-inputs.json").read_bytes() == inputs_path.read_bytes(),
                "selected manifest differs from exact source snapshot")
        base_tag = trial.prefix + ":base"
        trial.images.append(base_tag)  # Register custody before daemon mutation.
        base = trial.docker("image", "import", str(root / "inputs" / inputs["base"]["filename"]), base_tag).strip()
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", base), "import did not return an image ID")
        trial.image_ids[base_tag] = base
        trial.measure_docker()
        builder = trial.prefix + "-apk"
        trial.create_container(builder, base, ["/sbin/apk", "add", "--no-network", "--no-cache", "--repositories-file",
                               "/dev/null", *["/inputs/" + row["filename"] for row in inputs["packages"]]],
                               [f"type=bind,source={root / 'inputs'},target=/inputs,readonly"])
        trial.start(builder)
        native_tag = trial.prefix + ":native"
        trial.images.append(native_tag)
        image = trial.docker("container", "commit", builder, native_tag).strip()
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", image), "commit did not return an image ID")
        trial.image_ids[native_tag] = image
        report["images"] = {"base": base, "native_fixture": image}
        trial.measure_docker()
        for filesystem in ("ext2", "ext4"):
            native = None
            image_file = root / (filesystem + ".img")
            with image_file.open("xb") as stream:
                stream.truncate(CAPS["filesystem_bytes"])
            trial.loop_backings.append(image_file)
            trial.run(["mkfs." + filesystem, "-q", "-F", str(image_file)])
            mount = root / (filesystem + "-mount")
            mount.mkdir()
            loop = trial.run(["sudo", "-n", "losetup", "--find", "--show", str(image_file)]).strip()
            require(re.fullmatch(r"/dev/loop[0-9]+", loop), "unexpected owned loop device")
            trial.loops.append(loop)
            trial.mounts.append(mount)
            trial.run(["sudo", "-n", "mount", "-t", filesystem, "-o", "nodev,nosuid", loop, str(mount)])
            trial.run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(mount)])
            (mount / "home").mkdir()
            (mount / "tmp").mkdir()
            name = trial.prefix + "-" + filesystem
            trial.create_container(name, image, ["python3", "-B", "/source/tests/contracts/check_musl_preparation.py",
                    "--inside", "/scratch/report.json", "--filesystem", filesystem, "--source-revision", report["source"]["revision"]],
                    [f"type=bind,source={source},target=/source,readonly", f"type=bind,source={mount},target=/scratch"],
                    user=f"{os.getuid()}:{os.getgid()}")
            try:
                trial.start(name)
            finally:
                native = read_native_result(mount / "report.json", filesystem, report["source"]["revision"])
                report["filesystems"].append(native)
            require(native is not None and native.get("status") == "passed", "native result failed")
        require([row["filesystem"] for row in report["filesystems"]] == ["ext2", "ext4"], "native filesystem inventory incomplete")
        report["status"] = "passed"
    except Exception as error:
        failure = error
        report["failure"] = {"kind": type(error).__name__, "reason": str(error)[:500]}
    finally:
        report["cleanup"] = trial.cleanup()
        if not report["cleanup"]["confirmed"]:
            report["status"] = "failed"
        report["resources"] = {"seconds": round(time.monotonic() - trial.started, 3),
            "peak_observed_owned_bytes": trial.peak_owned_bytes,
            "storage_scope": "work-file logical bytes plus conservative sum of named image Size and container SizeRw; shared layers may count twice; excludes daemon physical storage and metadata",
            "sampling": "work files at most 0.25s between command polls; named Docker sizes at most each 0.5s plus inspect latency during attached execution; sampling may overshoot, not a storage quota",
            "container_memory": "hard 512 MiB memory and swap limit; 128 PIDs; no privileged containers"}
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    require(failure is None and report["status"] == "passed", "native primitive qualification failed; see safe report.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=REPOSITORY / "tests/contracts/musl-preparation-inputs.json")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--inside", type=Path)
    parser.add_argument("--filesystem", choices=("ext2", "ext4"))
    parser.add_argument("--source-revision")
    parser.add_argument("--fetch", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.fetch:
        require(args.destination is not None, "input destination missing")
        fetch_inputs(load_inputs(args.fetch), args.destination)
    elif args.inside:
        require(args.filesystem and args.source_revision, "native invocation incomplete")
        try:
            inside(args.inside, args.filesystem, args.source_revision)
        except Exception as error:
            if not args.inside.exists():
                args.inside.write_text(json.dumps({"status": "failed", "filesystem": args.filesystem,
                    "source_revision": args.source_revision, "phase": "native-witness", "error_type": type(error).__name__,
                    "reason": str(error) if isinstance(error, Refused) else "unexpected native witness error"}, indent=2) + "\n")
            raise
    else:
        require(args.work_dir is not None, "--work-dir is required")
        run_trial(args.inputs.resolve(), args.work_dir.resolve())


if __name__ == "__main__":
    main()
