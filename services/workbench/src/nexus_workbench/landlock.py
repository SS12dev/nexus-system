"""Landlock: lets a process lock itself (and everything it starts) out of the filesystem
except for the paths it names, with no privileges needed. Every command the workbench runs is
locked to its own project folder plus read-only system folders, so one project can't read or
change another's, or the service's own code.

Landlock is in Linux 5.13+ and Docker Desktop's kernel. Where it is missing (or a seccomp
profile blocks it) commands still run, isolated only by the container; /healthz says which.
"""

import ctypes
import os

# Syscall numbers are the same on x86_64 and arm64 (new syscalls share one table).
SYS_CREATE_RULESET = 444
SYS_ADD_RULE = 445
SYS_RESTRICT_SELF = 446
CREATE_RULESET_VERSION = 1
RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38

EXECUTE = 1 << 0
WRITE_FILE = 1 << 1
READ_FILE = 1 << 2
READ_DIR = 1 << 3
TRUNCATE = 1 << 14
# ABI 1 covers bits 0-12 (remove, make and so on); 2 adds REFER (13), 3 adds TRUNCATE (14).
ABI_BITS = {1: 13, 2: 14, 3: 15}
READ_ONLY = EXECUTE | READ_FILE | READ_DIR
# A rule on a file (such as /dev/null) may only grant rights that apply to files.
FILE_RIGHTS = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE

_libc = ctypes.CDLL(None, use_errno=True)


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def abi_version() -> int:
    """0 when Landlock isn't usable here."""
    v = _libc.syscall(SYS_CREATE_RULESET, None, ctypes.c_size_t(0), CREATE_RULESET_VERSION)
    return max(v, 0)


def restrict(read_write: list[str], read_only: list[str]) -> None:
    """Lock the calling process to these paths. Raises OSError if Landlock refuses."""
    abi = min(abi_version(), max(ABI_BITS))
    if abi < 1:
        raise OSError("Landlock isn't available")
    handled = (1 << ABI_BITS[abi]) - 1
    attr = _RulesetAttr(handled)
    fd = _libc.syscall(SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")
    try:
        for paths, access in ((read_write, handled), (read_only, READ_ONLY)):
            for path in paths:
                try:
                    pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
                except FileNotFoundError:
                    continue
                try:
                    rights = access if os.path.isdir(path) else access & FILE_RIGHTS
                    rule = _PathBeneath(rights, pfd)
                    if _libc.syscall(SYS_ADD_RULE, fd, RULE_PATH_BENEATH, ctypes.byref(rule), 0):
                        raise OSError(ctypes.get_errno(), f"landlock_add_rule {path}")
                finally:
                    os.close(pfd)
        if _libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0):
            raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS)")
        if _libc.syscall(SYS_RESTRICT_SELF, fd, 0):
            raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    finally:
        os.close(fd)
