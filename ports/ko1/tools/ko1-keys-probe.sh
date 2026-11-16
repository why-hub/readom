#!/bin/sh
# Name: KO1 Keys Probe
#
# 在原系统（stock Kindle OS）里只读探测"两个侧边翻页键"到底由谁负责：
# 哪个驱动、哪个 input 设备、哪些 GPIO / 中断、走不走 i2c。
#
# 为什么在原系统里问：原系统的按键是好的，答案就在它的 /proc 和 /sys 里，
# 比我们在 Android 内核里盲改板级代码再重启一轮要快得多。Android 侧现在
# 看到的是 i2c-2 上 0x58(KL05) 与 0x59(drv26xx) 同时 NAK、IRQ 273 计数 0，
# 这既可能是"芯片没供电"，也可能是"KO1 压根不用这颗 FSR 方案"，本脚本
# 就是去分辨这两种情况。
#
# 只读：唯一写入是 /mnt/us/ko1-probe/keys/ 下的文本文件。
# 时长：约 75 秒，其中第 10~70 秒是按键采样窗口（屏幕会提示）。
#
# 用法：KUAL → 「⑮ 按键探测」。点完立刻按左右两个翻页键，各按 5~8 次，
#       每次按住约半秒；屏幕上出现 "DONE" 后停止。

PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH
export PATH

OUTDIR=/mnt/us/ko1-probe/keys
OUT=$OUTDIR/keys-probe.txt
GPIO_CHG=$OUTDIR/gpio-changes.txt
KALL=$OUTDIR/kallsyms.txt
mkdir -p "$OUTDIR" 2>/dev/null
: > "$OUT"
exec >> "$OUT" 2>&1

EIPS=""
for c in /usr/sbin/eips /usr/bin/eips /sbin/eips /bin/eips; do
    [ -x "$c" ] && { EIPS="$c"; break; }
done
say() { [ -n "$EIPS" ] && $EIPS top 2 "$1" >/dev/null 2>&1; }

say "keys probe: reading system state ..."
echo "================ $(date) ================"

echo
echo "########## [1] identity ##########"
id
uname -a
cat /proc/version
cut -d' ' -f1 /proc/uptime

echo
echo "########## [2] input devices ##########"
cat /proc/bus/input/devices

echo
echo "########## [3] input class ##########"
ls /sys/class/input/ 2>/dev/null
for d in /sys/class/input/input*; do
    [ -d "$d" ] || continue
    echo "--- $d"
    echo "name : $(cat $d/name 2>/dev/null)"
    echo "phys : $(cat $d/phys 2>/dev/null)"
    echo "ev   : $(cat $d/capabilities/ev 2>/dev/null)"
    echo "key  : $(cat $d/capabilities/key 2>/dev/null)"
    echo "sw   : $(cat $d/capabilities/sw 2>/dev/null)"
    echo "abs  : $(cat $d/capabilities/abs 2>/dev/null)"
done

echo
echo "########## [4] modules ##########"
cat /proc/modules 2>/dev/null
echo "--- module dir"
ls -l /lib/modules/$(uname -r)/kernel/drivers/input/ 2>/dev/null
ls -l /lib/modules/$(uname -r)/kernel/drivers/input/keyboard/ 2>/dev/null
ls -l /lib/modules/$(uname -r)/kernel/drivers/input/misc/ 2>/dev/null

echo
echo "########## [5] kallsyms (filtered) ##########"
grep -iE 'kl05|fsr|keypad|gpio_keys|button|wario_fsr|duet_|hall|drv26|cyttsp' \
     /proc/kallsyms 2>/dev/null | head -120
echo "--- full dump"
cat /proc/kallsyms > "$KALL" 2>/dev/null
wc -l "$KALL" 2>/dev/null

echo
echo "########## [6] interrupts ##########"
cat /proc/interrupts

echo
echo "########## [7] kernel messages (key related) ##########"
( dmesg 2>/dev/null || cat /var/log/messages 2>/dev/null ) | \
    grep -iE 'key|button|fsr|kl05|gpio|input|i2c|hall' | tail -80

echo
echo "########## [8] kernel config ##########"
if [ -r /proc/config.gz ]; then
    zcat /proc/config.gz 2>/dev/null | grep -iE 'FSR|KEYBOARD|GPIO_KEY|INPUT_EVDEV|I2C_CHAR|DEBUG_FS' 
