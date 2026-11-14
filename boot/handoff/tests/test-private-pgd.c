/* SPDX-License-Identifier: GPL-2.0-only */
#include <stdio.h>
#include "private-pgd.h"
#include "identity-attrs.h"

static unsigned checks;
#define CHECK(x) do { ++checks; if(!(x)) { \
    printf("PRIVATE_PGD_FAIL line=%u\n",__LINE__);return 1;} } while(0)

#define SECTIONS 4096u
static pw_u32 live[SECTIONS], table[SECTIONS];

int main(void)
{
    pw_u32 i, window_limit = 0xbf0u, duet_limit = 0xa00u;
    pw_u32 control_phys = 0x81000000u, control_section = control_phys >> 20;
    const pw_u32 attrs = PW3_IDENTITY_SECTION;
    for (i=0;i<SECTIONS;++i) live[i]=(i<<20)|0x40eu;

    CHECK(PWS_PGD_BUILD_WINDOW==1 && PWS_PGD_COPY_LIVE==2);
    /* The window-building layout, word for word what PW3 used to produce. */
    for (i=0;i<SECTIONS;++i)
        table[i]=pws_private_pgd_word(PWS_PGD_BUILD_WINDOW,i,window_limit,
            control_section,control_phys,attrs,live);
    for (i=0;i<SECTIONS;++i) {
        pw_u32 expected = i < window_limit ? 0u : live[i];
        if (i==control_section) expected=(control_phys&0xfff00000u)|attrs;
        CHECK(table[i]==expected);
    }
    CHECK(table[0]==0 && table[0x7f0]==0);
    CHECK(table[control_section]==((control_phys&0xfff00000u)|attrs));
    CHECK(table[window_limit]==live[window_limit]);
    CHECK(table[0xc00]==live[0xc00]);

    /* The 2G/2G layout: nothing is cleared, including the module area and
     * the control section, which the live lowmem map already covers 1:1. */
    for (i=0;i<SECTIONS;++i)
        table[i]=pws_private_pgd_word(PWS_PGD_COPY_LIVE,i,duet_limit,
            control_section,control_phys,attrs,live);
    for (i=0;i<SECTIONS;++i) CHECK(table[i]==live[i]);
    CHECK(table[0x7f0]==live[0x7f0] && table[0x7f0]!=0);
    CHECK(table[control_section]==live[control_section]);
    CHECK(table[0x9f0]==live[0x9f0]);

    /* The mistake this parameterisation exists to prevent. With a duet bound
     * and the old unconditional clearing, the module area goes to zero and the
     * next instruction fetch after the TTBR write has no mapping. The test
     * pins that the old policy really does clear it, so a future refactor
     * cannot quietly make both policies identical. */
    CHECK(pws_private_pgd_word(PWS_PGD_BUILD_WINDOW,0x7f0,duet_limit,
        control_section,control_phys,attrs,live)==0);
    CHECK(pws_private_pgd_word(PWS_PGD_BUILD_WINDOW,0x9f0,duet_limit,
        control_section,control_phys,attrs,live)==0);
    /* An unknown policy must not turn into COPY_LIVE by accident. build_module
     * rejects it at compile time; here it falls back to the window layout. */
    CHECK(pws_private_pgd_word(99,0x7f0,duet_limit,
        control_section,control_phys,attrs,live)==0);

    /* The 2G/2G bound is the end of lowmem, not TASK_SIZE: it is above the
     * physical RAM start, which is what pw3_plan.c's contract check asks. */
    CHECK((duet_limit<<20)==0xa0000000u && 0xa0000000u>0x80000000u);
    CHECK(window_limit<<20==0xbf000000u);

    printf("PRIVATE_PGD_PASS checks=%u\n",checks);
    return 0;
}
