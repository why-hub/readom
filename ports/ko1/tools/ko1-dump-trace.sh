#!/bin/sh
# Name: KO1 Trace Dump
#
# 只读：把交接机制留在 RAM 顶部的那几页读回来，判断上一次点「启动 Android」
# 到底走到哪一步。
#
#   phys 0x9FFF0100  transition 做了什么（进入 / 跳转 / halt + 失败码）
#   phys 0x9FFF0200  进来的内核走到哪（0xb17e00XX 阶段字）
#   phys 0x9FFF8000  进来的内核 printk 环的最后 32 KiB
#
# 地址换算（22:11 那次实测定标，别再猜）：
#   stock 内核是 2G/2G，stext 在 0x80008000，DRAM 从 phys 0x80000000 起，
#   所以直接映射是恒等映射：kernel virt == phys。这里原来写的
#   "virt = phys + 0x40000000" 是 3G/1G 的公式，在这台机器上不成立 ——
#   按它去读 0xDFFF0000 落进 vmalloc 区，dd 得到 0 字节（15 日 00:50 那次
#   就是这么失败的）。
#
# 读取窗口从 0x9FFE0000 起 128 KiB，正好盖住三个探针点并直到 RAM 末尾
# 0xA0000000；读到什么都不要紧，脚本自己扫魔数。
#
# 危险点：这三页在 stock 内核里没有 reserve，开机久了会被分配器重用。
# 所以要在点完「启动 Android」、机器自己重启回来之后尽早跑这一项。
#
# 唯一写入是 /mnt/us/ko1-probe/ 下的 trace.bin / trace-dump.txt。

PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH
export PATH

OUTDIR=/mnt/us/ko1-probe
OUT=$OUTDIR/trace-dump.txt
BIN=$OUTDIR/trace.bin
mkdir -p "$OUTDIR" 2>/dev/null
: > "$OUT"
exec >> "$OUT" 2>&1

EIPS=""
for c in /usr/sbin/eips /usr/bin/eips /sbin/eips /bin/eips; do
    [ -x "$c" ] && { EIPS="$c"; break; }
done

screen() {
    [ -z "${KO1_NO_SCREEN:-}" ] || return 0
    [ -n "$EIPS" ] || return 0
    "$EIPS" -c >/dev/null 2>&1 || true
    row=2
    for line in "$@"; do
        "$EIPS" 2 "$row" "$line" >/dev/null 2>&1 || true
        row=$((row + 2))
    done
}

# 0x9FFE0000 / 4096 = 655328；32 块 = 128 KiB
SKIP=655328
COUNT=32

OFF_TW=256      # 0x9FFF0100
OFF_ST=512      # 0x9FFF0200
OFF_RING=32768  # 0x9FFF8000

slice() {  # slice <offset> <len>：用 dd 切，不依赖 od 的 -j
    dd if="$BIN" bs=1 skip="$1" count="$2" 2>/dev/null
}
word() {   # word <offset>：读一个 32bit 字，输出 8 位小写 hex
    slice "$1" 4 | od -An -tx4 2>/dev/null | tr -d ' \n'
}

echo "KO1 TRACE DUMP (identity-map build)"
echo "started: $(date 2>/dev/null)"
echo "uptime:  $(cut -d' ' -f1 /proc/uptime 2>/dev/null)"
echo "kernel:  $(cat /proc/version 2>/dev/null)"
ls -l /dev/kmem 2>&1

# 定标：stext 应是 0x80xxxxxx，才能按恒等映射读
echo "=== calibration ==="
grep -E ' (_stext|stext)$' /proc/kallsyms 2>/dev/null | head -4
echo

if [ ! -r /dev/kmem ]; then
    echo "RESULT no /dev/kmem; nothing dumped"
    screen "KO1 Trace Dump" "没有 /dev/kmem，无法读取" "DONE"
    exit 1
fi

