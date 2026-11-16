# Kindle Oasis 1（KO1 / duet）移植记录

这台机器已经能跑起 Android 8.1，Wi-Fi、充电指示、微信读书、两侧实体翻页键都能用。
本文记录它是怎么做到的、哪些结论有真机证据、哪些还没有，以及别人换一台同样的机器要重做哪几步。

**这不是一条可以直接合进上游的移植。** 上游 `CONTRIBUTING.md` 要求真机结果来自仓库自己的构建流程；
这里的做法是在原厂系统之上重新打包 `system.img`、用 KUAL 脚本交接进新内核，属于手工重打包路线，
所以资料放在 fork 的 `ports/` 下而不是走 PR。可上游化的部分是上游补丁系列，见
[`upstream-patches/`](upstream-patches/SERIES.md)：五个补丁，全部有本机测试与反向验证，与本文的设备结论互相独立。

---

## 机器

| 项 | 值 |
|---|---|
| 型号 | Kindle Oasis 1（2016，亚马逊第 8 代；上游 `duet.config` 也写 8th generation） |
| 板级代号 | `duet` / `juno`，machine id `4091` |
| 固件 | 5.16.2.1.1（`002-juno_16020101_whisky_duet-409755`） |
| SoC | i.MX6SL（Cortex-A9） |
| 屏幕 | EPDC，E60 面板，`bpp=8` |
| Wi-Fi | Broadcom BCM4343W，SDIO，`dhd` + `falcon`（**不是** PW3 的 ath6kl） |
| 原厂内核 | `3.0.35-lab126`（`#6 PREEMPT Tue Aug 1 12:57:50 UTC 2023`），与 PW3 / Voyage 同一内核版本 |
| 内存布局 | 2G/2G（`PAGE_OFFSET=0x80000000`），交接页表用 `COPY_LIVE` |

内核版本与 PW3 相同，是上游 #3 里"离得最近"的一档；但 Wi-Fi 芯片、实体按键方案、充电节点全都不同，
所以下面每一项都得单独查。

---

## 当前状态

| 项 | 状态 | 真机证据 |
|---|---|---|
| 进 Android 8.1 桌面 | 可用 | 多次启动，`init` 走到 `sys.boot_completed=1` |
| 从 Android 回原厂系统 | 可用 | 长按电源重启即回；不写分区，无刷机 |
| 显示 | 能用，刷新慢 | 面板点亮、灰度正常；拖动的延迟来自 EPDC 刷屏，见 [06](docs/06-touch-display.md) |
| 触摸 | 精确，跟随延迟 | 120 条事件按帧还原：16 ms 中位间隔、无丢帧、坐标全屏准确 |
| Wi-Fi | 可用 | `dhd` 起来、`wlan0` 有真实 MAC、能扫描关联，见 [01](docs/01-wifi.md) |
| 充电指示 | 可用 | 插拔线时图标随状态变化，见 [02](docs/02-charging.md) |
| 微信读书 | 可用 | 连续存活 11 分钟以上，能进书架与阅读页，见 [03](docs/03-weread-heapsize.md) |
| 两侧翻页键 | 可用 | 上/下键各验 4 次，`KEY_PAGEUP` / `KEY_PAGEDOWN` 完整按下+松开，见 [04](docs/04-page-keys.md) |
| 设备型号 | 已部署，待确认 | 两条属性改成 `Kindle Oasis 1`，见 [05](docs/05-model-name.md) |
| 深度睡眠 / 唤醒 | 未验证 | 会上报，但没有完整走一轮 |
| 蓝牙 / 音频 / 商店 | 未验证 | 不在本阶段范围 |

---

## 路线：为什么不写分区

原厂系统本身能 root、能 `dd`、能用 `debugfs` 只读访问分区，于是整条链路都建立在"不写分区"上：

1. `documents/Readom-Duet.sh` 从 Kindle 用户分区（`/mnt/us`，USB 上是 G 盘）读取离线载荷，
   在内存里展开新内核与 initramfs，通过交接模块把原厂系统切到新内核；
2. 新内核挂载重新打包过的 `system.img`（放在用户分区上，是文件而不是分区）；
3. 出任何问题，长按电源重启就回原厂系统——没有回退流程，因为什么都没写进去。

代价是：每次改 `system.img` 都要把它从用户分区读出、改、写回，`system.img` 的 SHA-256 还被钉在
preinit 里做一致性校验（防止挂错镜像）。好处是失败成本为零，这对一台没有 fastboot 逃生口的机器很关键。

