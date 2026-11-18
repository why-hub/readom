#!/usr/bin/env python3
"""KO1 v27: let EPDC use the fast (A2) waveform by default.

The measurement
---------------
dumpsys SurfaceFlinger on the device reports the PW3 panel counters:

    PW3 panel: presents=375 a2=0 du=22 gl16=147 gc16=26 settles=1
               prepare_us_total=12769855 send_us_total=36386972

Five swipes produced 34 presents and 8.2 s of submit time: 241 ms per frame,
about 4 fps.  `a2` is zero -- the fastest waveform the panel knows is never
used, and 88% of updates run GL16, which is the grey-scale waveform meant for
images.  Scrolling text through GL16 is exactly why the screen lags the finger
even though input arrives at 62 Hz with no dropped frames.

gralloc.pw3.so carries three debug properties:

    debug.pw3.epdc.settle_ms   (default 350 ms)
    debug.pw3.epdc.fast        (unset)
    debug.pw3.epdc.full_every  (unset)

but it reads them once, when the panel is opened at SurfaceFlinger startup.
Setting them at runtime does nothing (verified: setprop debug.pw3.epdc.fast 1,
swipes, `a2` stayed 0), and ctl.restart/none of the restart paths are available
without root on a production build.  So the switch has to be in the image.

Why build.prop and why the length does not change
-------------------------------------------------
system.img is not repacked.  build.prop is 4719 bytes living at image offset
83430*4096+58, and tools/patch-buildprop-heapsize.py addresses it through the
fixed file offset 3787.  Appending bytes would keep that offset valid (it sits
before the end) but changing the file size means touching the ext4 inode, so
the growth is paid for inside the file instead:

    line 69 (an ART comment)      old 78 bytes  ->  new 55 bytes   -23
    appended property                    0      ->  new 23 bytes   +23
                                                             ----------
    file total                        4719            4719         0

The trimmed comment is prose about the ART boot image; shortening it costs
nothing.  Everything else -- mode 0644, the SELinux label, the block map, the
heapsize offset -- stays as it was.

Only preinit's system.img pin has to follow (tools/update-preinit-system-sha.py,
run automatically by tools/build-duet-launcher.sh).

Usage
-----
    patch-buildprop-epdc-fast.py --check        # report, do not write
    patch-buildprop-epdc-fast.py                # patch G:/android/system.img
    patch-buildprop-epdc-fast.py --restore      # put the original line back,
                                                # drop the property
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

IMG = pathlib.Path("G:/android/system.img")

BLOCK = 83430
BLOCK_SIZE = 4096
INBLOCK = 58          # ro.build.id=OC starts here inside block 83430
FILE_SIZE = 4719

OLD_LINE = b"# 12 MB less in /data. Given up: the boot image's address is no longer random."
NEW_LINE = b"# 12 MB less in /data. Boot address no longer random.   "
PROP = b"\ndebug.pw3.epdc.fast=1"

# A2 leaves ghosting behind, so the panel needs a periodic full refresh to
# clean up.  debug.pw3.epdc.full_every does that every N partial updates.
# It is 28 bytes; line 74 ("# --no-watch-dog: see pw3.mk.") is 29, so the
# property plus one trailing space replaces it byte for byte.
FULL_ANCHOR = b"# --no-watch-dog: see pw3.mk."
FULL_PROP = b"debug.pw3.epdc.full_every=20 "

MARK = b"debug.pw3.epdc.fast=1\n"


def read_prop(img: pathlib.Path) -> bytes:
    with img.open("rb") as f:
        f.seek(BLOCK * BLOCK_SIZE + INBLOCK)
        return f.read(FILE_SIZE)


def write_prop(img: pathlib.Path, blob: bytes) -> None:
    assert len(blob) == FILE_SIZE, len(blob)
    with img.open("r+b") as f:
        f.seek(BLOCK * BLOCK_SIZE + INBLOCK)
        f.write(blob)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--image", default=str(IMG))
    args = ap.parse_args()

    img = pathlib.Path(args.image)
    if not img.exists():
        print(f"no image at {img} (switch the device to the stock OS first)")
        return 1

    blob = read_prop(img)
    if len(blob) != FILE_SIZE:
        print(f"unexpected build.prop size {len(blob)} (want {FILE_SIZE})")
        return 1

    patched = PROP in blob
    full = FULL_PROP in blob

    if args.check:
        print("build.prop size:", len(blob))
        print("epdc.fast property:", "present" if patched else "absent")
        print("epdc.full_every   :", "present" if full else "absent")
        print("trimmed comment:", "yes" if NEW_LINE in blob else "no")
        return 0 if (patched and full) else 1

    before = hashlib.sha256(img.read_bytes()).hexdigest()

    if args.restore:
        if not patched and not full:
            print("nothing to restore")
            return 0
        new = blob.replace(NEW_LINE, OLD_LINE, 1)
        new = new.replace(PROP, b"", 1)
        new = new.replace(FULL_PROP, FULL_ANCHOR, 1)
        if len(new) != FILE_SIZE:
            print(f"restore would change size: {len(new)} != {FILE_SIZE}")
            return 1
        write_prop(img, new)
        print("restored original comment, dropped both properties")
    else:
        if patched and full:
            print("already patched")
            return 0
        if not patched and OLD_LINE not in blob:
            print("comment anchor not found -- image is not the expected v25")
            return 1
        new = blob
        if not patched:
            new = new.replace(OLD_LINE, NEW_LINE, 1) + PROP
        if not full:
            if FULL_ANCHOR not in new:
                print("full_every anchor not found")
                return 1
            new = new.replace(FULL_ANCHOR, FULL_PROP, 1)
        if len(new) != FILE_SIZE:
            print(f"patch would change size: {len(new)} != {FILE_SIZE}")
            return 1
        write_prop(img, new)
        print("patched build.prop: debug.pw3.epdc.fast=1, full_every=20")

    # read back and prove it landed
    back = read_prop(img)
    after = hashlib.sha256(img.read_bytes()).hexdigest()
    ok_size = len(back) == FILE_SIZE
    print("size  :", len(back), "OK" if ok_size else "BROKEN")
    print("fast  :", "present" if PROP in back else "ABSENT")
    print("full  :", "present" if FULL_PROP in back else "ABSENT")
    print("sha256:", before[:16], "->", after[:16])
    if args.restore:
        return 0 if ok_size and PROP not in back and FULL_PROP not in back else 1
    return 0 if ok_size and PROP in back and FULL_PROP in back else 1


if __name__ == "__main__":
    sys.exit(main())
