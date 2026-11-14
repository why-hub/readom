/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef PWS_PRIVATE_PGD_H
#define PWS_PRIVATE_PGD_H
#include "pw3_plan.h"

/* The 4096 ARM section entries of the private TTBR0 table are filled one of
 * two ways. This is a fact about the kernel's memory split, not about the
 * payload, so it belongs to the profile that names the machine.
 *
 * BUILD_WINDOW: PAGE_OFFSET != PHYS_OFFSET. The live lowmem mapping is not
 *   1:1, so clear [0, identity_limit), write the one control section as an
 *   identity mapping, and copy the kernel/module mappings above the limit.
 * COPY_LIVE: PAGE_OFFSET == PHYS_OFFSET. Live lowmem is already 1:1 and the
 *   module area is below identity_limit (MODULES_VADDR = PAGE_OFFSET-16 MiB).
 *   Copy every word from the live table. Clearing anything would unmap the
 *   instruction that follows the TTBR0 write.
 *
 * docs/design/porting-to-other-models.md section 6 explains why a 2G/2G
 * machine takes the second policy; docs/design/private-pgd-policy.md records
 * what has and has not been run. */
#define PWS_PGD_BUILD_WINDOW 1
#define PWS_PGD_COPY_LIVE 2

/* Pure. 'live' is the running kernel's PGD; index is a 1 MiB section number.
 * identity_sections is identity_limit >> 20 and control_section is the section
 * holding the control page. Under COPY_LIVE the last three arguments describe
 * a mapping we do not need to make, and are deliberately ignored. */
static inline pw_u32 pws_private_pgd_word(pw_u32 policy, pw_u32 index,
        pw_u32 identity_sections, pw_u32 control_section,
        pw_u32 control_phys, pw_u32 identity_attrs, const pw_u32 *live)
{
    if (policy == PWS_PGD_COPY_LIVE || index >= identity_sections)
        return live[index];
    if (index == control_section)
        return (control_phys & 0xfff00000u) | identity_attrs;
    return 0;
}
#endif
