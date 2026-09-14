"""Keep Windows from sleeping while the bot is up -- and only while it is up.

The host sleeps after 45 idle minutes, and nothing in the bot asked it not to.
An idle PC slept through 2026-09-14 from 08:33 to 20:06, and a sleep during a
session freezes APScheduler just as silently as the console wedge did.

`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` is a request
attached to the calling THREAD, and Windows withdraws it when that thread or the
process ends -- including on a crash or a kill. So there is no sleep setting to
change and nothing to restore: when the bot exits after its scans, the machine's
own schedule simply applies again. That is why this is preferred over toggling
`powercfg`, which a killed process would leave disabled.

Call it from a thread that lives as long as the process (the main thread, before
`bot.run`). It does not request the display (ES_DISPLAY_REQUIRED): the monitor
may still turn off.
"""
from __future__ import annotations

import ctypes
import logging

logger = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

_WINDOWS = object()


def _kernel32():
    windll = getattr(ctypes, "windll", None)  # absent off Windows (CI is Linux)
    if windll is None:
        return None
    k = windll.kernel32
    # EXECUTION_STATE is a DWORD; without these, 0x80000000 overflows a C int.
    k.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
    k.SetThreadExecutionState.restype = ctypes.c_uint32
    return k


def hold_system_awake(kernel32=_WINDOWS) -> bool:
    """Ask Windows not to idle-sleep while this process runs. True if granted.

    Never raises: failing to hold the machine awake is a reason to log, not a
    reason to refuse to run the session.
    """
    if kernel32 is _WINDOWS:
        kernel32 = _kernel32()
    if kernel32 is None:
        return False
    try:
        previous = kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except Exception as exc:
        logger.warning("Could not ask Windows to stay awake: %s", exc)
        return False
    return bool(previous)  # 0 means the call failed
