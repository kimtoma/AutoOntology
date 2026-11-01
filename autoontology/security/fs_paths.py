# SPDX-License-Identifier: MIT
"""Native filesystem spelling for authority comparisons, without creating paths."""

import fcntl
import os
import stat
import sys
from pathlib import Path


def canonical_path(path, *, single_link=False):
    """Preserve distinct files; never infer physical identity from case folding.

    Path.resolve does not canonicalize case/Unicode aliases on macOS. F_GETPATH
    returns the opened object's stored spelling. Missing tails are appended to
    the nearest existing ancestor, and are deliberately not case-normalized.
    """
    path = Path(path).absolute()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        if path.name in {"", ".", ".."}:
            raise OSError("Ambiguous missing authority path") from None
        return canonical_path(path.parent) / path.name
    try:
        info = os.fstat(fd)
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise OSError("Authority paths must be regular files or directories")
        if single_link and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise OSError("Workspace databases cannot have multiple links")
        if sys.platform == "darwin":
            # Darwin sys/fcntl.h: F_GETPATH=50; sys/param.h: MAXPATHLEN=PATH_MAX.
            raw = fcntl.fcntl(fd, 50, bytes(os.pathconf(path, "PC_PATH_MAX")))
            resolved = Path(os.fsdecode(raw.split(b"\0", 1)[0]))
        else:
            resolved = path.resolve(strict=True)
        expected = (info.st_dev, info.st_ino)
        for target in (path, resolved):
            current = target.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != expected or (
                stat.S_ISREG(info.st_mode) and current.st_nlink != info.st_nlink
            ):
                raise OSError("Authority path changed during resolution")
        return resolved
    finally:
        os.close(fd)
