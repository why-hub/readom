#!/usr/bin/env python3
"""Restore security.selinux on KO1 vendor firmware/module files in system.img.

ROOT CAUSE (found 2026-11-16, first Android session v9):
  avc: denied { read } for name="fw_bcmdhd_sr.bin"
       scontext=u:r:kernel:s0  tcontext=u:object_r:unlabeled:s0
  _dhdsdio_download_firmware: dongle image file download failed
  dhd_bus_start failed -> dhdsdio_probe failed
  => the Broadcom 4343W dongle NEVER came up.  wlan0 existed only as an
     empty, DOWN interface with no working MAC, so wificond reported
     "Failed to get interface mac address" / "No usable interface found"
     and the WiFi tile could not stay on.

  tmp/restore-1539-system.py had run `ea_rm <file> security.selinux` on
  these five files (the 15:39 build had rebuilt their inodes with debugfs
  and dropped the label).  But an unlabeled firmware file cannot be read
  by the kernel, so WiFi was broken from the very first Android boot.

FIX:
  Put back u:object_r:vendor_file:s0 WITH the trailing NUL (26 bytes) --
  the value the sibling modules cfg80211.ko / ath6kl_sdio.ko carry.
  `ea_set ... value\\000` is required; a plain value writes 25 bytes with
  no NUL and SELinux still treats it as unlabeled.

  Only files currently missing the label are touched; already-labelled
  files are left byte-identical.  Inode mode/uid/gid/flags are preserved
  (ea_set does not rebuild the inode, unlike debugfs write).

Usage (WSL, G: mounted rw at /mnt/g):
  python3 tools/patch-firmware-selinux.py [--image PATH] [--check] [--restore]
"""
import argparse
import pathlib
import subprocess
import sys

DEF_IMG = "/mnt/g/android/system.img"
LABEL = "u:object_r:vendor_file:s0"
LABEL_NUL = LABEL + "\\000"          # literal backslash-zero-zero for debugfs

FILES = [
    "/vendor/lib/modules/dhd.ko",
    "/vendor/lib/modules/falcon.ko",
    "/vendor/lib/modules/mxc_epdc_fb.ko",
    "/vendor/firmware/brcm_4343w/fw_bcmdhd_sr.bin",
    "/vendor/firmware/brcm_4343w/nvram.txt",
]


def dbg(img, cmd, write=False):
    args = ["debugfs"] + (["-w"] if write else []) + ["-R", cmd, img]
    return subprocess.run(args, capture_output=True, text=True)


def label_of(img, path):
    r = dbg(img, "stat " + path)
    for line in r.stdout.splitlines():
        if "security.selinux" in line:
            return line.split("=", 1)[1].strip().strip('"')
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=DEF_IMG)
    ap.add_argument("--check", action="store_true",
                    help="report label state only, no writes")
    ap.add_argument("--restore", action="store_true",
                    help="remove labels again (back to broken/unlabeled state)")
    a = ap.parse_args()
    img = a.image

    if not pathlib.Path(img).exists():
        sys.exit("ABORT: %s not visible (switch back to stock system / mount G:)" % img)

    print("image: %s" % img)
    state = {}
    for f in FILES:
        lab = label_of(img, f)
        state[f] = lab
        print("  %-55s %s" % (f, lab if lab else "<<< UNLABELED >>>"))

    if a.check:
        missing = [f for f, l in state.items() if not l]
        print("check: %d/%d unlabeled" % (len(missing), len(FILES)))
        return 0

    if a.restore:
        for f, lab in state.items():
            if lab:
                dbg(img, "ea_rm %s security.selinux" % f, write=True)
        print("restored (removed labels):")
        for f in FILES:
            print("  %-55s %s" % (f, label_of(img, f) or "<<< UNLABELED >>>"))
        return 0

    changed = 0
    for f, lab in state.items():
        if lab == ('"%s\\000"' % LABEL) or (lab and LABEL in lab):
            print("  keep  %s (already labelled)" % f)
            continue
        # Remove any malformed (e.g. 25-byte no-NUL) value first.
        if lab:
            dbg(img, "ea_rm %s security.selinux" % f, write=True)
        r = dbg(img, "ea_set %s security.selinux %s" % (f, LABEL_NUL), write=True)
        if r.returncode != 0 and r.stderr.strip():
            print("  WARN  %s: %s" % (f, r.stderr.strip()[:120]))
        changed += 1

    print("--- verify ---")
    ok = True
    for f in FILES:
        lab = label_of(img, f)
        good = bool(lab and LABEL in lab and "\\000" in lab)
        print("  %-55s %s" % (f, lab if lab else "<<< UNLABELED >>>"))
        ok = ok and good
    print("changed=%d  all_labelled_with_nul=%s" % (changed, ok))
    if not ok:
        sys.exit("VERIFY FAILED")
    print("NEXT: sha256(G:/android/system.img) -> update preinit.conf system_sha256 "
          "-> rebuild launcher -> redeploy -> re-probe WiFi (do NOT full-hash on G: after deploy)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