启动链路的细节、取证通道（production build 下 `logcat -b kernel` 是唯一的内核日志出口）见
[`docs/boot-chain.md`](docs/boot-chain.md)。

---

## 每一项修复

| 文档 | 内容 | 一句话根因 |
|---|---|---|
| [01](docs/01-wifi.md) | Wi-Fi | 三个问题叠加：wificond 硬校验 PW3 的 `ath6kl_sdio`、只生成 `eth0` 没有 `wlan0`、固件文件 SELinux 标签被误删导致芯片从未启动 |
| [02](docs/02-charging.md) | 充电图标 | 三个 power_supply 节点都自称 `Mains`，而 battery boost 被报成 `UPS` 又被 healthd 折进 AC 位 |
| [03](docs/03-weread-heapsize.md) | 微信读书闪退 | 2G/2G 下 ART 的 CC region space 要一整块连续 256 MiB，拿不到；降到 192m 解决 |
| [04](docs/04-page-keys.md) | 两侧翻页键 | 不是 FSR/KL05 方案，是普通 GPIO 按键（`GPIO5_11`/`GPIO5_14` = `SD1_DAT0`/`SD1_CMD`），板级只在别的板号复用这两根 |
| [05](docs/05-model-name.md) | 型号名 | `ro.product.model` 是内部代号 `KO1`，`ro.vendor.product.model` 还是 PW3 残留 |
| [06](docs/06-touch-display.md) | 触摸与刷新 | 输入链路干净，延迟在 EPDC；用户态没有调节入口 |

顺带一条负面结论也写在该写的地方：`CONFIG_ARCH_MMAP_RND_BITS=8` 行不通，Android 8.1 的 `init`
开机强制要求 ≥16，低于就判 `Security failure` 直接重启；正确做法是把 2G/2G 下被 Kconfig 限住的
上限提到 16（见 [01](docs/01-wifi.md) 的最后一节，与 Wi-Fi 的地址空间问题同源）。

---

## 目录

```
ports/ko1/
├── README.md                 本文
├── docs/
│   ├── boot-chain.md         启动链路、取证通道、失败时的取证办法
│   ├── 01-wifi.md
│   ├── 02-charging.md
│   ├── 03-weread-heapsize.md
│   ├── 04-page-keys.md       最长的一篇，含 FSR 死路与 GPIO 正解
│   ├── 05-model-name.md
│   └── 06-touch-display.md
├── reports/                  两次只读取证的原始输出（系统清单、模块、GPIO、分区）
├── upstream-patches/         可上游的五个补丁 + 系列说明
└── tools/                    逐项修复用到的脚本，以及一份阅读顺序
```

---

## 复现要点

完整复现需要原厂系统的 root（越狱）、一份这个固件的 GPL 内核源码、以及 `system.img` 的可写副本。
步骤在每份文档的「怎么做」小节里，工具在 `tools/` 下，顺序见 [`tools/README.md`](tools/README.md)。

几条容易踩的：

- **每次改内核必须同时更新 `system.img` 的 pin**，否则 preinit 的一致性校验会拦住启动；表现为直接回原厂系统。
- **增量编译不重新生成 `include/generated/compile.h`**，`uname` 里的 `#编号` 会骗人，判断有没有编进去要看产物哈希或符号。
- **`getevent` 被 `timeout` 杀掉时缓冲区全丢**，文件是 0 字节不代表没有事件；要用 `-c N` 攒够事件让它自己退出。
- **production build 下 `/dev/kmsg` 与 debugfs 都被 SELinux 拒**，内核日志只能走 `logcat -b kernel -d`。

---

## 已知遗留

- 型号名已部署，还差一次真机确认（进「设置 → 设备信息」看是不是 `Kindle Oasis 1`）。
- 深度睡眠/唤醒没有完整验证。
- "跟手"要真正改善就得动显示栈（EPDC 更新模式选择），目前用户态没有开关。
- `system.img` 里 `ro.product.device` / `ro.product.name` / fingerprint 仍是 `pw3`。这是有意的：
  这些是平台名（i.MX6SL + 3.0.35 这个平台就叫 pw3），改了会让 `gralloc.pw3`、`ro.hardware=pw3`、
  `init.pw3*.rc` 一类的东西失配，上游也明确写了"别的机型照用，不改名"。

---

## 许可

本文与同目录脚本按上游同样的许可发布，见仓库根目录 `LICENSE`。
`reports/` 是设备自身输出的原始记录，已确认不含序列号、MAC 地址与网络名。
