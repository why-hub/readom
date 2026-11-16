# 启动链路与取证通道

这台机器没有 fastboot 逃生口，所以整条链路围绕两件事设计：**不写分区**、**失败能自证**。

---

## 1. 交接：原厂系统 → 新内核

```
原厂系统（5.16.2.1.1，root）
  └─ KUAL → documents/Readom-Duet.sh
       ├─ 从 /mnt/us 读离线载荷（内核 zImage、initramfs、system.img）
       ├─ 在内存里展开，不落盘、不动分区表、不动 bootmode
       ├─ 交接模块把页表、utsname、启动参数按 profile 钉住后跳转
       └─ 新内核 3.0.35-lab126-…-android81（2G/2G，COPY_LIVE 页表）
            ├─ 挂载 /mnt/us/android/system.img（文件，不是分区）
            └─ Android 8.1 init → sys.boot_completed=1
```

几个关键约束：

- **`system.img` 的 SHA-256 钉在 preinit 里。** 每次重建 `system.img`（哪怕只改 `build.prop` 一个字节）
  都必须同步更新这个 pin，否则 preinit 拒绝挂载 → 表现为"点了启动但直接回原厂"。
- **`system.img` 在 Android 侧是只读挂载**，在 Kindle 侧（USB 上的 G 盘）可写。所以改 `system.img`
  必须重启回原厂系统，这一点决定了很多改动的验证节奏。
- **内核与 `system.img` 是两个独立的 pin**：内核在启动脚本里（payload 哈希），`system.img` 在 preinit 里。

## 2. 失败时怎么知道失败在哪

Android 8.1 在原厂机器上是 production build：`/dev/kmsg`、debugfs 全被 SELinux 拒。
失败（尤其"启动即复位"）时唯一能留下的痕迹，是一条自己搭的通道——**内核把环形缓冲镜像到固定物理地址**，
下一次启动（原厂系统）再把它读出来：

```
kernel/printk.c         每个 printk 之后把 ring buffer 尾部复制到 0x9FFF8000
arch/arm/kernel/setup.c memblock_reserve(0x9FFF0000, 0x10000)     /* 64 KiB */
```

原厂系统侧用一个 KUAL 脚本读这两段：`0x9FFF0200` 起是阶段字（走到哪一步），`0x9FFF8000` 起是镜像日志。

**这条通道自己踩过一次坑，值得写下来**：把 `CONFIG_LOG_BUF_SHIFT` 从 17 提到 18（128 KiB → 256 KiB）之后，
`printk` 的镜像循环按 `__LOG_BUF_LEN` 整块复制，而 DRAM 在 `0xA0000000` 结束——

```
0x9FFF8000 + 0x40000 = 0xA0038000   越过 DRAM 顶端 224 KiB
```

结果是 external abort + `panic=5` → **启动即复位，连一行日志都留不下**。修复是把镜像改成只复制最新的
32 KiB 尾部，并在 `0xA0000000` 停止（`tools/patch-printk-mirror-clamp.py`）。教训：
这条通道的容量必须小于它占用的窗口，不能让"记录失败的机制"本身变成失败原因。

## 3. 平时怎么看内核日志

Android 起来之后：

```
adb shell logcat -b kernel -d
```

这是 production build 下唯一的内核日志出口。`/proc/config.gz`、`/proc/interrupts`、
`/proc/bus/input/devices`、`/sys/class/...` 这些可读，`/dev/kmsg` 与 debugfs 不行。

## 4. 原厂系统的只读取证

原厂系统的按键、模块、GPIO 状态都能直接读，这是查清"KO1 到底怎么接"的最快来源——
翻页键就是靠这一步才定案的（见 [04-page-keys.md](04-page-keys.md)）。可读的东西：

| 路径 | 用途 |
|---|---|
| `/proc/bus/input/devices` | 有哪些输入设备、名字、键位图 |
| `/proc/modules` | 加载了哪些模块（`gpiokey` 就这样发现的） |
| `/sys/kernel/debug/gpio` | GPIO 名称、方向、电平 |
| `/proc/interrupts` | 中断号与计数，按下时是否增长 |
| `/proc/kallsyms` | 符号，用来确认哪个驱动在处理 |
| `/lib/modules/3.0.35-lab126/extra/` | 原厂模块本体，可以直接反汇编读 platform data |

只读脚本见 `tools/`；`reports/` 里是两次实际运行的原始输出。

---

## 5. 一条关于增量编译的警告

`make zImage` 增量编译不会重新生成 `include/generated/compile.h`，于是 `uname -v` 里的 `#编号`
和日期可能还是上一次的。**不要用它判断"新内核有没有编进去"**，要用产物的 SHA-256 或 `System.map` 里
新加的符号。这一点在本项目里骗过一次，之后所有部署脚本都改成比对 payload 哈希。
