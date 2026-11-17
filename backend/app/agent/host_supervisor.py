"""Private standard-library launcher. Its control FD is never inherited by Bash.

The whole owned session is killed after foreground exit or parent loss, including
this supervisor. The result FD preserves the foreground exit code before cleanup.
This is process supervision, not an OS sandbox against deliberately escaping code.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time


def main():
    _, _identity, fd, grace, watchdog = sys.argv
    result_fd = int(fd)
    grace, watchdog = float(grace), float(watchdog)
    config = json.loads(sys.stdin.buffer.readline(262145))
    stopped = threading.Event()
    lock = threading.Lock()
    heartbeat = [time.monotonic()]

    def terminate():
        if not lock.acquire(blocking=False):
            return
        os.killpg(os.getpid(), signal.SIGTERM)
        time.sleep(grace)
        os.killpg(os.getpid(), signal.SIGKILL)

    signal.signal(signal.SIGTERM, lambda *_: stopped.set())

    def read_parent():
        try:
            while sys.stdin.buffer.readline(32) == b"alive\n":
                heartbeat[0] = time.monotonic()
        finally:
            stopped.set()

    def watch():
        while not stopped.wait(min(0.1, watchdog / 4)):
            if time.monotonic() - heartbeat[0] > watchdog:
                break
        terminate()

    threading.Thread(target=read_parent, daemon=True).start()
    threading.Thread(target=watch, daemon=True).start()
    try:
        process = subprocess.Popen(
            ["/bin/bash", "--noprofile", "--norc", "-c", config["command"]],
            cwd=config["cwd"],
            env=config["environment"],
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        code = process.wait()
        os.write(result_fd, json.dumps({"exitCode": code}).encode() + b"\n")
    finally:
        os.close(result_fd)
        terminate()
        stopped.wait()  # another termination thread may own cleanup
        time.sleep(grace + 1)


if __name__ == "__main__":
    main()
