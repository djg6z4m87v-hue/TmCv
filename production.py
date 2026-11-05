"""Supervise web + one application worker; stop both cleanly on container shutdown."""
import os
import signal
import subprocess
import sys
import time


def main():
    children = []
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
        for child in children:
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    children.append(subprocess.Popen([sys.executable, '-m', 'gunicorn', 'app:app', '--bind', '0.0.0.0:' + os.getenv('PORT', '8080'), '--workers', '2', '--timeout', '120']))
    children.append(subprocess.Popen([sys.executable, 'application_worker.py']))
    try:
        while not stopping and all(p.poll() is None for p in children):
            time.sleep(1)
    finally:
        stop()
        for child in children:
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return 0 if all(p.returncode in (0, -signal.SIGTERM) for p in children) else 1


if __name__ == '__main__':
    sys.exit(main())
