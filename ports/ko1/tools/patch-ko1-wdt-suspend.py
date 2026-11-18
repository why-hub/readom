#!/usr/bin/env python3
"""KO1 v26: stop the 60 s watchdog from killing the SoC while it is asleep.

The symptom
-----------
Lock the screen in Android and the device never comes back; a while later it
reboots and lands in the stock OS (expected: the Android boot is a one-shot
KUAL handoff, so any reboot returns to stock).

Two independent ways to get there, both armed by the current config:

  [1] watchdog / suspend
        imx2-wdt.0: IMX2+ Watchdog Timer enabled. timeout=60s (nowayout=1)
      CONFIG_WATCHDOG_NOWAYOUT=y, CONFIG_IMX2_WDT=y, and imx2_wdt_driver has
      only .remove and .shutdown -- no .suspend / .resume.  The driver keeps
      the hardware alive with a mod_timer() that pings every timeout/4 = 15 s
      (imx2_wdt_timer_ping).  Timers do not run during suspend, so the 15 s
      ping stops the moment the system suspends, while the WDOG counter keeps
      counting down from 60 s.  Nothing can stop it: imx2_wdt_stop() does not
      touch WDE, it just keeps pinging.

  [2] panic
        cmdline: panic=5
      Any panic reboots after five seconds and takes the evidence with it.

What this patch does
--------------------
  [1] imx2_wdt gains .suspend / .resume:
        suspend : del_timer_sync(), ping once, clear WCR.WDE (two writes --
                  i.MX6 latches WCR only after two identical writes, which is
                  why imx2_wdt_setup() writes it twice as well).  WDE=0 stops
                  the counter, so a 60 s timeout can no longer reset the SoC
                  mid-sleep.
        resume  : imx2_wdt_setup() reprograms the timeout and sets WDE again,
                  then ping + re-arm the pinger.  This is deliberately the
                  first thing resume does, so a later suspend failure in some
                  other driver still leaves a live watchdog as a backstop.
      Printk on both sides ("KO1WDT suspend/resume: wcr=...") so the log tells
      us whether suspend was even reached.

  [2] a late_initcall prints SRC_SRSR (latched across reset) and SRC_GPR10:
          KO1RST srsr=........ gpr10=........
      If the watchdog did the reboot, the WDOG bit in SRSR says so.  Read via
      ioremap so the cold path can never fault on a bad static mapping.

Both changes are kernel-side only; system.img is untouched.

Usage
-----
    patch-ko1-wdt-suspend.py            # apply (idempotent)
    patch-ko1-wdt-suspend.py --check
    patch-ko1-wdt-suspend.py --restore
"""
from __future__ import annotations

import argparse
from pathlib import Path

SRC = Path("/home/builder/pw3-build/linux-android-core")
WDT_C = SRC / "drivers/watchdog/imx2_wdt.c"
GPIO_C = SRC / "arch/arm/mach-mx6/mx6_wario_gpio.c"

# --------------------------------------------------------------------------
# [1] imx2_wdt.c: suspend/resume around the 60 s hardware timer
# --------------------------------------------------------------------------
WDT_ANCHOR = """static struct platform_driver imx2_wdt_driver = {
\t.remove\t\t= __exit_p(imx2_wdt_remove),
\t.shutdown\t= imx2_wdt_shutdown,"""

WDT_NEW = '''/* ===================== KO1 v26: survive suspend ===================== *
 * The hardware WDOG counts from 60 s and nothing ever clears WDE:
 * imx2_wdt_stop() only keeps pinging.  Pingers are mod_timer() based, and
 * timers are frozen during suspend, so every suspend turned into a guaranteed
 * reset ~60 s later.
 *
 * suspend: stop counting.  resume: start counting again, first thing, so a
 * later failure in another driver's resume still ends in a watchdog reset
 * instead of a silently wedged SoC.
 * ===================================================================== */
static int imx2_wdt_suspend(struct platform_device *pdev, pm_message_t state)
{
\tu16 val;

\tif (!test_bit(IMX2_WDT_STATUS_STARTED, &imx2_wdt.status))
\t\treturn 0;

\t/* Nothing will ping us while asleep, so drop the pinger first.
\t * del_timer_sync also covers the case where the callback is running. */
\tdel_timer_sync(&imx2_wdt.timer);

\t/* Ping once so the counter is at full 60 s, then clear WDE.  i.MX6
\t * latches WCR only after two consecutive identical writes. */
\timx2_wdt_ping();
\tval = __raw_readw(imx2_wdt.base + IMX2_WDT_WCR);
\tval &= ~IMX2_WDT_WCR_WDE;
\t__raw_writew(val, imx2_wdt.base + IMX2_WDT_WCR);
\t__raw_writew(val, imx2_wdt.base + IMX2_WDT_WCR);

\tdev_info(&pdev->dev, "KO1WDT suspend: WDE cleared, wcr=%04x\\n", val);
\treturn 0;
}

static int imx2_wdt_resume(struct platform_device *pdev)
{
\tu16 val;

\tif (!test_bit(IMX2_WDT_STATUS_STARTED, &imx2_wdt.status))
\t\treturn 0;

\t/* Re-programs timeout and sets WDE again. */
\timx2_wdt_setup();
\timx2_wdt_ping();
\tmod_timer(&imx2_wdt.timer, jiffies + imx2_wdt.timeout * HZ / 4);

\tval = __raw_readw(imx2_wdt.base + IMX2_WDT_WCR);
\tdev_info(&pdev->dev, "KO1WDT resume: wcr=%04x timeout=%us\\n",
\t\t val, imx2_wdt.timeout);
\treturn 0;
}

static struct platform_driver imx2_wdt_driver = {
\t.remove\t\t= __exit_p(imx2_wdt_remove),
\t.suspend\t= imx2_wdt_suspend,
\t.resume\t\t= imx2_wdt_resume,
\t.shutdown\t= imx2_wdt_shutdown,'''

