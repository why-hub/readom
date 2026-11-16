#!/usr/bin/env python3
"""Raise the ARM mmap randomization ceiling to 16 bits for the 2G/2G duet kernel.

Android 8.1's init runs a builtin `set_mmap_rnd_bits` action and treats a
failure as a security failure -> panic("rebooting to bootloader"):

    init: Unable to set minimum option value 16 in /proc/sys/vm/mmap_rnd_bits
    init: Unable to set adequate mmap entropy value!
    init: Security failure...

arch/arm/Kconfig caps ARCH_MMAP_RND_BITS_MAX at 15 when PAGE_OFFSET=0x80000000
(2G/2G), while the PW3 build (3G/1G, PAGE_OFFSET=0xC0000000) gets 16. Raising the
ceiling is safe here: arch_pick_mmap_layout() only adds
get_random_int() & ((1 << bits) - 1) pages on top of TASK_UNMAPPED_BASE, i.e. at
most 256 MiB below a ~0x2A000000 base with TASK_SIZE 0x7F000000.
"""

import pathlib
import sys

SRC = pathlib.Path('/home/builder/pw3-build/linux-android-core')
OUT = pathlib.Path('/home/builder/pw3-build/out-android-core-duet')

KCONFIG = SRC / 'arch/arm/Kconfig'
OLD = "config ARCH_MMAP_RND_BITS_MAX\n\tdefault 14 if PAGE_OFFSET=0x40000000\n\tdefault 15 if PAGE_OFFSET=0x80000000\n"
NEW = ("config ARCH_MMAP_RND_BITS_MAX\n\tdefault 14 if PAGE_OFFSET=0x40000000\n"
       "\t# 2G/2G kernels still have room for 16 bits; Android 8.1 init refuses\n"
       "\t# anything less and panics with \"Security failure\".\n"
       "\tdefault 16 if PAGE_OFFSET=0x80000000\n")


def patch_kconfig():
    s = KCONFIG.read_text()
    if NEW in s:
        print('arch/arm/Kconfig: already patched')
        return
    if OLD not in s:
        sys.exit('arch/arm/Kconfig: anchor not found')
    KCONFIG.write_text(s.replace(OLD, NEW, 1))
    print('arch/arm/Kconfig: ARCH_MMAP_RND_BITS_MAX 15 -> 16 for PAGE_OFFSET=0x80000000')


def patch_config():
    p = OUT / '.config'
    lines = p.read_text().splitlines()
    want = {'CONFIG_ARCH_MMAP_RND_BITS': 'CONFIG_ARCH_MMAP_RND_BITS=16'}
    out, seen = [], set()
    for line in lines:
        key = line.split('=', 1)[0]
        if key in want:
            out.append(want[key])
            seen.add(key)
        else:
            out.append(line)
    for key, val in want.items():
        if key not in seen:
            sys.exit('%s: missing from .config' % key)
    p.write_text('\n'.join(out) + '\n')
    print('config: CONFIG_ARCH_MMAP_RND_BITS=16')


if __name__ == '__main__':
    patch_kconfig()
    patch_config()
