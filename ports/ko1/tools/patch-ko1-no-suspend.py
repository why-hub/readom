#!/usr/bin/env python3
"""KO1 v27: refuse system suspend outright, keep the machine running when locked.

Why
---
v26 taught the watchdog to survive suspend, but the v26 run on hardware showed
that suspend never actually completes on this box:

    PM: Some devices failed to suspend
    wifi_platform_prealloc: failed to alloc static mem section 7
    PM: Some devices failed to suspend
    mmc3: error -110 during resume (card was removed?)

...and afterwards WiFi is dead for good:

    wlan0: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN
    Wi-Fi is unknown state

The sequence is: screen off -> PowerManagerService -> libsuspend
("Selected wakeup count", CONFIG_PM_AUTOSLEEP is off) -> write mem to
/sys/power/state -> enter_state() -> freeze userspace -> device suspend ->
bcmdhd tears the SDIO card down -> resume of mmc3 times out (-110) -> PM
aborts the whole thing and rolls back.  Net effect per lock: one aborted
suspend, a wrecked WiFi chip, and (before v26) a 60 s watchdog reset.

A partial suspend is worse than no suspend at all.  Until the SDIO host and
bcmdhd learn to keep the card powered across suspend, the honest thing is to
never enter it.

What this patch does
--------------------
Refuse at the single entry point -- kernel/power/suspend.c:enter_state() --
before anything is synced, frozen or powered down:

    if (!valid_state(state))
            return -ENODEV;
+   printk_ratelimited(KERN_ERR "KO1NOSUSP: ...");
+   return -EBUSY;

Nothing else runs: no sys_sync(), no freezer, no device suspend, no WCR.WDE
games, no WiFi teardown.  The screen still turns off (that is EPDC and the
backlight, driven from Android), the SoC keeps running, the watchdog keeps
being fed by its 15 s timer, and WiFi stays associated.

Cost: standby current is no longer tens of microamps; the SoC idles instead of
sleeping.  v26's watchdog suspend hook is kept as a belt-and-braces measure
(it simply stops being reachable).

Usage
-----
    patch-ko1-no-suspend.py            # apply (idempotent)
    patch-ko1-no-suspend.py --check
    patch-ko1-no-suspend.py --restore
"""
from __future__ import annotations

import argparse
from pathlib import Path

SRC = Path("/home/builder/pw3-build/linux-android-core")
SUSPEND_C = SRC / "kernel/power/suspend.c"

ANCHOR = """\tif (!valid_state(state))
\t\treturn -ENODEV;
"""

PATCH = '''\tif (!valid_state(state))
\t\treturn -ENODEV;

\t/* ===================== KO1 v27: no system suspend ==================== *
\t * A suspend on this box always dies half way: bcmdhd pulls the SDIO card
\t * (mmc3) down during device suspend and the host controller times out on
\t * resume (-110), so the PM core aborts and rolls back -- leaving the WiFi
\t * chip dead until the next boot.  Before v26 the frozen watchdog timer
\t * then reset the SoC 60 s later.
\t *
\t * Refusing here means: screen off only.  No freezer, no device suspend,
\t * no WiFi teardown, watchdog keeps being fed.  Revisit once mmc3/bcmdhd
\t * can keep the card powered across suspend.
\t * ==================================================================== */
\tprintk_ratelimited(KERN_ERR
\t\t"KO1NOSUSP: refusing system suspend (state=%d)\\n", state);
\treturn -EBUSY;
'''

MARK = "KO1 v27: no system suspend"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    text = SUSPEND_C.read_text()
    has = MARK in text

    if args.check:
        print("enter_state patch:", "applied" if has else "absent")
        return 0 if has else 1

    if args.restore:
        if not has:
            print("nothing to restore")
            return 0
        i = text.find("\n\t/* ===================== " + MARK)
        j = text.find("return -EBUSY;\n", i)
        if i < 0 or j < 0:
            print("cannot locate patch block")
            return 1
        out = text[:i] + "\n" + text[j + len("return -EBUSY;\n"):]
        SUSPEND_C.write_text(out)
        print("restored enter_state()")
        return 0

    if has:
        print("already applied")
        return 0

    if ANCHOR not in text:
        print("anchor not found -- source moved?")
        return 1

    text = text.replace(ANCHOR, PATCH, 1)
    SUSPEND_C.write_text(text)
    print("patched kernel/power/suspend.c: enter_state() refuses suspend")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
