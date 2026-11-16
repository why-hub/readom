# 01 · Wi-Fi（Broadcom 4343W / `dhd`）

PW3 用 ath6kl，KO1 用 Broadcom 4343W。两者除了"都叫 Wi-Fi"之外没有共同点，所以上游那套 PW3 校验
在这台机器上处处不成立。最终查到的是**三个问题叠在一起**，必须全部修掉才能用。

---

## 现象

Wi-Fi 磁贴点了会弹回、`wifi_on` 被拉回 0。逐层往下查，依次露出三个独立的失败点：

### 一、固件文件读不了 → 芯片从未启动

```
avc: denied { read } for name="fw_bcmdhd_sr.bin"
     scontext=u:r:kernel:s0  tcontext=u:object_r:unlabeled:s0
_dhdsdio_download_firmware: dongle image file download failed
dhd_bus_start failed -> dhdsdio_probe failed
```

原因是早期取证阶段对 `system.img` 里几个 vendor 文件跑过 `ea_rm <file> security.selinux`，
本来是想"清掉继承来的标签让系统重新标"，结果把标签彻底清掉了，文件变成 `unlabeled`。
内核（`u:r:kernel`）读不了，Broadcom dongle 从来没被下过固件。

**`wlan0` 那时候是存在的**，但只是一个 DOWN 状态的空壳、没有可用 MAC，于是 wificond 报
`Failed to get interface mac address` / `No usable interface found`，磁贴自然打不开。
这条最容易被误判成"驱动问题"，实际是文件标签问题。

修法：把这些文件补回 `u:object_r:vendor_file:s0`（`tools/patch-firmware-selinux.py`，纯 ext4 扩展属性操作）。

### 二、没有 `wlan0`，只有 `eth0`

`dhd` 在这台机器上挂在 `mmc3:0001:2`，默认生成 `eth0` + `p2p0`，而 Android 侧 `wifi.interface=wlan0`。

两条路：内核里 rename，或者让驱动自己按 `iface_name` 建。rename 试过能用，但会和 `dev_change_name`
的时序打架；最后改成**在驱动侧直接把 netdev 命名为 `wlan0`**，一次到位、MAC 也是真的。

### 三、wificond 硬校验 PW3 的驱动名

`wificond` 的 `pw3_radio.cpp` 在 `createClientInterface` 前验三件事：

1. `stat("/sys/module/ath6kl_sdio")` 是目录；
2. `stat("/sys/class/net/wlan0")` 且 `if_nametoindex("wlan0")` 成功；
3. `readlink("/sys/class/net/wlan0/phy80211/device/driver")` 的 basename `strcmp` 等于 `ath6kl_sdio`。

第 3 条在 KO1 上**永远不可能成立**——sysfs 链接指向 `dhd` 的 SDIO 驱动。
（1）和（2）可以真的满足：从 initramfs 加载一个空的 `ath6kl_sdio.ko` 存根，再让驱动建出 `wlan0`。

修法：只改第 3 条那个 `strcmp` 调用点（`tools/patch-wificond-ko1-driver.py`）。

修完仍有 15 秒一次的 `PW3 radio module transition did not reach verified state`，
`ReadModuleState()` / `ReadInterfaceState()` 在 `wifi` 域里返回 false 且从 `shell` 域验不了，
于是把 `ReachedState()` 改成恒报已达（`tools/patch-wificond-radio-state.py`）。
这是绕过而不是修好，**如果谁找到干净的判定方式，这一层应该被替掉**。

---

## 还有两个前置条件

### 原厂 vendor 模块得能加载

原厂 `dhd.ko` / `falcon.ko` 的 vermagic 是

```
3.0.35-lab126 preempt mod_unload ARMv7 p2v8
```

我们编的内核带 `-lab126-pw3-android81`，vermagic 不匹配，加载直接被拒。
`tools/patch-allow-foreign-vendor-modules.py` 放开这一处校验。
静态门禁（vermagic + 185/185 个符号）都过了，**运行时 ABI 只能靠真机证明**——真机 3/3 加载成功，
但这只证明"能加载"，不能外推成"所有调用点都安全"。

### `mmap_rnd_bits`：一条走错的弯路

Android 8.1 的 `init` 有个内置动作要求 `mmap_rnd_bits >= 16`，达不到就：

```
init: Unable to set minimum option value 16 in /proc/sys/vm/mmap_rnd_bits
init: Security failure...
```

2G/2G 下 `PAGE_OFFSET=0x80000000`，`arch/arm/Kconfig` 把 `ARCH_MMAP_RND_BITS_MAX` 限在 15，
PW3（3G/1G，`PAGE_OFFSET=0xC0000000`）才有 16。

**先试的是把它设成 8**（想减少地址空间随机化带来的碎片），结果开机被 init 写回 16，白改一场；
而且低于 16 本身就是 init 判定的失败条件。正解是把 Kconfig 的上限提到 16：
`arch_pick_mmap_layout()` 只是在 `TASK_UNMAPPED_BASE` 之上加 `get_random_int() & ((1<<bits)-1)` 页，
16 位在这个布局下是安全的（`tools/patch-kernel-mmap-rnd.py`）。

---

## 真机结果

- `dhd` 起来，芯片完成固件下载，`wlan0` 有真实 MAC；
- 能扫描、能关联；
- 磁贴不再弹回，`wifi_on` 保持 1；
- `wpa_supplicant` / `wificond` 不再刷错误日志。

## 未做

- 唤醒后的 Wi-Fi 恢复没单独验过（和深度睡眠一起）。
- `p2p0` 保留着但没使用。
- 上面第三条 wificond 的绕过是二进制补丁，**换一版 wificond 就要重打**。
