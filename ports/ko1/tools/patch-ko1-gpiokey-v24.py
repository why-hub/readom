#!/usr/bin/env python3
"""KO1 page keys v24: fix the pad pull, auto-detect polarity, log every event.

What v23 got wrong
------------------
v23 muxed SD1_DAT0 / SD1_CMD to ALT5 and registered gpio-keys, and the driver
did come up (input "gpiokey", irqs 395/398 counting on every press).  But the
pad control word it wrote had PKE (pull/keep enable) *disabled*:

    v23: PUS_100KOHM_PU | PUE_PULL | PKE_DISABLED   -> no pull at all, pin floats

Result on the real device:

    KO1KEY sample 0: pgdn(139)=0 pgup(142)=1      (stock OS: both "in hi")
    dumpsys input   : KeyDowns: 2 keys currently down
    getevent        : only 006d (KEY_PAGEDOWN), no 0068 (KEY_PAGEUP)

A floating input explains both: 139 sat at 0, which active_low turned into a
permanent key-down, so Android swallowed every later press of the same key.

What v24 changes
----------------
  [1] pad word: PKE_ENABLED + PUS_22KOHM_PU (strong enough to beat any light
      external pull-down), everything else unchanged.
  [2] polarity is sampled at boot instead of assumed: two reads 60 ms apart,
      idle high -> active_low = 1, idle low -> active_low = 0.  The choice is
      printed, so the next log tells us the truth either way.
  [3] gpio_keys.c gets a one-line printk per reported event:
          KO1KEY ev code=109 raw=0 al=1 state=1
      raw = pin level, al = active_low chosen, state = what Android receives.
  [4] four idle samples at 5/20/35/50 s so we can see the line settle.

Usage
-----
    patch-ko1-gpiokey-v24.py            # apply (idempotent)
    patch-ko1-gpiokey-v24.py --check
"""
from __future__ import annotations

import argparse
from pathlib import Path

SRC = Path("/home/builder/pw3-build/linux-android-core")
GPIO_C = SRC / "arch/arm/mach-mx6/mx6_wario_gpio.c"
KEYS_C = SRC / "drivers/input/keyboard/gpio_keys.c"

BLOCK_HEAD = "\n/* ===================== KO1 gpio page keys"
MARK = "KO1KEY v24"
EV_MARK = "KO1KEYev"

