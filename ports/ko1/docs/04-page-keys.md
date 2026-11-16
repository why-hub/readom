# 04 · 两侧实体翻页键

这是本项目里唯一一个**方向一开始就错了**的问题。写这么细是因为：错误方向占掉了十几轮内核编译，
而正确答案一直在原厂系统里，只读就能拿到。

---

## 现象

翻页键没反应。但内核里有个输入设备 `/dev/keypad`（`fsr_keypad` 驱动）注册着，
`/proc/bus/input/devices` 里键位图里有 `KEY_PAGEUP` / `KEY_PAGEDOWN`，看起来"驱动是对的、只是没使能"。

## 错误方向：按 PW3 的 FSR 方案修

PW3 的侧键是力敏（FSR）方案：一颗 KL05 MCU 挂在 i2c 上，通过 EPDC 的复用引脚通信。沿这条路查到的：

| 项 | 值 |
|---|---|
| MCU | KL05 @ `i2c-2`，地址 `0x58` |
| 引脚 | `EPDC_D8` = `GPIO1_15`（strap）、`EPDC_D9` = `GPIO1_16`（reset）、`EPDC_D10` = `GPIO1_17`（INT） |
| 复用 | `ALT5` = GPIO |
| 同总线另一颗 | `0x59`（drv26xx 触觉驱动） |

驱动里有两道门，两道都关着：

1. `probe` 之后立刻 `fsr_disable_irq()`，只有 `fsr_i2c_poweron()` 里 `kl05_bl_cmd_exit_bl()` 成功才使能；
2. `event_enable` 默认 false，必须用户态 `ioctl` `/dev/keypad` 才会报事件（设备节点是 `root:root 0600`）。

于是编了一版"强开 IRQ + 强开 event_enable + 60 秒重试"的诊断内核。真机结果：

```
12 轮全部: exit_bl ret=-5  ->  kl05_simple_fc_cmd: i2c write failed
```

`-EIO` 在 `drivers/i2c/busses/i2c-imx.c` 里就是 **No ACK**。更关键的是 `0x58` 与 `0x59`
**同时** NAK——不是某颗芯片坏了，是整条支路上的从机全都不应答（或者这一段总线根本没供电）。

继续查，把两个假设都否掉了：

- **"板级没做复用"** → 实测焊盘 mux 本来就是 `00000005`（ALT5），pad 原本是 100k 下拉。不是这回事。
- **"reset 被驱动成低"** → `GPIO1_16` 的 DR=1、GDIR=1（推挽输出高），但 **PSR 读回 0**。
  这恰好是 MCU 没上电时其 ESD 钳位二极管把线拉低的样子。

`/proc/interrupts` 里 `273: 0 GPIO fsr-keypad`，一次都没进过中断；200 秒 `getevent` 窗口里
`event1` 零事件。到这里能下的结论只有：**KO1 上这套方案不成立，别再往下修**。

## 正解：去原厂系统里读

原厂系统的按键是好的，答案就在它的 `/proc` 里。一次只读探测（`ko1-keys-probe.sh`，约 75 秒）拿到：

| 项 | 原厂系统实测 |
|---|---|
| 输入设备 | `Name="gpiokey"`，`gpiokey.0 → input4`，KEY=2100（PAGEUP/PAGEDOWN 位图） |
| 模块 | `gpiokey.ko` + `gpiokey_pgturn.ko`（lab126 "GPIO Button Driver"） |
| GPIO | `gpio-139 (gpiokey-pgdn) in hi`、`gpio-142 (gpiokey-pgup) in hi` |
| 中断 | `395 gpiokey-pgdn`、`398 gpiokey-pgup`，按下时都在涨 |
| 极性 | 0.2 秒间隔的差分采样里每次按键都是 hi→lo，**低有效** |
| 有没有 `fsr_keypad` | **没有**（原厂系统只有 gpiokey / hall / 触屏 / PMIC onkey / 加速度计） |

原厂模块的 vermagic 是 `3.0.35-lab126 preempt mod_unload ARMv7 p2v8`，和我们编的内核不匹配，
不能直接 `insmod`。但可以**反汇编读它的 platform data 表**，映射完全对得上：

```
{gpio 142, irq 398, code 104 KEY_PAGEUP,   debounce 24 ms, active_low 1}
{gpio 139, irq 395, code 109 KEY_PAGEDOWN, debounce 40 ms, active_low 1}
```

