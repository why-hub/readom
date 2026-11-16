#!/usr/bin/env python3
"""KO1 v15: make only the real plug detector report Mains, and add
change-only diagnostics so logcat shows which power_supply node lies.

Background (v14 result): after switching soda_boost to UPS and pointing
soda_chg.online at soda_usb_charger_connected, healthd still reported
"chg=a" and BatteryService kept plugged=AC with the cable out.  healthd's
type table is {Unknown,Battery,Mains,USB_DCP,USB_CDP,USB_ACA,Wireless}, so
any node whose type is Mains contributes AC-online.  Three nodes still
claimed Mains:

    soda_chg            -> now reads the real plug atomic (verified)
    max77696-charger    -> online = chg_online (CHGINA bit)
    max77696-charger-eh -> online = chg_online (CHGINB bit)

CHGINB is the second charger input; on this board it is not the USB cable,
so the energy-harvester node can sit "online" forever.  Both PMIC nodes are
therefore demoted to UPS (ignored by healthd) and the only remaining Mains
node is soda_chg, which tracks the cable.

Diagnostics: each of the three getters prints on value change only, tagged
KO1CHG, so `logcat | grep KO1CHG` gives an exact timeline of what each node
reports while the cable is pulled.
"""

import pathlib
import sys

ROOT = pathlib.Path("/home/builder/pw3-build/linux-android-core/drivers/power")

DIAG = (
    "\t\t\t{ static int ko1_last = -1;\n"
    "\t\t\t  if (ko1_last != val->intval) {\n"
    "\t\t\t\tko1_last = val->intval;\n"
    "\t\t\t\tprintk(KERN_INFO \"KERNEL: KO1CHG %s online=%%d\\n\", val->intval);\n"
    "\t\t\t  } }\n"
)


def sub(path, old, new, tag, count=1):
    p = ROOT / path
    s = p.read_text(errors="surrogateescape")
    if new in s:
        print("%-28s %-14s ALREADY" % (path, tag))
        return False
    if old not in s:
        print("%-28s %-14s ANCHOR MISSING" % (path, tag))
        return False
    s = s.replace(old, new, count)
    p.write_text(s, errors="surrogateescape")
    print("%-28s %-14s PATCHED" % (path, tag))
    return True


def main():
    if not ROOT.is_dir():
        sys.exit("ABORT: kernel power dir not found: %s" % ROOT)

    # --- 1. max77696-charger: demote to UPS, add diag -------------------
    sub(
        "max77696-charger.c",
        "\tme->psy.type            = POWER_SUPPLY_TYPE_MAINS;",
        "\t/* KO1: only soda_chg may claim Mains (that is the real cable\n"
        "\t * detector).  As Mains, healthd kept AC-online asserted forever. */\n"
        "\tme->psy.type            = POWER_SUPPLY_TYPE_UPS;",
        "type -> UPS",
    )
    sub(
        "max77696-charger.c",
        "\t\tcase POWER_SUPPLY_PROP_ONLINE:\n"
        "#ifdef CONFIG_LAB126_ANDROID_POWER_SUPPLY_ABI\n"
        "\t\t\t/* A charger can stay enabled after its cable is removed. */\n"
        "\t\t\tval->intval = me->chg_online;\n"
        "#else\n"
        "\t\t\tval->intval = me->chg_enable;\n"
        "#endif\n"
        "\t\t\tbreak;",
        "\t\tcase POWER_SUPPLY_PROP_ONLINE:\n"
        "#ifdef CONFIG_LAB126_ANDROID_POWER_SUPPLY_ABI\n"
        "\t\t\t/* A charger can stay enabled after its cable is removed. */\n"
        "\t\t\tval->intval = me->chg_online;\n"
        "#else\n"
        "\t\t\tval->intval = me->chg_enable;\n"
        "#endif\n"
        + DIAG.replace("%s", "chgA") +
        "\t\t\tbreak;",
        "diag chgA",
    )

    # --- 2. max77696-charger-eh: demote to UPS, add diag ----------------
    sub(
        "max77696-charger-eh.c",
        "    me->psy.type             = POWER_SUPPLY_TYPE_MAINS;",
        "    /* KO1: CHGINB (energy harvester input) is not the USB cable.\n"
        "     * As Mains it pins healthd's AC-online at 1. */\n"
        "    me->psy.type             = POWER_SUPPLY_TYPE_UPS;",
        "type -> UPS",
    )
    sub(
        "max77696-charger-eh.c",
        "    case POWER_SUPPLY_PROP_PRESENT:\n"
        "    case POWER_SUPPLY_PROP_ONLINE:\n"
        "        val->intval = me->chg_online;\n"
        "        break;",
        "    case POWER_SUPPLY_PROP_PRESENT:\n"
        "    case POWER_SUPPLY_PROP_ONLINE:\n"
        "        val->intval = me->chg_online;\n"
        + DIAG.replace("%s", "chgB").replace("\t\t\t", "        ") +
        "        break;",
        "diag chgB",
    )

    # --- 3. soda.c: diag on the real detector ---------------------------
    sub(
        "soda.c",
        "\t\t\tval->intval = (int) atomic_read(&soda_usb_charger_connected);\n"
        "\t\t\tbreak;",
        "\t\t\tval->intval = (int) atomic_read(&soda_usb_charger_connected);\n"
        + DIAG.replace("%s", "soda_chg") +
        "\t\t\tbreak;",
        "diag soda_chg",
    )

    print("\n=== verification ===")
    for f, pat in (
        ("max77696-charger.c", "POWER_SUPPLY_TYPE_UPS"),
        ("max77696-charger-eh.c", "POWER_SUPPLY_TYPE_UPS"),
        ("soda.c", "KO1CHG soda_chg"),
    ):
        s = (ROOT / f).read_text(errors="surrogateescape")
        print("%-26s UPS=%d  KO1CHG=%d" % (f, s.count("POWER_SUPPLY_TYPE_UPS"), s.count("KO1CHG")))
    mains = (ROOT / "soda.c").read_text(errors="surrogateescape").count(
        "soda_chg_psy.type = POWER_SUPPLY_TYPE_MAINS"
    )
    print("soda_chg still Mains:", mains == 1)


if __name__ == "__main__":
    main()
