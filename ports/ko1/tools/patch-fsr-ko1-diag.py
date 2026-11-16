#!/usr/bin/env python3
"""Make the FSR keypad driver self-diagnosing and self-sufficient.

Why
---
On the stock Kindle the two bezel page buttons are force-sensing (FSR) keys on
i2c 0x58.  In this Android port the driver is now compiled in and probes, but:

  * probe requests the button IRQ and then immediately *disables* it
    (fsr_disable_irq after request_threaded_irq).  It is only re-enabled inside
    fsr_i2c_poweron() after kl05_bl_cmd_exit_bl() returns success.  Measured on
    v18: /proc/interrupts shows "fsr-keypad" with a count of 0 even while the
    user presses both buttons, so either power-on failed (IRQ still masked) or
    the MCU never drives INT.

  * event_enable defaults to false and is only set by a userspace ioctl on
    /dev/keypad (minor 167, root-only).  Amazon's stock system ships a daemon
    that does this; Android does not, so even a working IRQ would produce no
    input events.

  * CONFIG_LOG_BUF_SHIFT=15 means the kernel ring buffer is 32 KiB, so every
    boot-time printk from this driver is already evicted by the time adb is up.

What this patch does
--------------------
  [1] CONFIG_LOG_BUF_SHIFT 15 -> 18 (256 KiB) so boot messages survive.
  [2] poweron: log the exit_bl and wait_for_poweron return codes.
  [3] probe: set event_enable = true (no userspace enabler on this port) and
      enable the IRQ unconditionally, so a press is visible in
      /proc/interrupts even when the power-on handshake failed.
  [4] a delayed work prints a KO1FSR status line every 60 s (15 times) and
      retries fsr_i2c_poweron() while the controller is not ON.  The prints
      land in the live kernel buffer, which logcat -b kernel can read without
      root.

Usage
-----
    patch-fsr-ko1-diag.py                 # apply (idempotent)
    patch-fsr-ko1-diag.py --check         # report only
    patch-fsr-ko1-diag.py --restore       # revert to the original text
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC = Path("/home/builder/pw3-build/linux-android-core/drivers/input/keyboard/fsr_keypad.c")
CONFIG = Path("/home/builder/pw3-build/out-android-core-duet/.config")

MARK = "KO1FSR"

# --- [2] poweron instrumentation -------------------------------------------
POWERON_ANCHOR = """		kl05_hw_reset(keypad);
		ret = kl05_bl_cmd_exit_bl(keypad->i2c_fsr);
"""
POWERON_NEW = """		kl05_hw_reset(keypad);
		ret = kl05_bl_cmd_exit_bl(keypad->i2c_fsr);
		printk(KERN_ERR "KO1FSR poweron: exit_bl ret=%d\\n", ret);
"""

WAIT_ANCHOR = """		ret = wait_for_poweron(keypad);
"""
WAIT_NEW = """		ret = wait_for_poweron(keypad);
		printk(KERN_ERR "KO1FSR poweron: wait ret=%d ps=%d\\n", ret, keypad->power_state);
"""

# --- [4] diagnostics worker -------------------------------------------------
DIAG_ANCHOR = """	keypad->irq_enabled = false;
	disable_irq_nosync(keypad->irq);
}
"""
DIAG_NEW = """	keypad->irq_enabled = false;
	disable_irq_nosync(keypad->irq);
}

/*
 * KO1 port: the stock system has a userspace daemon that powers the FSR
 * controller up and enables event propagation.  Android has neither, and the
 * probe-time handshake cannot be observed (32 KiB ring buffer), so report the
 * state from a delayed work instead: the lines land in the live buffer, which
 * logcat -b kernel can read without root.
 */
static void fsr_ko1_diag_work(struct work_struct *work);
static DECLARE_DELAYED_WORK(fsr_ko1_diag_wq, fsr_ko1_diag_work);

