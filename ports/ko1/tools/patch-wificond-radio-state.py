#!/usr/bin/env python3
"""Make wificond's ReachedState() always report "target state reached".

Evidence (v12 run, 2026-11-16 19:28):
  * dhd now creates the netdev itself (iface_name=wlan0), so the v12 kernel
    module no longer renames eth0 -> wlan0.
  * /sys/module/ath6kl_sdio is a directory (stub module loaded from initramfs)
  * /sys/class/net/wlan0 exists with MAC xx:xx:xx:xx:xx:xx
  * yet wificond logs, every 15 s:
        E wificond: PW3 radio module transition did not reach verified state
  * the strcmp in InterfaceMatches() is already stubbed to 0 (patch-wificond-
    ko1-driver.py), so the only remaining failure is ReadModuleState() or
    ReadInterfaceState() returning false inside the `wifi` SELinux domain --
    both are stat()/ioctl probes that cannot be verified from a `shell` domain
    without root.

Pw3SetRadioEnabled() is the only consumer.  Its inlined ReachedState() funnels
every "false" exit through one instruction:

    18444: 2400    movs r4, #0     ; -> returned by `moveq r0, r4` at 18454

Turning that into `movs r4, #1` makes the radio check unconditional, which is
correct for KO1: the radio is brought up by initramfs (insmod dhd.ko) and must
never be torn down by the PW3 Atheros property dance.  The disabled branch is
unaffected in practice -- returning true there simply skips `rmmod`.

The 1 byte is written IN PLACE at the physical block of /bin/wificond, so inode
metadata (0755, 0:2000, flags 0x80080) and the SELinux xattr
(u:object_r:wificond_exec:s0) are preserved.

Usage: python3 tools/patch-wificond-radio-state.py [--check] [--restore]
"""
import argparse
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
IMG = pathlib.Path("G:/android/system.img")
ORIG = ROOT / "forensics" / "gsys" / "wificond"

# `debugfs -R 'stat /bin/wificond'` on the shipped image: EXTENTS (0-46):103021-103067
INODE_FIRST_BLOCK = 103021
BLOCK_SIZE = 4096

FILE_OFFSET = 0x18444          # movs r4, #0 -> movs r4, #1
EXPECTED = bytes.fromhex("0024")
REPLACEMENT = bytes.fromhex("0124")

# sanity: the earlier strcmp stub must already be there, proving we are looking
# at the same wificond build the other patch tool agrees with
STRCMP_OFFSET = 0x18428
STRCMP_PATCHED = bytes.fromhex("002000bf")


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=str(IMG),
                    help="system.img path (default G:/android/system.img; "
                         "use /mnt/g/android/system.img when running inside WSL)")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()

    image = pathlib.Path(a.image)
    if not image.exists():
        sys.exit("ABORT: %s not visible (switch back to the stock system)" % image)
    if not ORIG.exists():
        sys.exit("ABORT: pristine wificond dump missing: %s" % ORIG)

    raw = ORIG.read_bytes()
    if raw[FILE_OFFSET:FILE_OFFSET + 2] != EXPECTED:
        sys.exit("ABORT: pristine wificond at 0x%x is %s, expected %s" %
                 (FILE_OFFSET, raw[FILE_OFFSET:FILE_OFFSET + 2].hex(), EXPECTED.hex()))

    image_offset = INODE_FIRST_BLOCK * BLOCK_SIZE + FILE_OFFSET

    with image.open("r+b" if not a.check else "rb") as f:
        f.seek(STRCMP_OFFSET + INODE_FIRST_BLOCK * BLOCK_SIZE)
        if f.read(4) != STRCMP_PATCHED:
            sys.exit("ABORT: wificond driver-name patch (0x18428) missing -- wrong image")

        f.seek(image_offset)
        on_disk = f.read(2)
        print("image offset : %d (block %d + 0x%x)" %
              (image_offset, INODE_FIRST_BLOCK, FILE_OFFSET))
        print("bytes on disk: %s" % on_disk.hex())
        if on_disk == EXPECTED:
            state = "UNPATCHED"
        elif on_disk == REPLACEMENT:
            state = "ALREADY PATCHED"
        else:
            sys.exit("ABORT: unexpected bytes in system.img at that offset")
        print("state        : %s" % state)

        if a.check:
            print("check only, no write")
            return 0
        if a.restore:
            f.seek(image_offset)
            f.write(EXPECTED)
            f.flush()
            f.seek(image_offset)
            assert f.read(2) == EXPECTED, "restore verification failed"
            print("restored original 2 bytes")
        elif state == "ALREADY PATCHED":
            print("nothing to do")
        else:
            f.seek(image_offset)
            f.write(REPLACEMENT)
            f.flush()
            f.seek(image_offset)
            assert f.read(2) == REPLACEMENT, "writeback verification failed"
            print("patched (2 bytes)")

    print("system.img sha256 : %s" % sha256(image))
    print("Next: re-pin pw3-preinit.conf system_sha256, then rebuild the launcher.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
