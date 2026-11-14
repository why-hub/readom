# The private page table policy: why one constant cannot cover two layouts

> 2026-11-14, stage A2. This records a **mechanism** change: the handoff
> module's private TTBR0 table now picks its fill rule per model. "Verified"
> below means checks run on this host; neither a device nor QEMU has run it.
> The upstream reasoning is in section 6 of
> `docs/design/porting-to-other-models.en.md`.

## The contract

`identity_limit` is "the upper bound of the 1:1 region in the table the handoff
will run under", not `TASK_SIZE`. Both layouts still satisfy the
`identity_limit > boot_ram_base` check in `pw3_plan.c`; that check rejects a
plan that was written wrong, not a class of machine.

## Two policies

| Policy | When it applies | How the private table is filled |
|---|---|---|
| `PWS_PGD_BUILD_WINDOW` | `PAGE_OFFSET != PHYS_OFFSET` (3G/1G) | Clear `[0, identity_limit)`, write the control section, copy the live table above the limit word for word |
| `PWS_PGD_COPY_LIVE` | `PAGE_OFFSET == PHYS_OFFSET` (2G/2G) | Copy all 4096 sections from the live table; clear none |

The profile decides: `build_module.py` emits `TARGET_PGD_POLICY` into
`target-lock.h` only when a profile explicitly sets `pgd_policy`. The PW3
profile does not, so its `target-lock.h`, its 4096 table entries and every
existing assertion stay **byte for byte unchanged**.

One pure function, `pws_private_pgd_word()`, is shared by all three callers:
`prepare()` in `stage.c` writes the table, `audit_plan()` recomputes every
section independently, and `native-handoff.h` fills the native backend's
table. The auditor never reads the writer's intermediate state.

## Why duet cannot just change a number in its config

Under 2G/2G, `PAGE_OFFSET` and `PHYS_OFFSET` are both `0x80000000`, so the
kernel's lowmem mapping is already 1:1 and no separate window has to be built.
But the module area sits at `0x7F000000`-`0x80000000`
(`MODULES_VADDR = PAGE_OFFSET - 16 MiB`), inside the range the old code
cleared unconditionally. Clear it and the instruction after the TTBR0 write
has no mapping.

The existing `BUILD_BUG_ON(TARGET_IDENTITY_LIMIT > MODULES_VADDR)` would reject
duet's `0xA0000000` bound, and that was **deliberate**: it stops someone from
widening the bound to get around the clearing. The assertion now applies only
to `BUILD_WINDOW`; under `COPY_LIVE` the whole live table is kept, there is no
"clear below the bound" step, and the assertion no longer applies.

```c
if (policy == PWS_PGD_COPY_LIVE || index >= identity_sections)
    return live[index];
```

## Verified and not verified

- Verified: the upstream full test suite passes; `test-private-pgd.c` compares
  all 4096 entries under both policies and reverse-pins the fact that the old
  policy with a duet bound clears the module area; breaking the policy branch
  on purpose makes the test fail and restoring it makes it pass again.
- Verified: the PW3 profile, its generated `target-lock.h` and its table
  entries are unchanged.
- Partly verified: `pw3_first_kernel.ko` was really built twice, against the
  PW3 kernel tree (3G/1G, `BUILD_WINDOW`, 33,244 B) and against the duet kernel
  tree (2G/2G, `COPY_LIVE`, 32,972 B). Building `BUILD_WINDOW` into the 2G/2G
  tree is rejected by the existing `BUILD_BUG_ON`, as designed.
  **Still unverified**: any duet device and the QEMU 2G test kernel; section 6
  recommends running the "copy the live table" path on QEMU vexpress first.
- Not verified: the duet profile itself. Its four `stock_functions` body hashes
  have to be sampled from the target device's `/proc/kallsyms`; those data are
  not on this host. Therefore **this patch creates no duet profile** and no
  code claiming to boot that model.

This follows `CONTRIBUTING.md`: a mechanism with tests may be contributed, but
no path is written in advance for a machine nobody has run.