static void fsr_ko1_diag_work(struct work_struct *work)
{
	static int rounds;
	int ret = 0;

	printk(KERN_ERR "KO1FSR diag: round=%d ps=%d mode=%d irq=%d irq_en=%d ev_en=%d i2c=%p\\n",
	       rounds, g_keypad.power_state, g_keypad.device_mode, g_keypad.irq,
	       (int)g_keypad.irq_enabled, (int)g_keypad.event_enable,
	       g_keypad.i2c_fsr);

	if (g_keypad.i2c_fsr && g_keypad.power_state != FSR_POWERSTATE_ON) {
		ret = fsr_i2c_poweron(&g_keypad);
		printk(KERN_ERR "KO1FSR diag: poweron retry ret=%d ps=%d mode=%d\\n",
		       ret, g_keypad.power_state, g_keypad.device_mode);
	}

	fsr_enable_irq(&g_keypad);
	printk(KERN_ERR "KO1FSR diag: irq_en=%d app_ver=%02x%02x\\n",
	       (int)g_keypad.irq_enabled,
	       g_keypad.static_config.app_ver[0], g_keypad.static_config.app_ver[1]);

	if (++rounds < 15)
		schedule_delayed_work(&fsr_ko1_diag_wq, msecs_to_jiffies(60000));
}
"""

# --- [3] probe: events on, IRQ on, start the reporter -----------------------
ENABLE_ANCHOR = "\tg_keypad.event_enable = false;\n"
ENABLE_NEW = ("\t/* KO1: no userspace enabler exists on this port, so let the driver\n"
              "\t * propagate key events on its own. */\n"
              "\tg_keypad.event_enable = true;\n")

PROBE_ANCHOR = """	printk(KERN_INFO "Done Registering FSR platform device\\n");

	return 0;
"""
PROBE_NEW = """	printk(KERN_INFO "Done Registering FSR platform device\\n");

	/*
	 * KO1: enable the button IRQ even if the power-on handshake above failed.
	 * A masked line makes "no interrupts" indistinguishable from "MCU silent",
	 * and /proc/interrupts is readable from an unrooted shell.
	 */
	fsr_enable_irq(&g_keypad);
	printk(KERN_ERR "KO1FSR probe: ps=%d mode=%d irq=%d irq_en=%d ev_en=%d\\n",
	       g_keypad.power_state, g_keypad.device_mode, g_keypad.irq,
	       (int)g_keypad.irq_enabled, (int)g_keypad.event_enable);
	schedule_delayed_work(&fsr_ko1_diag_wq, msecs_to_jiffies(20000));

	return 0;
"""

EDITS = [
    ("poweron exit_bl log", POWERON_ANCHOR, POWERON_NEW),
    ("poweron wait log", WAIT_ANCHOR, WAIT_NEW),
    ("diag worker", DIAG_ANCHOR, DIAG_NEW),
    ("event_enable", ENABLE_ANCHOR, ENABLE_NEW),
    ("probe tail", PROBE_ANCHOR, PROBE_NEW),
]


def apply(text: str) -> tuple[str, list[str]]:
    done: list[str] = []
    for name, old, new in EDITS:
        if new in text:
            done.append(f"{name}: already applied")
            continue
        if text.count(old) != 1:
            raise SystemExit(f"ABORT: anchor for {name!r} matched {text.count(old)} times")
        text = text.replace(old, new, 1)
        done.append(f"{name}: applied")
    return text, done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    text = SRC.read_text()
    cfg = CONFIG.read_text()

    if args.restore:
        for name, old, new in EDITS:
            if new in text:
                text = text.replace(new, old, 1)
        SRC.write_text(text)
        print("restored driver source (config untouched)")
        return 0

    new_text, notes = apply(text)
    for n in notes:
        print(" ", n)

    shift = "CONFIG_LOG_BUF_SHIFT=18"
    cfg_old = "CONFIG_LOG_BUF_SHIFT=15"
    need_cfg = shift not in cfg

    print(f"  log buffer: {'15 -> 18' if need_cfg else 'already 18'}")
    print(f"  source changed: {new_text != text}")

    if args.check:
        return 0

    if new_text != text:
        SRC.write_text(new_text)
    if need_cfg:
        if cfg_old not in cfg:
            raise SystemExit("ABORT: cannot find CONFIG_LOG_BUF_SHIFT=15")
        CONFIG.write_text(cfg.replace(cfg_old, shift, 1))
    print("patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
