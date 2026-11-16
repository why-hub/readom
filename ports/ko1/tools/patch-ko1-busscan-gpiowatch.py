#!/usr/bin/env python3
"""KO1 v22 instrumentation: bus sweep + GPIO watch + reset-line drive proof.

Why: on v21 the whole i2c-2 bus showed no ACK at all (the drv26xx haptics at
0x59 failed in the very same millisecond as the KL05 at 0x58), so "KL05 is
unhappy" is the wrong model -- either the bus never reaches those chips on KO1
hardware, or the chips are unpowered.  At the same time nothing proves the
page buttons are even wired to EPDC_D8/D9/D10 on this model, because the only
board support in this tree is wario (PW3) and the board id here does not match.

So this build answers it by measurement, not by assumption:

  1. sweep i2c-0..3 for every address 0x08..0x77 with a one byte read and log
     every ACK -- if KL05 lives on another bus we see it;
  2. snapshot every GPIO bank and then watch all five for 15 minutes at 200 ms,
     logging each bit that changes -- if a page button toggles any SoC pin we
     see exactly which one;
  3. drive the reset line low, sample PSR, drive it high, sample PSR -- a pad
     that cannot produce both levels is not connected to a powered rail.
"""
from __future__ import annotations

import argparse
from pathlib import Path

GPIO_C = Path("/home/builder/pw3-build/linux-android-core/arch/arm/mach-mx6/mx6_wario_gpio.c")
FSR_C = Path("/home/builder/pw3-build/linux-android-core/drivers/input/keyboard/fsr_keypad.c")

MARK = "KO1FSR watch start"

# ------------------------------------------------------------------ board file
BOARD_ANCHOR = "void wario_fsr_init_pins(void)\n{\n"

