#!/usr/bin/env python3
"""KO1: give the device its full name instead of the codename abbreviations.

The shipped image still introduces itself with two short names:

    ro.product.model=KO1                          <- what Settings shows as Model
    ro.vendor.product.model=Kindle Paperwhite 3   <- left over from the PW3 build

Neither is acceptable now that WiFi, charging, WeRead and both page keys work:
"KO1" is an internal codename, and the vendor block still claims to be a
Paperwhite.  Both become the name the first Oasis generation is sold and
discussed under:

    Kindle Oasis 1

Why this is a byte patch and still leaves the file the same size
---------------------------------------------------------------
system.img is not repacked.  build.prop is 4719 bytes in two ext4 blocks, and
the heapsize tool (tools/patch-buildprop-heapsize.py) finds its target at a
fixed file offset of 3787.  Appending bytes would move everything after the
model lines, so the growth is paid for inside the file:

    anchor                         old bytes  new bytes  delta
    ro.product.model=...                  20         31    +11
    ro.vendor.product.model=...           43         38     -5
    the two abi/abi2 comment lines        97         91     -6
                                     --------------------------
    file total                          4719       4719      0

The comment pair is reworded to 91 bytes, which keeps
    * the file size at 4719 (no inode change, no new blocks, no repack),
    * dalvik.vm.heapsize= at file offset 3787 (the heapsize tool keeps working),
    * mode 0644 and the SELinux label untouched.
Only preinit's system.img pin has to follow (tools/update-preinit-system-sha.py).

Offsets (debugfs -R 'stat /build.prop' on the shipped image):
    EXTENTS (0-1): 83430-83431        file size 4719
so file byte N lives at image byte N + 83430*4096.

Usage:
    python3 tools/patch-buildprop-model.py --check          # look, do not write
    python3 tools/patch-buildprop-model.py                 # patch G:/android/system.img
    python3 tools/patch-buildprop-model.py --source tmp/build.prop.exact --out /tmp/bp.new
                                                           # offline dry run
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
IMG = pathlib.Path("G:/android/system.img")

FIRST_BLOCK = 83430
BLOCK_SIZE = 4096
FILE_SIZE = 4719
HEAPSIZE_OFFSET = 3787

OLD_PRODUCT = b"ro.product.model=KO1"
NEW_PRODUCT = b"ro.product.model=Kindle Oasis 1"
OLD_VENDOR = b"ro.vendor.product.model=Kindle Paperwhite 3"
NEW_VENDOR = b"ro.vendor.product.model=Kindle Oasis 1"
OLD_ABI = (b"# ro.product.cpu.abi and ro.product.cpu.abi2 are obsolete,\n"
           b"# use ro.product.cpu.abilist instead.\n")
NEW_ABI = (b"# ro.product.cpu.abi and abi2 are obsolete,\n"
           b"# use ro.product.cpu.abilist instead of these.\n")

EDITS = (
    ("product model", OLD_PRODUCT, NEW_PRODUCT),
    ("vendor model", OLD_VENDOR, NEW_VENDOR),
    ("abi comment", OLD_ABI, NEW_ABI),
)

PATCHED_MARK = b"ro.product.model=Kindle Oasis 1"


def sha256(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def transform(data: bytes) -> bytes:
    """Apply the three byte-level edits and prove the file is still the same size."""
    if len(data) != FILE_SIZE:
        sys.exit("ABORT: build.prop is %d bytes, expected %d" % (len(data), FILE_SIZE))
    if PATCHED_MARK in data:
        return data

    out = data
    for label, old, new in EDITS:
        i = out.find(old)
        if i < 0:
            sys.exit("ABORT: %s anchor %r not found in build.prop" % (label, old[:40]))
        out = out[:i] + new + out[i + len(old):]

    if len(out) != FILE_SIZE:
        sys.exit("ABORT: rewrite changed the file size (%d -> %d)" % (FILE_SIZE, len(out)))
    if out.find(b"dalvik.vm.heapsize=") != HEAPSIZE_OFFSET:
        sys.exit("ABORT: heapsize anchor moved off offset %d" % HEAPSIZE_OFFSET)
    return out


def show(data: bytes) -> None:
    for line in data.split(b"\n"):
        if b".model=" in line or b"abi" in line:
            print("   ", line.decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=str(IMG))
    ap.add_argument("--source", help="dry run on a local copy instead of the image")
    ap.add_argument("--out", help="with --source: write the patched copy here")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    if a.source:
        old = pathlib.Path(a.source).read_bytes()
        new = transform(old)
        print("source      :", a.source)
        print("size        :", len(old), "->", len(new))
        show(new)
        if new == old:
            print("already patched")
        if a.out:
            pathlib.Path(a.out).write_bytes(new)
            print("written     :", a.out)
        return

    image = pathlib.Path(a.image)
    if not image.exists():
        sys.exit("ABORT: %s not visible (switch back to the stock system)" % image)

    base = FIRST_BLOCK * BLOCK_SIZE
    with image.open("rb") as f:
        f.seek(base)
        old = f.read(FILE_SIZE)

    if not old.startswith(b"\n# begin build properties") or not old.endswith(b"\n"):
        sys.exit("ABORT: block %d does not hold build.prop -- layout moved" % FIRST_BLOCK)
    if old.find(b"dalvik.vm.heapsize=") != HEAPSIZE_OFFSET:
        sys.exit("ABORT: heapsize anchor is not at %d -- wrong image" % HEAPSIZE_OFFSET)

    print("image       :", image)
    print("sha256      :", sha256(image)[:16])
    print("build.prop  : block %d, %d bytes" % (FIRST_BLOCK, FILE_SIZE))
    print("before      :")
    show(old)

    new = transform(old)
    if new == old:
        print("already patched -- nothing to do")
        return
    print("after       :")
    show(new)
    if a.check:
        print("--check only, image not written")
        return

    with image.open("r+b") as f:
        f.seek(base)
        f.write(new)
    with image.open("rb") as f:
        f.seek(base)
        if f.read(FILE_SIZE) != new:
            sys.exit("ABORT: read-back mismatch")

    print("patched      : yes (read-back identical)")
    print("new sha256   :", sha256(image))


if __name__ == "__main__":
    main()
