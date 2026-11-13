"""Private Linux subprocess guardian for sealed bootstrap, not a public CLI.

An inherited manager lock stays held while the package process or adopted
descendants exist. EOF from the manager also cancels the owned process group.
"""

import ctypes
import os
import select
import signal
import subprocess
import sys
import time


def main():
    control = int(sys.argv[1])
    lock = int(sys.argv[2])
    if lock >= 0:
        os.fstat(lock)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER; no extra privilege.
        return 120
    child = subprocess.Popen(sys.argv[3:], start_new_session=True, close_fds=True)
    cancelled = False
    while child.poll() is None:
        readable, _, _ = select.select([control], [], [], 0.1)
        if readable and not os.read(control, 1):
            cancelled = True
            break
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    # Keep the lock until wait confirms the direct process and all adopted
    # children are gone. Kernel-level uninterruptible work retains the lock;
    # the manager reports unconfirmed cleanup instead of allowing a new writer.
    child.wait()
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == 0:
            time.sleep(0.02)
    return 121 if cancelled else (child.returncode if 0 <= child.returncode < 120 else 122)


if __name__ == "__main__":
    raise SystemExit(main())