BOARD_BLOCK = r'''#include <linux/workqueue.h>

/*
 * KO1 v22 probes.  All of this has to live here: the board file is the only
 * place with IO_ADDRESS() and the iomux register macros, and this build is a
 * production image, so the kernel is the only thing that can look at them.
 */
#define KO1FSR_GPIO_REG_DR   0x00
#define KO1FSR_GPIO_REG_GDIR 0x04
#define KO1FSR_GPIO_REG_PSR  0x08
#define KO1FSR_GPIO_REG_IMR  0x14
#define KO1FSR_GPIO_REG_ISR  0x18

static const u32 ko1fsr_bank_base[5] = {
	0x0209C000,	/* GPIO1 */
	0x020A0000,	/* GPIO2 */
	0x020A4000,	/* GPIO3 */
	0x020A8000,	/* GPIO4 */
	0x020AC000,	/* GPIO5 */
};

/* the pads the KL05 sits on, from the PW3 board file that this tree carries */
void fsr_ko1_pin_probe(void)
{
	void __iomem *g = (void __iomem *)IO_ADDRESS(KO1FSR_GPIO1_BASE);
	u32 lo, hi;

	printk(KERN_ERR "KO1FSR i2c2 pads scl mux=%08x pad=%08x sda mux=%08x pad=%08x\n",
	       __raw_readl(IOMUXC_SW_MUX_CTL_PAD_I2C2_SCL),
	       __raw_readl(IOMUXC_SW_PAD_CTL_PAD_I2C2_SCL),
	       __raw_readl(IOMUXC_SW_MUX_CTL_PAD_I2C2_SDA),
	       __raw_readl(IOMUXC_SW_PAD_CTL_PAD_I2C2_SDA));

	gpio_direction_output(MX6SL_ARM2_EPDC_SDDO_9, 0);
	mdelay(2);
	lo = __raw_readl(g + KO1FSR_GPIO_REG_PSR);
	gpio_direction_output(MX6SL_ARM2_EPDC_SDDO_9, 1);
	mdelay(2);
	hi = __raw_readl(g + KO1FSR_GPIO_REG_PSR);

	printk(KERN_ERR "KO1FSR reset drive lo=%08x hi=%08x dr=%08x gdir=%08x\n",
	       lo, hi, __raw_readl(g + KO1FSR_GPIO_REG_DR),
	       __raw_readl(g + KO1FSR_GPIO_REG_GDIR));
}
EXPORT_SYMBOL(fsr_ko1_pin_probe);

void fsr_ko1_gpio_dump(const char *tag)
{
	int b;

	for (b = 0; b < 5; b++) {
		void __iomem *g = (void __iomem *)IO_ADDRESS(ko1fsr_bank_base[b]);

		printk(KERN_ERR "KO1FSR gpio%d %-4s psr=%08x gdir=%08x dr=%08x imr=%08x isr=%08x\n",
		       b + 1, tag,
		       __raw_readl(g + KO1FSR_GPIO_REG_PSR),
		       __raw_readl(g + KO1FSR_GPIO_REG_GDIR),
		       __raw_readl(g + KO1FSR_GPIO_REG_DR),
		       __raw_readl(g + KO1FSR_GPIO_REG_IMR),
		       __raw_readl(g + KO1FSR_GPIO_REG_ISR));
	}
}
EXPORT_SYMBOL(fsr_ko1_gpio_dump);

static u32 ko1fsr_prev_psr[5];
static int ko1fsr_watch_left;
static int ko1fsr_watch_lines;
static int ko1fsr_watch_tick;
static struct delayed_work fsr_ko1_watch_wq;

static void fsr_ko1_watch_work(struct work_struct *work)
{
	int b, bit;

	for (b = 0; b < 5; b++) {
		void __iomem *g = (void __iomem *)IO_ADDRESS(ko1fsr_bank_base[b]);
		u32 now = __raw_readl(g + KO1FSR_GPIO_REG_PSR);
		u32 diff = now ^ ko1fsr_prev_psr[b];

		if (!diff)
			continue;

		for (bit = 0; bit < 32; bit++) {
			if (!(diff & (1u << bit)))
				continue;
			if (ko1fsr_watch_lines < 160)
				printk(KERN_ERR "KO1FSR watch tick=%d gpio%d_%d %d->%d\n",
				       ko1fsr_watch_tick, b + 1, bit,
				       (int)((ko1fsr_prev_psr[b] >> bit) & 1),
				       (int)((now >> bit) & 1));
			ko1fsr_watch_lines++;
		}
		ko1fsr_prev_psr[b] = now;
	}

	ko1fsr_watch_tick++;
	if (--ko1fsr_watch_left > 0)
		schedule_delayed_work(&fsr_ko1_watch_wq, msecs_to_jiffies(200));
	else
		printk(KERN_ERR "KO1FSR watch done ticks=%d lines=%d\n",
		       ko1fsr_watch_tick, ko1fsr_watch_lines);
}

void fsr_ko1_gpio_watch_start(int ticks)
{
	int b;

	for (b = 0; b < 5; b++) {
		void __iomem *g = (void __iomem *)IO_ADDRESS(ko1fsr_bank_base[b]);

		ko1fsr_prev_psr[b] = __raw_readl(g + KO1FSR_GPIO_REG_PSR);
	}
	ko1fsr_watch_tick = 0;
	ko1fsr_watch_lines = 0;
	ko1fsr_watch_left = ticks;
	printk(KERN_ERR "KO1FSR watch start ticks=%d\n", ticks);
	INIT_DELAYED_WORK(&fsr_ko1_watch_wq, fsr_ko1_watch_work);
	schedule_delayed_work(&fsr_ko1_watch_wq, msecs_to_jiffies(200));
}
EXPORT_SYMBOL(fsr_ko1_gpio_watch_start);

'''

# ------------------------------------------------------------------- driver
FSR_EXTERN_ANCHOR = "extern void fsr_ko1_hw_dump(const char *tag);\n"
FSR_EXTERN_NEW = ("extern void fsr_ko1_hw_dump(const char *tag);\n"
                  "extern void fsr_ko1_pin_probe(void);\n"
                  "extern void fsr_ko1_gpio_dump(const char *tag);\n"
                  "extern void fsr_ko1_gpio_watch_start(int ticks);\n")

FSR_DECL_ANCHOR = "static void fsr_ko1_diag_work(struct work_struct *work);\n"

FSR_SCAN = r'''/*
 * KO1 v22: sweep every adapter for every 7 bit address with a single byte
 * read.  No write phase, so an idle device only sees its own address being
 * clocked; a reply means "something acked here".  This is how we find out
 * whether the KL05 is on another bus than the one wario's board file assumes.
 */
static void fsr_ko1_bus_scan(void)
{
	int nr, addr, found, total = 0;

	for (nr = 0; nr < 4; nr++) {
		struct i2c_adapter *adap = i2c_get_adapter(nr);

		if (!adap) {
			printk(KERN_ERR "KO1FSR scan i2c-%d: no adapter\n", nr);
			continue;
		}

		found = 0;
		for (addr = 0x08; addr <= 0x77; addr++) {
			u8 byte = 0;
			struct i2c_msg msg;

			msg.addr = addr;
			msg.flags = I2C_M_RD;
			msg.len = 1;
			msg.buf = &byte;

			if (i2c_transfer(adap, &msg, 1) == 1) {
				printk(KERN_ERR "KO1FSR scan i2c-%d ACK 0x%02x byte=%02x\n",
				       nr, addr, byte);
				found++;
			}
		}
		printk(KERN_ERR "KO1FSR scan i2c-%d found=%d\n", nr, found);
		total += found;
		i2c_put_adapter(adap);
	}
	printk(KERN_ERR "KO1FSR scan total=%d\n", total);
}

static void fsr_ko1_diag_work(struct work_struct *work);
'''