if dd if=/dev/kmem of="$BIN" bs=4096 skip=$SKIP count=$COUNT 2>"$OUTDIR/dd-err.txt"; then
    echo "RESULT dumped $BIN (base 0x9FFE0000 + $COUNT pages)"
else
    echo "RESULT dd failed: $(cat "$OUTDIR/dd-err.txt" 2>/dev/null)"
fi
echo "dd: $(tr '\n' ' ' < "$OUTDIR/dd-err.txt" 2>/dev/null)"
ls -l "$BIN" 2>&1
BYTES=$(wc -c < "$BIN" 2>/dev/null | tr -d ' ')
echo "bytes: ${BYTES:-0}"
echo

if [ "${BYTES:-0}" -lt 4096 ]; then
    echo "RESULT 读不到内容 —— 地址换算或 /dev/kmem 读权限有问题"
    screen "KO1 Trace Dump" "读取 0 字节" "地址 0x9FFF0000 恒等映射" "DONE"
    exit 1
fi

# ---- transition 记录 ----
echo "=== transition @0x9FFF0100 ==="
echo "words:$(slice $OFF_TW 16 | od -An -tx4 2>/dev/null | tr -s ' ')"
TW1=$(word $OFF_TW)
CODE=$(word $((OFF_TW + 4)))
echo "first=$TW1 code=$CODE"
case "$TW1" in
    bc0000a1) echo "VERDICT transition: 进入拷贝循环 (0xa1)";;
    bc0000a3) echo "VERDICT transition: 已跳转到 payload (0xa3)";;
    bc0000ff) echo "VERDICT transition: HALT，失败码=$CODE";;
    00000000) echo "VERDICT transition: 该字为 0 —— RAM 已被覆盖或从未写入";;
    *)        echo "VERDICT transition: 无法识别 ($TW1)，多半是覆盖后的脏数据";;
esac
echo

# ---- 内核阶段字 ----
echo "=== kernel stage @0x9FFF0200 ==="
echo "words:$(slice $OFF_ST 16 | od -An -tx4 2>/dev/null | tr -s ' ')"
ST1=$(word $OFF_ST)
echo "first=$ST1"
case "$ST1" in
    b17e0011) echo "VERDICT kernel: start_kernel";;
    b17e0021) echo "VERDICT kernel: setup_arch";;
    b17e0013) echo "VERDICT kernel: paging_init 完成";;
    b17e0014) echo "VERDICT kernel: setup_arch 结束";;
    b17e0015) echo "VERDICT kernel: rest_init";;
    b17e0016) echo "VERDICT kernel: kernel_init";;
    b17e0017) echo "VERDICT kernel: init_post / PID1";;
    00000000) echo "VERDICT kernel: 全 0 —— 内核没走到这里";;
    *)        echo "VERDICT kernel: 其它 ($ST1)";;
esac
echo

# ---- printk 环里的可打印文字 ----
echo "=== printk ring @0x9FFF8000 (printable runs) ==="
slice $OFF_RING 32768 | tr -c '[:print:]\n' '\n' | grep -E '.{8,}' | tail -60
echo "=== ring end ==="
echo

# ---- 全窗口魔数扫描 ----
echo "=== magic scan (whole 128 KiB window) ==="
od -An -tx4 -v "$BIN" 2>/dev/null | tr -s ' ' '\n' \
    | grep -E '^(bc0000|b17e00)[0-9a-f]{2}$' | sort | uniq -c | sort -rn | head -24
echo "=== scan end ==="

RING_HINT=$(slice $OFF_RING 32768 | tr -c '[:print:]\n' '\n' | grep -E '.{8,}' | tail -2 | tr '\n' '|' | cut -c1-60)
echo "screen-hint: $RING_HINT"
echo "DONE $(date 2>/dev/null)"
sync

screen "KO1 Trace Dump" "tw=$TW1 st=$ST1" "ring:$RING_HINT" "已写 ko1-probe/trace.bin" "DONE"

exit 0
