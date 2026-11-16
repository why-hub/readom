#!/usr/bin/env python3
"""Patch Amazon healthd's power_supply type map so UPS is NOT treated as AC.

Why: this fork maps charger type names to Android's BATTERY_PLUGGED_* bits:

    0xa8dc  "Battery"     -> 4
    0xa8e4  "UPS"         -> 1     <-- same bit as Mains == AC
    0xa8ec  "Mains"       -> 1
    0xa8f4  "USB"         -> 2

`drivers/power/soda.c` exposes `soda_boost` (the battery boost converter that
powers the rails when no cable is present) as POWER_SUPPLY_TYPE_UPS, and its
`online` is `soda_soft_boost_sts`, which is 1 exactly when we run on battery.
Because healthd folds UPS into the AC bit, BatteryService reported
"AC powered: true" forever and the status bar kept the charging icon with the
cable pulled.  Demoting the node to UPS (v14) changed nothing for that reason.

Patch: set the UPS entry's bit from 1 to 0, i.e. "this supply is not a
charger".  Mains keeps working, so the real detector (soda_chg) still drives
the icon.

Target: /bin/healthd file offset 0xa8e8 (the 4-byte int following the
"UPS" string pointer), expect 01 00 00 00, write 00 00 00 00.
"""

import argparse
import hashlib
import pathlib
import re
import struct
import subprocess
import sys

DEFAULT_IMAGE = "/mnt/g/android/system.img"
HEALTHD = "/bin/healthd"
FILE_OFF = 0xA8E8
EXPECT = b"\x01\x00\x00\x00"
PATCH = b"\x00\x00\x00\x00"


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True)


def block_size(image):
    out = sh("dumpe2fs", "-h", str(image)).stdout
    m = re.search(r"^Block size:\s+(\d+)", out, re.M)
    if not m:
        sys.exit("ABORT: cannot read block size from %s" % image)
    return int(m.group(1))


def file_blocks(image, path):
    """Return (list of physical block numbers, size) for `path`."""
    out = sh("debugfs", "-R", "stat %s" % path, str(image)).stdout
    if "Inode:" not in out:
        sys.exit("ABORT: cannot stat %s in %s" % (path, image))
    size = int(re.search(r"Size:\s+(\d+)", out).group(1))

    blocks = []
    m = re.search(r"EXTENTS:\s*\n((?:\(\d+-\d+\):\d+-\d+\s*\n?)+)", out)
    if m:
        for seg in re.finditer(r"\((\d+)-(\d+)\):(\d+)-(\d+)", m.group(1)):
            lo, hi, p0, p1 = (int(x) for x in seg.groups())
            if hi - lo != p1 - p0:
                sys.exit("ABORT: unexpected extent shape")
            blocks.extend(range(p0, p0 + (hi - lo) + 1))
    else:
        m = re.search(r"BLOCKS:\s*\n((?:\([^)]*\)\s*)+)", out)
        if not m:
            sys.exit("ABORT: no extents and no block list for %s" % path)
        for blk in re.findall(r"\((\d+)\)", m.group(1)):
            blocks.append(int(blk))
        m2 = re.search(r"BLOCKS:((?:\s*\([^)]*\))+)", out)
        if m2:
            seq = [int(b) for b in re.findall(r"\((\d+)\)", m2.group(1))]
            if seq:
                blocks = seq
    return blocks, size


def phys_offset(blocks, bs, off):
    idx = off // bs
    if idx >= len(blocks):
        sys.exit("ABORT: file offset 0x%x beyond last block" % off)
    return blocks[idx] * bs + (off % bs)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()

    image = pathlib.Path(a.image)
    if not image.exists():
        sys.exit("ABORT: %s not visible (switch back to the stock system)" % image)

    bs = block_size(image)
    blocks, size = file_blocks(image, HEALTHD)
    poff = phys_offset(blocks, bs, FILE_OFF)
    print("image      : %s" % image)
    print("block size : %d" % bs)
    print("%s: %d bytes, %d blocks" % (HEALTHD, size, len(blocks)))
    print("file off   : 0x%x -> physical 0x%x" % (FILE_OFF, poff))

    data = image.read_bytes()[poff:poff + 4]
    if data == EXPECT:
        state = "UNPATCHED (UPS -> AC bit 1)"
    elif data == PATCH:
        state = "PATCHED (UPS -> 0, ignored by healthd)"
    else:
        sys.exit("ABORT: unexpected bytes at target: %s (image changed?)" % data.hex())

    print("current    : %s  %s" % (data.hex(), state))
    print("before sha : %s" % sha256(image))

    if a.check:
        return

    want = EXPECT if a.restore else PATCH
    if data == want:
        print("RESULT: nothing to do")
        return

    with open(image, "r+b") as f:
        f.seek(poff)
        f.write(want)
        f.flush()

    with open(image, "rb") as f:
        f.seek(poff)
        after = f.read(4)
    if after != want:
        sys.exit("ABORT: write did not stick, read back %s" % after.hex())

    print("after      : %s" % after.hex())
    print("after sha  : %s" % sha256(image))
    print("RESULT: %s" % ("RESTORED" if a.restore else "PATCHED"))


if __name__ == "__main__":
    main()
