"""Nonblocking, process-wide publication locks for release directories."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def publication_lock(directory: Path):
    """Hold one stable lock inode; kernel locks release even after process exit.

    The lock file intentionally remains in place. Unlinking it after unlocking
    could let two processes lock different inodes for the same directory.
    """
    directory = Path(directory)
    lock_path = directory / ".publication.lock"
    if lock_path.resolve().parent != directory.resolve():
        raise ValueError("Publication lock escapes the intended directory")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError(f"Another publisher is already using {directory}") from error
        acquired = True
        yield
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