FSR_PROBE_ANCHOR = """	fsr_ko1_hw_dump("probe");
	schedule_delayed_work(&fsr_ko1_diag_wq, msecs_to_jiffies(20000));
"""
FSR_PROBE_NEW = """	fsr_ko1_hw_dump("probe");
	fsr_ko1_pin_probe();
	fsr_ko1_gpio_dump("boot");
	/* 15 minutes of 200 ms sampling: press the buttons whenever you like */
	fsr_ko1_gpio_watch_start(4500);
	schedule_delayed_work(&fsr_ko1_diag_wq, msecs_to_jiffies(20000));
"""

FSR_WORK_ANCHOR = """	fsr_ko1_hw_dump("diag");
	fsr_ko1_bus_probe(g_keypad.i2c_fsr ? g_keypad.i2c_fsr->adapter : NULL);

	if (++rounds < 15)
"""
FSR_WORK_NEW = """	fsr_ko1_hw_dump("diag");

	if (rounds == 0)
		fsr_ko1_bus_scan();

	if (++rounds < 15)
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    board = GPIO_C.read_text()
    driver = FSR_C.read_text()

    if args.restore:
        if MARK in board:
            start = board.index("/*\n * KO1 v22 probes.")
            end = board.index("void wario_fsr_init_pins(void)\n{\n")
            board = board[:start] + board[end:]
            GPIO_C.write_text(board)
            print("  board file: probes removed")
        driver = driver.replace(FSR_EXTERN_NEW, FSR_EXTERN_ANCHOR, 1)
        driver = driver.replace(FSR_SCAN, FSR_DECL_ANCHOR, 1)
        driver = driver.replace(FSR_PROBE_NEW, FSR_PROBE_ANCHOR, 1)
        driver = driver.replace(FSR_WORK_NEW, FSR_WORK_ANCHOR, 1)
        FSR_C.write_text(driver)
        print("  driver: instrumentation removed")
        return 0

    if MARK in board:
        print("  board file: already patched")
    else:
        if board.count(BOARD_ANCHOR) != 1:
            raise SystemExit("ABORT: board anchor not unique")
        board = board.replace(BOARD_ANCHOR, BOARD_BLOCK + BOARD_ANCHOR, 1)
        print("  board file: probes inserted")
        if not args.check:
            GPIO_C.write_text(board)

    changed = False
    if "extern void fsr_ko1_pin_probe" not in driver:
        if driver.count(FSR_EXTERN_ANCHOR) != 1:
            raise SystemExit("ABORT: extern anchor not unique")
        driver = driver.replace(FSR_EXTERN_ANCHOR, FSR_EXTERN_NEW, 1)
        print("  driver: externs inserted")
        changed = True

    if "fsr_ko1_bus_scan" not in driver:
        if driver.count(FSR_DECL_ANCHOR) != 1:
            raise SystemExit("ABORT: diag decl anchor not unique")
        driver = driver.replace(FSR_DECL_ANCHOR, FSR_SCAN, 1)
        print("  driver: bus scan inserted")
        changed = True

    if "fsr_ko1_pin_probe();" not in driver:
        if driver.count(FSR_PROBE_ANCHOR) != 1:
            raise SystemExit("ABORT: probe anchor not unique")
        driver = driver.replace(FSR_PROBE_ANCHOR, FSR_PROBE_NEW, 1)
        print("  driver: probe hooks inserted")
        changed = True

    if "fsr_ko1_bus_scan();" not in driver:
        if driver.count(FSR_WORK_ANCHOR) != 1:
            raise SystemExit("ABORT: diag work anchor not unique")
        driver = driver.replace(FSR_WORK_ANCHOR, FSR_WORK_NEW, 1)
        print("  driver: diag hook inserted")
        changed = True

    if changed and not args.check:
        FSR_C.write_text(driver)

    print("  check only" if args.check else "patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