BLOCK = r'''
/* ===================== KO1 gpio page keys (v24) ===================== *
 * Stock OS (measured 2026-11-16 22:46 via the KUAL probe):
 *     Name="gpiokey"   KEY=2100 (PAGEUP/PAGEDOWN)
 *     gpio-139 (gpiokey-pgdn) in hi     gpio-142 (gpiokey-pgup) in hi
 *     395 GPIO gpiokey-pgdn / 398 GPIO gpiokey-pgup, both counting
 * i.e. the two side keys are plain active-low GPIO buttons on GPIO5_11 and
 * GPIO5_14 = pads SD1_DAT0 / SD1_CMD, not the KL05 force sensor.
 *
 * v23 muxed and registered them but wrote PKE_DISABLED in the pad word, which
 * turns the pull resistor off entirely; the pins floated (139 read 0 at idle,
 * 142 read 1) and Android saw a key held down forever.  v24 enables the pull,
 * uses 22k for margin, and picks active_low from the measured idle level.
 * ==================================================================== */
#include <linux/delay.h>

#define KO1KEY_PAD_IN ((LVE_ENABLED & 0x1) << 22 | (HYS_ENABLED & 0x1) << 16 | \
		       (PUS_22KOHM_PU & 0x3) << 14 | (PUE_PULL & 0x1) << 13 | \
		       (PKE_ENABLED & 0x1) << 12 | (ODE_DISABLED & 0x1) << 11 | \
		       (SPD_50MHZ & 0x3) << 6 | (DSE_40OHM & 0x7) << 3 | \
		       (SRE_SLOW & 0x1))

static struct gpio_keys_button ko1_gpiokeys[] = {
	{
		.code			= KEY_PAGEDOWN,
		.gpio			= 139,	/* GPIO5_11, pad SD1_DAT0 */
		.active_low		= 1,
		.desc			= "gpiokey-pgdn",
		.type			= EV_KEY,
		.wakeup			= 1,
		.debounce_interval	= 20,
	},
	{
		.code			= KEY_PAGEUP,
		.gpio			= 142,	/* GPIO5_14, pad SD1_CMD */
		.active_low		= 1,
		.desc			= "gpiokey-pgup",
		.type			= EV_KEY,
		.wakeup			= 1,
		.debounce_interval	= 20,
	},
};

static struct gpio_keys_platform_data ko1_gpiokeys_pdata = {
	.buttons	= ko1_gpiokeys,
	.nbuttons	= ARRAY_SIZE(ko1_gpiokeys),
	.name		= "gpiokey",
};

static struct platform_device ko1_gpiokeys_dev = {
	.name	= "gpio-keys",
	.id	= -1,
	.dev	= {
		.platform_data	= &ko1_gpiokeys_pdata,
	},
};

static struct delayed_work ko1_key_sample_wq;
static int ko1_key_sample_n;

static void ko1_gpiokey_sample(struct work_struct *work)
{
	printk(KERN_ERR "KO1KEY v24 sample %d: pgdn(139)=%d pgup(142)=%d\n",
	       ko1_key_sample_n, gpio_get_value(139), gpio_get_value(142));
	if (++ko1_key_sample_n < 4)
		schedule_delayed_work(&ko1_key_sample_wq, 15 * HZ);
}

static int __init ko1_gpiokey_init(void)
{
	int a, b, i, va, vb;

	__raw_writel((SION_DISABLED & 0x1) << 4 | (ALT5 & 0x7),
		     IOMUXC_SW_MUX_CTL_PAD_SD1_DAT0);
	__raw_writel(KO1KEY_PAD_IN, IOMUXC_SW_PAD_CTL_PAD_SD1_DAT0);
	__raw_writel((SION_DISABLED & 0x1) << 4 | (ALT5 & 0x7),
		     IOMUXC_SW_MUX_CTL_PAD_SD1_CMD);
	__raw_writel(KO1KEY_PAD_IN, IOMUXC_SW_PAD_CTL_PAD_SD1_CMD);

	printk(KERN_ERR "KO1KEY v24 mux dat0 mux=%08x pad=%08x cmd mux=%08x pad=%08x want=%08x\n",
	       __raw_readl(IOMUXC_SW_MUX_CTL_PAD_SD1_DAT0),
	       __raw_readl(IOMUXC_SW_PAD_CTL_PAD_SD1_DAT0),
	       __raw_readl(IOMUXC_SW_MUX_CTL_PAD_SD1_CMD),
	       __raw_readl(IOMUXC_SW_PAD_CTL_PAD_SD1_CMD),
	       KO1KEY_PAD_IN);

	/* let the pad charge before believing the level */
	msleep(20);
	for (i = 0, a = 1, b = 1; i < 3; i++) {
		msleep(20);
		a &= gpio_get_value(139) ? 1 : 0;
		b &= gpio_get_value(142) ? 1 : 0;
	}
	va = gpio_get_value(139) ? 1 : 0;
	vb = gpio_get_value(142) ? 1 : 0;
	/* idle high -> active-low button; idle low -> active-high (report it) */
	ko1_gpiokeys[0].active_low = a ? 1 : 0;
	ko1_gpiokeys[1].active_low = b ? 1 : 0;
	printk(KERN_ERR "KO1KEY v24 idle pgdn=%d(all_hi=%d) pgup=%d(all_hi=%d) -> active_low %d/%d\n",
	       va, a, vb, b, ko1_gpiokeys[0].active_low, ko1_gpiokeys[1].active_low);

	if (platform_device_register(&ko1_gpiokeys_dev))
		printk(KERN_ERR "KO1KEY v24 platform_device_register failed\n");

	INIT_DELAYED_WORK(&ko1_key_sample_wq, ko1_gpiokey_sample);
	schedule_delayed_work(&ko1_key_sample_wq, 5 * HZ);
	return 0;
}
late_initcall(ko1_gpiokey_init);
'''

EV_OLD = """	int state = (gpio_get_value_cansleep(button->gpio) ? 1 : 0) ^ button->active_low;
"""
EV_NEW = """	int raw = gpio_get_value_cansleep(button->gpio) ? 1 : 0;
	int state = raw ^ button->active_low;

	/* KO1KEYev: what the pin says vs what Android gets */
	printk(KERN_ERR "KO1KEYev code=%d raw=%d active_low=%d state=%d\\n",
	       button->code, raw, button->active_low, !!state);
"""


def apply(check_only: bool = False) -> None:
    text = GPIO_C.read_text()
    idx = text.find(BLOCK_HEAD)
    if idx >= 0:
        text = text[:idx] + ("" if text[:idx].endswith("\n") else "\n")
    if not text.endswith("\n"):
        text += "\n"
    text += BLOCK
    if check_only:
        print("would rewrite board block (%d bytes)" % len(BLOCK))
    else:
        GPIO_C.write_text(text)
        print("board block ->", GPIO_C)

    k = KEYS_C.read_text()
    if "KO1KEYev" in k:
        print("gpio_keys.c already logs events")
    elif EV_OLD not in k:
        raise SystemExit("report_event anchor not found in %s" % KEYS_C)
    else:
        if check_only:
            print("would add event logging to gpio_keys.c")
        else:
            KEYS_C.write_text(k.replace(EV_OLD, EV_NEW, 1))
            print("event logging ->", KEYS_C)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    apply(check_only=a.check)
