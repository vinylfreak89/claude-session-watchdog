"""Serialize complete read/modify/write transactions across supported state writers."""
from contextlib import contextmanager
import fcntl
import os

@contextmanager
def transaction(directory):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, '.writer.lock'), 'a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
