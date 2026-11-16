#!/usr/bin/env python3
"""Lower ART's max heap (dalvik.vm.heapsize) from 256m to 192m.

Why this is a KO1-specific fix, not a preference
------------------------------------------------
The "duet" board (Kindle Oasis 1) runs a 2G/2G kernel split, so a 32-bit
process gets 2 GiB of user address space and the mmap area grows bottom-up
from TASK_SIZE/3 (~715 MiB) toward the stack.  After the code, the boot image
and the heap are taken out of that window, roughly 1.2 GiB is left.

ART creates the concurrent-copying region space as ONE contiguous mapping of
`dalvik.vm.heapsize` bytes.  At 256m that single reservation is a fifth of the
whole window.

Measured on the device (2026-11-16, WeRead 2.1.2, forensics/weread-crash):

    1.086 GiB  VmSize for a 9-thread process right after fork
    1.316 GiB  VmSize when the app stops growing -- then every mmap fails
    E CursorWindow: Could not allocate CursorWindow ... error -12 (ENOMEM)
    dex2oat: heap.cc:438 Check failed: region_space_mem_map != nullptr
              No region space mem map

PW3 is 3G/1G, so its window is ~2 GiB and the same 256m heap is affordable.
On 2G/2G it is not.  heapgrowthlimit is 128m on this build, so 192m keeps a
comfortable margin above the limit while giving the window back 64 MiB.

The text "256" is rewritten to "192" IN PLACE (same length, same inode), so
mode 0644, uid/gid 0:0 and the system_file SELinux xattr are untouched.

Usage: python3 tools/patch-buildprop-heapsize.py [--image PATH] [--check] [--restore]
"""
import argparse
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
IMG = pathlib.Path("G:/android/system.img")

# `debugfs -R 'stat /build.prop'`: EXTENTS (0-1):83430-83431, size 4719
FIRST_BLOCK = 83430
BLOCK_SIZE = 4096
FILE_OFFSET = 3787            # "dalvik.vm.heapsize=256m"
ANCHOR = b"dalvik.vm.heapsize="
OLD = b"256"
NEW = b"192"
# the digits sit right after the anchor
DIGIT_OFFSET = FILE_OFFSET + len(ANCHOR)


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=str(IMG))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()

    image = pathlib.Path(a.image)
    if not image.exists():
        sys.exit("ABORT: %s not visible (switch back to the stock system)" % image)

    base = FIRST_BLOCK * BLOCK_SIZE
    anchor_at = base + FILE_OFFSET
    digits_at = base + DIGIT_OFFSET

    with image.open("r+b" if not a.check else "rb") as f:
        f.seek(anchor_at)
        if f.read(len(ANCHOR)) != ANCHOR:
            sys.exit("ABORT: anchor not at 0x%x -- wrong image or build.prop moved"
                     % anchor_at)
        f.seek(digits_at)
        on_disk = f.read(3)
        print("image        : %s" % image)
        print("anchor at    : 0x%x (block %d + %d)" % (anchor_at, FIRST_BLOCK, FILE_OFFSET))
        print("digits at    : 0x%x -> %r" % (digits_at, on_disk.decode("latin1")))

        if on_disk == OLD:
            state = "256m (unpatched)"
        elif on_disk == NEW:
            state = "192m (patched)"
        else:
            sys.exit("ABORT: expected 256 or 192, found %r" % on_disk)
        print("state        : %s" % state)

        if a.check:
            print("check only, no write")
            return 0
        if a.restore:
            f.seek(digits_at)
            f.write(OLD)
            f.flush()
            f.seek(digits_at)
            assert f.read(3) == OLD, "restore verification failed"
            print("restored 256")
        elif on_disk == NEW:
            print("nothing to do")
        else:
            f.seek(digits_at)
            f.write(NEW)
            f.flush()
            f.seek(digits_at)
            assert f.read(3) == NEW, "writeback verification failed"
            print("patched 256 -> 192")

    print("system.img sha256 : %s" % sha256(image))
    print("Next: re-pin pw3-preinit.conf system_sha256, then rebuild the launcher.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