else
    echo "(no /proc/config.gz)"
fi

echo
echo "########## [9] i2c inventory ##########"
ls /sys/bus/i2c/devices/ 2>/dev/null
for d in /sys/bus/i2c/devices/*-*; do
    [ -e "$d/name" ] || continue
    echo "$(basename $d) -> $(cat $d/name 2>/dev/null)"
done
echo "--- /dev/i2c-*"
ls -l /dev/i2c-* 2>/dev/null
for t in i2cdetect i2cget i2cset; do
    command -v $t >/dev/null 2>&1 && echo "tool: $t = $(command -v $t)"
done

echo
echo "########## [10] gpio state + press window ##########"
if ! grep -q ' /sys/kernel/debug ' /proc/mounts 2>/dev/null; then
    mount -t debugfs none /sys/kernel/debug 2>/dev/null
fi
if [ -r /sys/kernel/debug/gpio ]; then
    echo "--- gpio dump (before)"
    cat /sys/kernel/debug/gpio
else
    echo "(no /sys/kernel/debug/gpio)"
fi

: > "$GPIO_CHG"
PREV=$OUTDIR/.gpio.prev
CUR=$OUTDIR/.gpio.cur
cat /sys/kernel/debug/gpio > "$PREV" 2>/dev/null
IRQ_PREV=$OUTDIR/.irq.prev
IRQ_CUR=$OUTDIR/.irq.cur
grep -iE 'gpio|key|i2c|fsr' /proc/interrupts > "$IRQ_PREV" 2>/dev/null

# 有的 busybox 没有小数 sleep，检测不到就退化成 1 秒一次
STEP=1
N=60
if sleep 0.2 2>/dev/null; then
    STEP=0.2
    N=300
fi
echo "--- poll step=$STEP n=$N"

same() { 
    if command -v cmp >/dev/null 2>&1; then cmp -s "$1" "$2"; 
    else [ "$(md5sum < "$1")" = "$(md5sum < "$2")" ]; fi
}

say "PRESS THE PAGE BUTTONS NOW (~60 s)"
echo "--- press window starts $(date)"

i=0
while [ $i -lt $N ]; do
    sleep $STEP
    if [ -r /sys/kernel/debug/gpio ]; then
        cat /sys/kernel/debug/gpio > "$CUR" 2>/dev/null
        if ! same "$PREV" "$CUR"; then
            echo "=== gpio t=$i/$N $(date) ===" >> "$GPIO_CHG"
            diff "$PREV" "$CUR" >> "$GPIO_CHG" 2>/dev/null
            cp -f "$CUR" "$PREV"
        fi
    fi
    grep -iE 'gpio|key|i2c|fsr' /proc/interrupts > "$IRQ_CUR" 2>/dev/null
    if ! same "$IRQ_PREV" "$IRQ_CUR"; then
        echo "=== irq  t=$i/$N $(date) ===" >> "$GPIO_CHG"
        diff "$IRQ_PREV" "$IRQ_CUR" >> "$GPIO_CHG" 2>/dev/null
        cp -f "$IRQ_CUR" "$IRQ_PREV"
    fi
    i=$((i + 1))
done

echo "--- press window ends $(date)"
say "keys probe: DONE"

echo
echo "########## [11] after press window ##########"
if [ -r /sys/kernel/debug/gpio ]; then
    echo "--- gpio dump (after)"
    cat /sys/kernel/debug/gpio
fi
echo "--- gpio changes: $(wc -l < $GPIO_CHG 2>/dev/null) lines"
echo "--- interrupts diff (whole window)"
grep -iE 'gpio|key|i2c|fsr' /proc/interrupts > "$IRQ_CUR" 2>/dev/null
diff "$IRQ_PREV" "$IRQ_CUR" 2>/dev/null
echo "--- interrupts (after)"
cat /proc/interrupts
echo "--- kernel messages (after)"
( dmesg 2>/dev/null || cat /var/log/messages 2>/dev/null ) | tail -40

rm -f "$PREV" "$CUR" "$IRQ_PREV" "$IRQ_CUR"
echo "########## DONE $(date) ##########"
say "keys probe: DONE - plug USB, read ko1-probe/keys/"
