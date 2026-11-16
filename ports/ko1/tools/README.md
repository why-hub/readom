# 工具

这些脚本是逐项修复时实际用的版本，保留是为了让结论可复现，不是可直接安装的成品。
它们按本机的路径与镜像布局写成，**复用前先读脚本头部的说明**：

- 大部分接受 `--source` / `--out`，或者直接对 Kindle 用户分区上的镜像文件操作；
- 凡是改 `system.img` 的，都只改字节、不重打包，改完必须重 pin preinit 里的哈希；
- 凡是改内核的，都是对内核源码树的定点替换，改完要重编 `zImage`。

## 内核侧（重编内核才生效）

| 脚本 | 作用 |
|---|---|
| `patch-kernel-mmap-rnd.py` | 把 2G/2G 下被 Kconfig 限在 15 的 `ARCH_MMAP_RND_BITS_MAX` 提到 16（Android 8.1 `init` 要求 ≥16，否则判 `Security failure`） |
| `patch-allow-foreign-vendor-modules.py` | 放开 vermagic 校验，让原厂 `dhd.ko` / `falcon.ko` 能加载 |
| `patch-printk-mirror-clamp.py` | 把 printk 的物理内存镜像限在 32 KiB 窗口内（否则 `0xA0000000` 越界，启动即复位） |
| `patch-ko1-gpiokey-v24.py` | 板级把 `SD1_DAT0` / `SD1_CMD` 复用为 GPIO，注册 `gpio-keys`；含 `PKE_ENABLED` 修正与极性自检 |

## `system.img` 侧（字节级改写）

| 脚本 | 作用 |
|---|---|
| `patch-firmware-selinux.py` | 给被误删标签的 vendor 固件/模块文件补回 `u:object_r:vendor_file:s0` |
| `patch-wificond-ko1-driver.py` | 绕开 wificond 对 `dhd` 驱动名的 `strcmp` 校验 |
| `patch-wificond-radio-state.py` | 让 `ReachedState()` 恒报已达（绕过，不是修好；换 wificond 要重打） |
| `patch-healthd-ups-flag.py` | healthd 的 type 表里把 `UPS` 从 AC 位里拿掉 |
| `patch-charger-single-mains.py` | 只让真正的插线检测节点报 `Mains`，附变化时才打印的诊断 |
| `patch-buildprop-heapsize.py` | `dalvik.vm.heapsize` 256m → 192m |
| `patch-buildprop-model.py` | 型号名两条属性改 `Kindle Oasis 1`，用相邻注释抵消长度 |

## 诊断（不修东西，只留证据）

| 脚本 | 作用 |
|---|---|
| `patch-ko1-busscan-gpiowatch.py` | 四总线扫描 + 5 个 GPIO bank 变化监视 + 复位线极性自测 |
| `patch-fsr-ko1-diag.py` | FSR/KL05 那条死路上的诊断：强开 IRQ 与 `event_enable`、60 秒重试、打印寄存器 |

## 设备侧（在 Kindle 上跑的只读脚本）

| 脚本 | 作用 |
|---|---|
| `ko1-keys-probe.sh` | 原厂系统只读取证，约 75 秒：输入设备、模块、GPIO 状态、0.2 秒差分采样、中断表。**翻页键就是靠它定案的** |
| `ko1-dump-trace.sh` | 读内核镜像到固定物理地址的那两段（阶段字 + 日志），用来查"启动失败在哪一步" |

## 两个脚本被脱敏过

`patch-wificond-radio-state.py` 与设备侧脚本里原本出现的设备 MAC 已替换成 `xx:xx:xx:xx:xx:xx`。
除了这个占位，代码与当初运行的版本一致。
