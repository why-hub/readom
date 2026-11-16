#!/usr/bin/env python3
"""Allow the stock duet vendor modules (dhd.ko / falcon.ko) to load.

Why this is needed
------------------
The Android kernel we build carries LOCALVERSION "-lab126-pw3-android81", so
its vermagic is

    3.0.35-lab126-pw3-android81 preempt mod_unload ARMv7 p2v8

The Oasis 1 (duet) Wi-Fi stack ships as two prebuilt Broadcom modules whose
vermagic is

    3.0.35-lab126 preempt mod_unload ARMv7 p2v8

`check_modinfo()` does a plain strcmp, so insmod fails with -ENOEXEC and the
Android init log prints a misleading "No such file or directory".

We cannot rebuild those two: there is no bcmdhd source in the lab126 GPL tree
(drivers/net/wireless/ has ath6kl but no bcmdhd), and the driver is the only
thing that knows this board's SDIO glue and nvram layout. We also cannot drop
the "-pw3-android81" suffix from LOCALVERSION: the Android vendor image loads
nine prebuilt modules (cyttsp4_core/i2c/mt_b, cfg80211, g_android, mxc_otg,
...) that are stamped with the Android vermagic, and the touch controller
stops working without them.

So: keep the kernel identity, and let exactly these two modules through.
Nothing else is relaxed -- an unknown module with a foreign vermagic still
gets -ENOEXEC, and the module is tainted as forced so `dmesg` never pretends
this was a clean load.

Usage:
    python3 tools/patch-allow-foreign-vendor-modules.py [--revert] [--check]
"""

import argparse
import os
import shutil
import sys

SRC = "/home/builder/pw3-build/linux-android-core/kernel/module.c"
BACKUP = SRC + ".pre-foreign-vendor"

ANCHOR = """	} else if (!same_magic(modmagic, vermagic, info->index.vers)) {
		printk(KERN_ERR "%s: version magic '%s' should be '%s'\\n",
		       mod->name, modmagic, vermagic);
		return -ENOEXEC;
	}
"""

PATCHED = """	} else if (!same_magic(modmagic, vermagic, info->index.vers)) {
		const char *modname = get_modinfo(info, "name");

		/*
		 * KO1/duet: the Broadcom Wi-Fi driver and its falcon helper are
		 * prebuilt vendor binaries with vermagic "3.0.35-lab126" while
		 * this kernel is "3.0.35-lab126-pw3-android81". There is no
		 * bcmdhd source to rebuild them from, and the Android vendor
		 * image needs the -pw3-android81 suffix for its own modules.
		 * Let these two through and taint the kernel so nobody reads
		 * the resulting dmesg as a clean load.
		 */
		if (!ko1_foreign_vendor_module(modname)) {
			printk(KERN_ERR "%s: version magic '%s' should be '%s'\\n",
			       mod->name, modmagic, vermagic);
			return -ENOEXEC;
		}
		printk(KERN_WARNING
		       "%s: foreign vermagic '%s' accepted (kernel '%s')\\n",
		       mod->name, modmagic, vermagic);
		add_taint_module(mod, TAINT_FORCED_MODULE);
	}
"""

HELPER_ANCHOR = "static int check_modinfo(struct module *mod, struct load_info *info)\n"
HELPER = """/*
 * Modules we are allowed to load even though their vermagic does not match
 * this kernel's. Keep this list as short as the missing sources force it to
 * be; anything that can be rebuilt from the lab126 tree must be rebuilt.
 */
static bool ko1_foreign_vendor_module(const char *name)
{
	return name && (!strcmp(name, "dhd") || !strcmp(name, "falcon"));
}

static int check_modinfo(struct module *mod, struct load_info *info)
"""


def die(msg):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(SRC):
        die("not found: " + SRC)

    text = open(SRC, encoding="utf-8").read()
    patched = "ko1_foreign_vendor_module" in text

    if args.check:
        print("patched" if patched else "clean")
        return

    if args.revert:
        if not patched:
            print("already clean, nothing to do")
            return
        if not os.path.exists(BACKUP):
            die("no backup at " + BACKUP)
        shutil.copy2(BACKUP, SRC)
        print("reverted from " + BACKUP)
        return

    if patched:
        print("already patched, nothing to do")
        return

    if text.count(ANCHOR) != 1:
        die("anchor for check_modinfo body matched %d times" % text.count(ANCHOR))
    if text.count(HELPER_ANCHOR) != 1:
        die("helper anchor matched %d times" % text.count(HELPER_ANCHOR))

    shutil.copy2(SRC, BACKUP)
    text = text.replace(HELPER_ANCHOR, HELPER)
    text = text.replace(ANCHOR, PATCHED)
    open(SRC, "w", encoding="utf-8").write(text)
    print("patched " + SRC)
    print("backup  " + BACKUP)


if __name__ == "__main__":
    main()
