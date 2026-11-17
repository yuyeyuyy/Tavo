"""No task code in PID 1. Docker init reaps orphaned children; manager removes stragglers."""

import signal
import sys
import time

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    time.sleep(3600)
