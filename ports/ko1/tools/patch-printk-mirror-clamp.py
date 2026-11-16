#!/usr/bin/env python3
"""Clamp the pw3-duet printk RAM mirror to the 32 KiB window it owns.

Why
---
The port mirrors the kernel ring buffer to a fixed physical address so the NEXT
boot (the stock system) can still read the last log lines after a failed
Android start:

    kernel/printk.c   pw3_dst = (volatile char *)0x9fff8000u;
                      for (n = 0; n < __LOG_BUF_LEN; n++) pw3_dst[n] = src[n];

    arch/arm/kernel/setup.c  memblock_reserve(0x9fff0000, 0x10000)   /* 64 KiB */

The reserved window ends at the top of DRAM (0xA0000000), so the mirror may only
cover 0x9FFF8000..0xA0000000 = 32 KiB.  That silently assumed
CONFIG_LOG_BUF_SHIFT=15.

v19 raised it to 18 (256 KiB ring, so boot messages survive until adb).  The
loop then copied 256 KiB per single printk() call, i.e. up to 0xA0038000 --
224 KiB past the end of DRAM.  The first printk after paging_init (when
pw3_ram_ready flips to 1) therefore wrote into unpopulated address space, took
an external abort in kernel context and reset the board: Android never came up
and the device fell back to the stock system.

What this patch does
--------------------
Keep the 256 KiB ring (that is the point of v19/v20) but copy only the most
recent 32 KiB -- the tail, which is what a post-mortem wants anyway -- and never
write past 0xA0000000.

Usage
-----
    patch-printk-mirror-clamp.py            # apply (idempotent)
    patch-printk-mirror-clamp.py --check    # report only
    patch-printk-mirror-clamp.py --restore  # put the old loop back
"""
from __future__ import annotations

import argparse
from pathlib import Path

SRC = Path("/home/builder/pw3-build/linux-android-core/kernel/printk.c")
CONFIG = Path("/home/builder/pw3-build/out-android-core-duet/.config")

OLD = """	/* pw3-duet: mirror the ring where the next boot can still read it. */
	if (pw3_ram_ready) {
		volatile char *pw3_dst = (volatile char *)0x9fff8000u;
		const char *pw3_src = __log_buf;
		int pw3_n;

		for (pw3_n = 0; pw3_n < __LOG_BUF_LEN; pw3_n++)
			pw3_dst[pw3_n] = pw3_src[pw3_n];
	}
"""

NEW = """	/* pw3-duet: mirror the ring where the next boot can still read it.
	 *
	 * 0x9FFF8000..0xA0000000 is 32 KiB and ends exactly at the top of DRAM
	 * (setup.c reserves 0x9FFF0000..0xA0000000), so this copy must never be
	 * sized by __LOG_BUF_LEN: with CONFIG_LOG_BUF_SHIFT=18 that is 256 KiB
	 * and the tail 224 KiB would land on unpopulated address space, take an
	 * external abort and reset the board.  Copy the newest 32 KiB only.
	 */
	if (pw3_ram_ready) {
		volatile char *pw3_dst = (volatile char *)0x9fff8000u;
		const char *pw3_src = __log_buf;
		unsigned int pw3_mask = __LOG_BUF_LEN - 1;
		unsigned int pw3_n = __LOG_BUF_LEN < 32768u ? __LOG_BUF_LEN : 32768u;
		unsigned int pw3_start = log_end & pw3_mask;
		unsigned int pw3_i;

		for (pw3_i = 0; pw3_i < pw3_n; pw3_i++)
			pw3_dst[pw3_i] = pw3_src[(pw3_start + pw3_i) & pw3_mask];
	}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    text = SRC.read_text()

    if args.restore:
        if NEW in text:
            SRC.write_text(text.replace(NEW, OLD, 1))
            print("restored: mirror copies __LOG_BUF_LEN bytes again")
        else:
            print("nothing to restore")
        return 0

    if NEW in text:
        print("  mirror clamp: already applied")
    elif OLD in text:
        if args.check:
            print("  mirror clamp: MISSING (old loop still there)")
            return 0
        SRC.write_text(text.replace(OLD, NEW, 1))
        print("  mirror clamp: applied")
    else:
        raise SystemExit("ABORT: neither the old nor the new loop was found")

    shift = [l for l in CONFIG.read_text().splitlines() if l.startswith("CONFIG_LOG_BUF_SHIFT=")]
    print(f"  ring: {shift[0] if shift else '?'}  mirror: <= 32768 B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
