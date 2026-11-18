#!/usr/bin/env python3
"""KO1 v29: both EPDC properties inside build.prop's text area.

What the last two attempts proved
---------------------------------
v27 put `debug.pw3.epdc.fast=1` at the end of the file:

    text 4661 B + NUL padding 58 B
    appended property -> after the padding, no trailing newline

On the device `debug.pw3.epdc.full_every` read back 20 (line 78, text area)
while `debug.pw3.epdc.fast` read back empty.  v28 then moved both properties
into the 58-byte tail padding -- and on the device *both* disappeared, while
`ro.product.model` still said "Kindle Oasis 1", i.e. the image really was the
one we wrote.

The tell was the padding itself: the image carries 58 NUL bytes at the end of
build.prop, the device copy has none.  The preinit path rewrites build.prop
and drops everything past the first NUL.  Anything sitting in that padding is
therefore lost, regardless of newlines.

Conclusion: properties go in the text area, and the length has to balance
exactly.  Both substitutions below are the same number of bytes.

    line 69  78 B ->
             "# 12 MB less in /data. Boot address no longer random.   "  (56)
             + "\\n" + "debug.pw3.epdc.fast=1"                              (22)
             = 78 B

    line 74  "# --no-watch-dog: see pw3.mk."                             (29) ->
             "debug.pw3.epdc.full_every=20 "                             (29)

Line 69 is the tail of a long comment about the ART boot image; only the
closing sentence is rewritten and the property rides on the new line that it
frees.  Line 74's note is redundant -- the same flags appear on
`dalvik.vm.dex2oat-flags=` two lines below.

The 58-byte padding at the end is left untouched, so the file stays 4719
bytes and every other offset (including the heapsize one at 3787 that
tools/patch-buildprop-heapsize.py depends on) is unaffected.

Usage
-----
    patch-buildprop-epdc-v29.py --check
    patch-buildprop-epdc-v29.py                 # -> build/images/system-v29.img
    patch-buildprop-epdc-v29.py --verify PATH
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

SRC = pathlib.Path("build/backup/system.img.v25-a2229b9c")
DST = pathlib.Path("build/images/system-v29.img")

BLOCK = 83430
BLOCK_SIZE = 4096
INBLOCK = 58
FILE_SIZE = 4719
OFF = BLOCK * BLOCK_SIZE + INBLOCK

OLD_LINE = b"# 12 MB less in /data. Given up: the boot image's address is no longer random."
NEW_BLOCK = (
    b"# 12 MB less in /data. Boot address no longer random.   "
    b"\ndebug.pw3.epdc.fast=1"
)
FULL_ANCHOR = b"# --no-watch-dog: see pw3.mk."
FULL_PROP = b"debug.pw3.epdc.full_every=20 "

FAST = b"debug.pw3.epdc.fast=1"
FULL = b"debug.pw3.epdc.full_every=20"


def blob_at(img: pathlib.Path) -> bytes:
    with img.open("rb") as f:
        f.seek(OFF)
        return f.read(FILE_SIZE)


def text_len(blob: bytes) -> int:
    end = len(blob)
    while end > 0 and blob[end - 1] == 0:
        end -= 1
    return end


def check_props(blob: bytes) -> dict[str, bool]:
    t = text_len(blob)
    return {
        "size_ok": len(blob) == FILE_SIZE,
        "fast_in_text": FAST in blob[:t],
        "full_in_text": FULL in blob[:t],
        "fast_line_terminated": b"\ndebug.pw3.epdc.fast=1\n" in blob[:t],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--verify", metavar="PATH")
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--dst", default=str(DST))
    args = ap.parse_args()

    assert len(NEW_BLOCK) == len(OLD_LINE), (len(NEW_BLOCK), len(OLD_LINE))
    assert len(FULL_PROP) == len(FULL_ANCHOR), (len(FULL_PROP), len(FULL_ANCHOR))

    if args.verify:
        blob = blob_at(pathlib.Path(args.verify))
        r = check_props(blob)
        t = text_len(blob)
        print(f"file={len(blob)} text={t} padding={len(blob) - t}")
        for k, v in r.items():
            print(f"  {k:22s} {'yes' if v else 'NO'}")
        ok = all(r.values())
        print("RESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    src = pathlib.Path(args.src)
    if not src.exists():
        print(f"no pristine v25 image at {src}")
        return 1

    blob = blob_at(src)
    if len(blob) != FILE_SIZE:
        print(f"unexpected build.prop size {len(blob)}")
        return 1
    if b"epdc." in blob:
        print("source already carries epdc properties -- expected pristine v25")
        return 1
    if OLD_LINE not in blob:
        print("line-69 anchor missing -- not the expected v25")
        return 1
    if FULL_ANCHOR not in blob:
        print("line-74 anchor missing -- not the expected v25")
        return 1

    new = blob.replace(OLD_LINE, NEW_BLOCK, 1).replace(FULL_ANCHOR, FULL_PROP, 1)
    if len(new) != FILE_SIZE:
        print(f"length changed: {len(new)} != {FILE_SIZE}")
        return 1

    if args.check:
        print("anchors ok, size stays", len(new))
        print("padding preserved:", len(new) - text_len(new))
        return 0

    print("copying image ...")
    dst = pathlib.Path(args.dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fi, dst.open("wb") as fo:
        while True:
            chunk = fi.read(1 << 22)
            if not chunk:
                break
            fo.write(chunk)

    with dst.open("r+b") as f:
        f.seek(OFF)
        f.write(new)

    before = hashlib.sha256(src.read_bytes()).hexdigest()
    after = hashlib.sha256(dst.read_bytes()).hexdigest()
    print("sha256:", before[:16], "->", after[:16])

    back = blob_at(dst)
    r = check_props(back)
    t = text_len(back)
    print(f"result: file={len(back)} text={t} padding={len(back) - t}")
    for k, v in r.items():
        print(f"  {k:22s} {'yes' if v else 'NO'}")
    print("tail of text:", repr(back[max(0, t - 60):t]))
    return 0 if all(r.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
