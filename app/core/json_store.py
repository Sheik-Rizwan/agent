"""Tiny JSON-file storage engine used by every repository.

Guarantees:
- Atomic writes: data is written to a temp file, fsynced, then renamed over the
  original, so a crash never leaves a half-written file.
- Serialized updates: `update()` holds a per-file lock (threads) and an OS file
  lock (processes, where supported) for the whole read-modify-write.
- Private files: created with mode 0600 inside a 0700 directory, because they
  hold password hashes and session data.
- Corruption recovery: an unreadable file is moved aside to `<name>.corrupt-<ts>`
  and replaced with the default, and the problem is logged.
"""

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl  # POSIX only
except ImportError:  # pragma: no cover - Windows
    fcntl = None

logger = logging.getLogger(__name__)

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:  # pragma: no cover - e.g. filesystems without permissions
        pass


class JsonFile:
    def __init__(self, path: Path, default: Callable[[], Any]):
        self.path = path
        self.default = default

    @contextmanager
    def _locked(self):
        ensure_private_dir(self.path.parent)
        with _thread_lock(self.path):
            if fcntl is None:
                yield
                return
            lock_dir = self.path.parent / ".locks"
            lock_dir.mkdir(mode=0o700, exist_ok=True)
            lock_path = lock_dir / f"{self.path.name}.lock"
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _load(self) -> Any:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return self.default()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            backup = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
            os.replace(self.path, backup)
            logger.error("Corrupt JSON in %s (%s); moved it to %s and started fresh", self.path, exc, backup)
            return self.default()

    def _save(self, data: Any) -> None:
        tmp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def read(self) -> Any:
        with self._locked():
            return self._load()

    @contextmanager
    def update(self, create: bool = True):
        """Read, let the caller mutate the data in place, then save atomically.
        If the block raises, nothing is written. With create=False and no file on
        disk, yields None and writes nothing (so a deleted record stays deleted)."""
        with self._locked():
            if not create and not self.path.exists():
                yield None
                return
            data = self._load()
            yield data
            self._save(data)

    def write(self, data: Any) -> None:
        with self._locked():
            self._save(data)

    def delete(self) -> bool:
        with self._locked():
            try:
                self.path.unlink()
                return True
            except FileNotFoundError:
                return False