# --------------------------------------------------------------------------
# [2] board file: print the latched reset reason
# --------------------------------------------------------------------------
RST_MARK = "KO1RST"
RST_BLOCK = r'''
/* ===================== KO1 v26: why did we boot? ===================== *
 * cmdline has panic=5, so a panic reboots in five seconds and RAM (including
 * the printk mirror) is gone by the time anyone looks.  SRC_SRSR is latched
 * across the reset and survives: its WDOG bit says the watchdog did it.
 *
 *   0x020D8000 = AIPS1_OFF_BASE_ADDR + SRC
 *              = (ATZ1 0x02000000 + 0x80000) + 0x58000
 * ioremap + iounmap instead of a static mapping so a wrong address can only
 * return NULL here, never fault the boot.
 * ===================================================================== */
#include <linux/io.h>

static int __init ko1_rst_report(void)
{
	void __iomem *src = ioremap(0x020D8000, 0x100);
	u32 srsr, gpr10;

	if (!src) {
		printk(KERN_ERR "KO1RST ioremap failed\n");
		return 0;
	}
	srsr = __raw_readl(src + 0x008);	/* SRC_SRSR  */
	gpr10 = __raw_readl(src + 0x044);	/* SRC_GPR10 */
	iounmap(src);

	printk(KERN_ERR "KO1RST srsr=%08x gpr10=%08x\n", srsr, gpr10);
	return 0;
}
late_initcall(ko1_rst_report);
'''


def apply(check_only: bool = False) -> None:
    w = WDT_C.read_text()
    if "KO1WDT suspend" in w:
        print("imx2_wdt.c already patched")
    elif WDT_ANCHOR not in w:
        raise SystemExit("driver anchor not found in %s" % WDT_C)
    else:
        if check_only:
            print("would patch imx2_wdt.c (suspend/resume)")
        else:
            WDT_C.write_text(w.replace(WDT_ANCHOR, WDT_NEW, 1))
            print("suspend/resume ->", WDT_C)

    g = GPIO_C.read_text()
    if RST_MARK in g:
        print("board file already prints %s" % RST_MARK)
    else:
        if check_only:
            print("would add %s report to the board file" % RST_MARK)
        else:
            body = g if g.endswith("\n") else g + "\n"
            GPIO_C.write_text(body + RST_BLOCK)
            print("reset reason ->", GPIO_C)


def restore() -> None:
    w = WDT_C.read_text()
    if "KO1WDT suspend" in w:
        i = w.find("/* ===================== KO1 v26: survive suspend")
        j = w.find("static struct platform_driver imx2_wdt_driver = {")
        orig = ("static struct platform_driver imx2_wdt_driver = {\n"
                "\t.remove\t\t= __exit_p(imx2_wdt_remove),\n"
                "\t.shutdown\t= imx2_wdt_shutdown,")
        if i < 0 or j < 0:
            raise SystemExit("cannot locate the block to restore")
        WDT_C.write_text(w[:i] + orig + w[j + len(
            "static struct platform_driver imx2_wdt_driver = {\n"
            "\t.remove\t\t= __exit_p(imx2_wdt_remove),\n"
            "\t.suspend\t= imx2_wdt_suspend,\n"
            "\t.resume\t\t= imx2_wdt_resume,\n"
            "\t.shutdown\t= imx2_wdt_shutdown,"):])
        print("imx2_wdt.c restored")
    else:
        print("imx2_wdt.c: nothing to restore")

    g = GPIO_C.read_text()
    if RST_MARK in g:
        i = g.find("\n/* ===================== KO1 v26: why did we boot?")
        if i < 0:
            raise SystemExit("cannot locate the reset block")
        GPIO_C.write_text(g[:i + 1])
        print("board file restored")
    else:
        print("board file: nothing to restore")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    if a.restore:
        restore()
    else:
        apply(check_only=a.check)
