#!/usr/bin/env python3
"""Bypass wificond's PW3 driver-name check on the live G: system.img.

wificond (pw3_radio.cpp) verifies three things before createClientInterface:
  1. stat("/sys/module/ath6kl_sdio") is a directory
  2. stat("/sys/class/net/wlan0") and if_nametoindex("wlan0") succeed
  3. readlink("/sys/class/net/wlan0/phy80211/device/driver") basename
     strcmp's equal to "ath6kl_sdio"

KO1 uses Broadcom 4343W / dhd, so (3) can NEVER match: the sysfs driver link
names the dhd SDIO driver.  Conditions (1) and (2) are satisfied for real by
a stub ath6kl_sdio.ko plus an eth0 -> wlan0 rename in initramfs.  This patch
changes only the strcmp call site:

    18428: f7f1 e932  blx  9690 <strcmp@plt>
 -> 18428: 2000       movs r0, #0
    1842a: bf00       nop

so the subsequent `cmp r0,#0 / moveq r4,#1` treats the driver as verified.
Everything else in wificond is byte-identical.

The patch is applied IN PLACE on the ext4 image at the exact physical block,
so all inode metadata (0755, 0:2000, flags 0x80080) and the SELinux xattr
(u:object_r:wificond_exec:s0) stay untouched -- unlike `debugfs write`, which
recreates the inode.

Usage: python tools/patch-wificond-ko1-driver.py [--check]
"""
import argparse
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
IMG = pathlib.Path("G:/android/system.img")
ORIG = ROOT / "forensics" / "gsys" / "wificond"
PATCHED = ROOT / "forensics" / "gsys" / "wificond.patched"

# From `debugfs -R 'stat /bin/wificond'`: EXTENTS (0-46):103021-103067
INODE_FIRST_BLOCK = 103021
BLOCK_SIZE = 4096
FILE_OFFSET = 0x18428
# objdump prints Thumb-2 as halfwords ("f7f1 e932"); on disk each halfword is
# little-endian, so the byte sequence is f1 f7 32 e9.
EXPECTED = bytes.fromhex("f1f732e9")
REPLACEMENT = bytes.fromhex("002000bf")


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore", action="store_true",
                    help="put the original blx strcmp back and un-patch the image")
    a = ap.parse_args()

    if not IMG.exists():
        sys.exit("ABORT: %s not visible (switch back to the stock system)" % IMG)
    if not ORIG.exists():
        sys.exit("ABORT: pristine wificond dump missing: %s" % ORIG)

    raw = ORIG.read_bytes()
    if len(raw) < FILE_OFFSET + 4:
        sys.exit("ABORT: wificond dump too short")
    if raw[FILE_OFFSET:FILE_OFFSET + 4] != EXPECTED:
        sys.exit("ABORT: wificond at 0x%x is %s, expected %s -- wrong binary" %
                 (FILE_OFFSET, raw[FILE_OFFSET:FILE_OFFSET + 4].hex(), EXPECTED.hex()))

    patched = bytearray(raw)
    patched[FILE_OFFSET:FILE_OFFSET + 4] = REPLACEMENT
    image_offset = INODE_FIRST_BLOCK * BLOCK_SIZE + FILE_OFFSET

    with IMG.open("r+b" if not a.check else "rb") as f:
        f.seek(image_offset)
        on_disk = f.read(4)
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
            if state == "UNPATCHED":
                print("already pristine, nothing to restore")
            else:
                f.seek(image_offset)
                f.write(EXPECTED)
                f.flush()
                f.seek(image_offset)
                assert f.read(4) == EXPECTED, "restore verification failed"
                print("restored original 4 bytes")
        elif state == "ALREADY PATCHED":
            print("nothing to do")
        else:
            f.seek(image_offset)
            f.write(REPLACEMENT)
            f.flush()
            f.seek(image_offset)
            assert f.read(4) == REPLACEMENT, "writeback verification failed"
            print("patched (4 bytes)")

    if a.restore:
        print("system.img       sha256 : %s" % sha256(IMG))
        print("Rollback: re-pin preinit.conf to this digest, rebuild launcher,")
        print("or simply copy forensics/Readom-Duet.v5.sh back to G:/documents/.")
        return 0

    PATCHED.write_bytes(bytes(patched))
    print("pristine wificond sha256 : %s" % hashlib.sha256(raw).hexdigest())
    print("patched  wificond sha256 : %s" % hashlib.sha256(bytes(patched)).hexdigest())
    print("system.img       sha256 : %s" % sha256(IMG))
    print("Next: sync pw3-preinit.conf system_sha256, then rebuild the launcher.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