### 引脚换算

```
gpio 139 = GPIO5_11 = 焊盘 SD1_DAT0   mux 0x020E0234  pad ctl 0x020E053C
gpio 142 = GPIO5_14 = 焊盘 SD1_CMD    mux 0x020E0230  pad ctl 0x020E0538
```

### 为什么 Android 侧一直没反应

板级的 `gpio5_iomux_config()` **只给 WHISKY / WOODY 板号复用这两根**，KO1 的板号走不到那个分支，
于是这两根焊盘在 Android 内核里从来没人管——这才是"按键没反应"的真因，跟 i2c-2 全 NAK 是两件不相干的事。

顺带一个好消息：这块板的 `usdhc1` 根本没注册（eMMC 在 `mmc1` / `sdhci-esdhc-imx.1`），
SD1 那两组焊盘是空的，可以安全当 GPIO 用。

## 怎么做

在内核板级文件加一个 `late_initcall`（`tools/patch-ko1-gpiokey-v24.py`）：

1. 把 `SD1_DAT0` / `SD1_CMD` 复用成 `ALT5`，pad 配成 `PKE_ENABLED | PUS_22KOHM_PU`；
2. 注册标准的 `gpio-keys` platform device：
   `139 → KEY_PAGEDOWN`、`142 → KEY_PAGEUP`，active-low、20 ms 去抖、可唤醒；
3. input 名字沿用 `gpiokey`，与原厂一致；
4. `.config` 打开 `CONFIG_KEYBOARD_GPIO=y`；
5. 开机打印 mux/pad 实测值，以及 5/20/35/50 秒四次空闲电平采样。

## 踩的坑：`PKE=0` 时上拉根本不接通

第一版（v23）看起来全对：设备注册成功、中断在涨、`dumpsys input` 把 `gpiokey` 当键盘接管。
但实际只出得来一个键：

```
KO1KEY sample 0: pgdn(139)=0  pgup(142)=1      原厂系统两条都是 hi
dumpsys input  : KeyDowns: 2 keys currently down
getevent       : 只有 006d (KEY_PAGEDOWN)，398 一次没涨
```

原因是 pad 控制字里把 **`PKE`（pull/keep enable）写成了 disabled**：

```
v23: PUS_100KOHM_PU | PUE_PULL | PKE_DISABLED     -> 上拉完全不接，引脚悬空
```

**`PKE=0` 时 `PUS` 选的那个上拉电阻根本不会接上。** 悬空的 139 漂到 0，被 `active_low`
解释成"一直按着"，InputReader 认为这个键已经按下，后续每次真实按下都被当重复事件吞掉；
142 漂到 1 反而"看起来正常"，所以只出一条键。

改成 `PKE_ENABLED | PUS_22KOHM_PU`（pad 读回 `0041f070`）后一次通过。

> 这条值得单独记住：**pad 配了上拉不等于上拉生效**，还要看 `PKE`。
> 判断方法是看空闲电平稳不稳，而不是看 mux/pad 寄存器有没有写进去。

## 真机结果

```
KO1KEY v24 idle pgdn=1(all_hi=1) pgup=1(all_hi=1) -> active_low 1/1
KO1KEYev code=104 raw=0 al=1 state=1     上键按下
KO1KEYev code=104 raw=1 al=1 state=0     上键松开
KO1KEYev code=109 raw=0 al=1 state=1     下键按下
KO1KEYev code=109 raw=1 al=1 state=0     下键松开
```

- 4 次空闲采样（5/20/35/50 秒）两条线恒为 1；
- 395 / 398 各 4 次中断，与"按下 + 松开"一一对应；
- `Generic.kl` 里 `key 104 PAGE_UP` / `key 109 PAGE_DOWN`；
- `dumpsys input` 的 `KeyDowns` 归零（不再卡键）；
- 阅读器里实测可以翻页。

## 教训

1. **驱动起来了 ≠ 引脚通了。** 输入设备存在、中断在涨，都可能建立在一根悬空线上。
2. **中断计数增长不能证明极性。** 悬空线也会触发中断。
3. **原厂系统的只读数据比内核试错快一个数量级。** 这台机器能 root、能读 `/proc` 和 debugfs，
   凡是"按键/传感器/模块"这类问题，先去原厂系统问，别先改内核。
