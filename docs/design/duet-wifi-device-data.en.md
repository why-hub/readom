# Oasis 1 ("duet") Wi-Fi: what the device actually has

> Measured 2026-11-14 on a Kindle Oasis 1 running stock 5.16.2.1.1.
> This note separates **measured** facts from **still unverified** ones; an
> inference is never written as a conclusion. It belongs to route A in section 7
> of the porting design and to rule 2 of CONTRIBUTING.md: device-private data is
> read from the user's own device at start time, never stored here or shipped.

## Measured

### Four files in the stock system

Found by mounting that device's stock p1 partition image read-only and cross-
checking against the on-device survey report. These paths and sizes describe the
model, not the individual unit; no contents, hashes, NVRAM MAC or serial are in
the repository.

| Role | Stock path | Bytes | Path in the start's cpio |
|---|---|---:|---|
| NVRAM calibration | `/opt/brcm_4343w/wifi/nvram/nvram.txt` | 906 | `firmware/image/brcm_4343w/nvram.txt` |
| Runtime firmware | `/opt/brcm_4343w/wifi/firmware/fw_bcmdhd_sr.bin` | 357387 | `firmware/image/brcm_4343w/fw_bcmdhd_sr.bin` |
| FullMAC driver | `/lib/modules/3.0.35-lab126/extra/dhd.ko` | 603828 | `firmware/image/brcm_4343w/modules/dhd.ko` |
| Helper module | `/lib/modules/3.0.35-lab126/extra/falcon.ko` | 34392 | `firmware/image/brcm_4343w/modules/falcon.ko` |

The same tree also holds an MFG firmware (374673 bytes) and Bluetooth `.hcd`
files; a start does not need them and does not read them. `dhd.ko`'s `.modinfo`
exposes `firmware_path` and `nvram_path`, defaulting to the two stock paths
above; `modules.alias` has the `sdio:c00v*d* dhd` catch-all for this chip.

### Stock kernel identity (for `dd_device_matches`)

```
/proc/cmdline : no_console_suspend rootwait log_pos_goal=0x8fff0000 console=ttymxc0,115200 root=/dev/mmcblk0p1 ro ip=off quiet video=mxcepdcfb:E60,bpp=8,x_mem=3M
/proc/version : Linux version 3.0.35-lab126 (builder@eink-builds) (gcc version 4.9.1 (GCC) ) #6 PREEMPT Tue Aug 1 12:57:50 UTC 2023
user FAT      : /mnt/base-us
```

Only two things differ from PW3: the boot loader adds `no_console_suspend`, and
the stock kernel is build `#6 ... 12:57:50` (PW3's is `#8 ... 12:49:59`).
`/proc/cpuinfo` still names `Freescale i.MX 6SoloLite based Wario Board`:
duet shares `board-mx6sl_wario.c` with wario and branches at runtime through
`lab126_board_is()`. That last point comes from the source and from the duet
`vmlinux` built here, **not** from reading the device's `/proc/cpuinfo`, so it
is recorded as source-level evidence.

### The two static gates of route A

- A kernel built from this project's duet config exports **185/185** of the
  factory `dhd.ko`'s undefined symbols (the PW3 comparison is 184/185; the
  missing `in_falcon` comes from duet's built-in board code).
- After rebuilding with `CONFIG_LOCALVERSION="-lab126"`, module vermagic is
  **byte-identical** to the factory `3.0.35-lab126 preempt mod_unload ARMv7 p2v8 `
  (including the trailing space).
- On the device, `insmod` of three modules built here (`crc-ccitt`, `nls_ascii`,
  `nls_utf8`) succeeded **3/3**, `rmmod` 3/3, with no `version magic` or
  `Unknown symbol` in `dmesg`. This is the **reverse** experiment: it shows the
  cross toolchain's output is acceptable to the stock kernel, not that the
  factory modules run under the new kernel.

## Still unverified (do not present these as conclusions)

1. **Runtime ABI**: the factory modules were built for the stock kernel, and
   this one carries Android patches. Matching vermagic and symbols does not
   guarantee matching structure layouts or function semantics. Needs `insmod`
   and `dmesg` on a device.
2. **Load branch**: the launcher's vendor-module branch was removed unused
   before v0.1.0 (2026-10-29) and is not restored here. This patch only reads
   the four files into the cpio; it adds no loading logic.
3. **Module parameters**: `firmware_path` / `nvram_path` must point at the
   expanded cpio paths; those are not yet settled.
4. **Patches 0310 / 1045**: both describe ath6kl facts (firmware policy and
   waiting for SDIO card detection) and need rewriting for BCM4343W.
5. **Handover page table**: on 2G/2G the private table must copy the live one
   and keep the module area (section 6); neither is implemented or verified.
6. **Wi-Fi itself**: associating, scanning and resume behaviour are untested.

Following the project's rule against writing a path no machine has run, this
series lands only the **measured** part: reading and archiving the device data,
plus the per-model parameterisation that makes it testable. The other six items
are left for whoever has a target device.

## What changed

- `boot/devicedata/devicedata.h`: the two hard-coded Wi-Fi paths in `dd_profile`
  become a per-model plan (calibration size, cpio paths, directory list,
  factory-module list read at start time, waveform path).
- `boot/devicedata/devicedata.c`: PW3 behaviour is unchanged; `dd_duet_5_16_2_1_1`
  is added. `dd_archive` now builds directories and writes firmware, NVRAM and
  each factory module from the plan. The calibration size check uses the
  profile's measured value (906 for duet) and names both values on failure.
- `boot/devicedata/tests/devicedata_test.c`: verifies duet identity and six cpio
  entries on a made-up device, with two reverse checks (PW3's build number must
  be refused; a 905-byte calibration must be refused).
- No device-private data enters the repository: paths and sizes only, no
  contents, hashes, MAC, serial or board ID.
