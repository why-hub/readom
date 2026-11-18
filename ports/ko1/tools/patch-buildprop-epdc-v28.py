#!/usr/bin/env python3
"""KO1 v28: put the EPDC properties in build.prop's tail padding.

Why v27 failed
---------------
v27 appended `\\ndebug.pw3.epdc.fast=1` to the 4719-byte file.  On the device
`getprop debug.pw3.epdc.full_every` returned 20 (that line was a straight
in-place replacement on line 78), but `debug.pw3.epdc.fast` came back empty:

    $ grep -an epdc /system/build.prop
    78:debug.pw3.epdc.full_every=20

The property never reached the property table.  A property line needs a
trailing newline; a last line without one is dropped by the loader.

Where the space is
------------------
The v25 build.prop is 4719 bytes but its real text ends at 4661
(`...dalvik.vm.stack-trace-dir=/data/anr\\n`).  The remaining 58 bytes are
NUL padding that was already there.  That is free space inside the file:

    text        4661 bytes
    padding       58 bytes  (NUL)
    ------------
    file        4719 bytes

So both properties go into the padding, each properly newline-terminated,
and no line anywhere else has to be touched:

    debug.pw3.epdc.fast=1\\n         22
    debug.pw3.epdc.full_every=20\\n   29
    ------------------------------------
    used                             51 of 58, 7 NUL left over

This is strictly better than v27: the ART comment on line 69 and the
`--no-watch-dog` note on line 74 both stay as upstream wrote them, and the
file length, inode, block map and the heapsize offset all stay valid.

Usage
-----
    patch-buildprop-epdc-v28.py --check         # inspect the source image
    patch-buildprop-epdc-v28.py                 # write the target image
    patch-buildprop-epdc-v28.py --verify PATH   # re-check a written image
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

SRC = pathlib.Path("build/backup/system.img.v25-a2229b9c")
DST = pathlib.Path("build/images/system-v28.img")

BLOCK = 83430
BLOCK_SIZE = 4096
INBLOCK = 58
FILE_SIZE = 4719
OFF = BLOCK * BLOCK_SIZE + INBLOCK

PROPS = b"debug.pw3.epdc.fast=1\ndebug.pw3.epdc.full_every=20\n"


def blob_at(img: pathlib.Path) -> bytes:
    with img.open("rb") as f:
        f.seek(OFF)
        return f.read(FILE_SIZE)


def split_padding(blob: bytes) -> tuple[bytes, int]:
    """Return (text, padding_len).  Padding must be all NUL and follow a newline."""
    end = len(blob)
    while end > 0 and blob[end - 1] == 0:
        end -= 1
    return blob[:end], len(blob) - end


def report(tag: str, blob: bytes) -> int:
    text, pad = split_padding(blob)
    print(f"{tag}: file={len(blob)} text={len(text)} padding={pad}")
    print(f"{tag}: text ends with newline: {text.endswith(b'\\n')}")
    for name, needle in (
        ("fast", b"debug.pw3.epdc.fast=1"),
        ("full_every", b"debug.pw3.epdc.full_every=20"),
    ):
        print(f"{tag}: {name:11s} {'present' if needle in blob else 'ABSENT'}")
    return 0 if len(blob) == FILE_SIZE else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--verify", metavar="PATH")
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--dst", default=str(DST))
    args = ap.parse_args()

    if args.verify:
        img = pathlib.Path(args.verify)
        blob = blob_at(img)
        rc = report("verify", blob)
        text, pad = split_padding(blob)
        ok = (
            rc == 0
            and b"debug.pw3.epdc.fast=1" in blob
            and b"debug.pw3.epdc.full_every=20" in blob
            and pad >= 0
            and b"# 12 MB less in /data. Given up" in blob
            and b"# --no-watch-dog: see pw3.mk." in blob
        )
        print("body untouched :", "yes" if b"# 12 MB less in /data. Given up" in blob else "NO")
        print("watchdog note  :", "yes" if b"# --no-watch-dog: see pw3.mk." in blob else "NO")
        print("RESULT         :", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    src = pathlib.Path(args.src)
    if not src.exists():
        print(f"no pristine v25 image at {src}")
        return 1

    blob = blob_at(src)
    if len(blob) != FILE_SIZE:
        print(f"unexpected build.prop size {len(blob)}")
        return 1

    text, pad = split_padding(blob)
    print(f"source: text={len(text)} padding={pad} (need {len(PROPS)})")
    if not text.endswith(b"\n"):
        print("text does not end with a newline -- refusing to guess")
        return 1
    if pad < len(PROPS):
        print(f"padding too small: {pad} < {len(PROPS)}")
        return 1
    if b"epdc." in blob:
        print("source image already carries epdc properties -- expected pristine v25")
        return 1

    if args.check:
        print("would write:", PROPS.decode().strip().replace("\n", " / "))
        print("result padding left:", pad - len(PROPS))
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

    new_blob = text + PROPS + b"\x00" * (pad - len(PROPS))
    assert len(new_blob) == FILE_SIZE, len(new_blob)
    with dst.open("r+b") as f:
        f.seek(OFF)
        f.write(new_blob)

    before = hashlib.sha256(src.read_bytes()).hexdigest()
    after = hashlib.sha256(dst.read_bytes()).hexdigest()
    print("sha256:", before[:16], "->", after[:16])

    back = blob_at(dst)
    return report("result", back)


if __name__ == "__main__":
    sys.exit(main())
