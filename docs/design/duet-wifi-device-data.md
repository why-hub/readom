# Oasis 1（duet）Wi‑Fi 设备侧取证

> 2026-11-14，在一台 Kindle Oasis 1（固件 5.16.2.1.1）上实测。
> 本文只记录**实测**与**仍待验证**两类事实；推断不写成结论。
> 对应上游第七节的路线 A，以及 `CONTRIBUTING.md` 第 2 条：设备专属数据只在启动时
> 从使用者自己的设备读取，不进仓库、不进发行物。

## 已实测

### 原厂系统的四个文件

来源：把该机原厂 p1 分区镜像以只读 loop 挂载后 `find`，并用设备端报告交叉核对。
路径与大小是**型号特征**，不是设备秘密；内容、哈希、NVRAM 里的 MAC 一律不写进仓库。

| 作用 | 原厂路径 | 字节 | 启动时放进 cpio 的位置 |
|---|---|---:|---|
| NVRAM 校准 | `/opt/brcm_4343w/wifi/nvram/nvram.txt` | 906 | `firmware/image/brcm_4343w/nvram.txt` |
| 正常固件 | `/opt/brcm_4343w/wifi/firmware/fw_bcmdhd_sr.bin` | 357387 | `firmware/image/brcm_4343w/fw_bcmdhd_sr.bin` |
| FullMAC 驱动 | `/lib/modules/3.0.35-lab126/extra/dhd.ko` | 603828 | `firmware/image/brcm_4343w/modules/dhd.ko` |
| 辅助模块 | `/lib/modules/3.0.35-lab126/extra/falcon.ko` | 34392 | `firmware/image/brcm_4343w/modules/falcon.ko` |

同目录另有产测固件 `fw_bcmdhd_mfg.bin`（374673 字节）与一组蓝牙 `.hcd`；启动不需要，不读取。
`dhd.ko` 的 `.modinfo` 里有 `firmware_path` / `nvram_path` 参数，默认指向上面两个原厂路径；
`modules.alias` 里 `sdio:c00v*d* dhd` 兜底可匹配这颗芯片。

### 原厂内核身份（供 `dd_device_matches`）

```
/proc/cmdline : no_console_suspend rootwait log_pos_goal=0x8fff0000 console=ttymxc0,115200 root=/dev/mmcblk0p1 ro ip=off quiet video=mxcepdcfb:E60,bpp=8,x_mem=3M
/proc/version : Linux version 3.0.35-lab126 (builder@eink-builds) (gcc version 4.9.1 (GCC) ) #6 PREEMPT Tue Aug 1 12:57:50 UTC 2023
用户分区挂载 : /mnt/base-us
```

与 PW3 的差别只有两处：引导命令行多一个 `no_console_suspend`，原厂内核构建号是 `#6 ... 12:57:50`
（PW3 是 `#8 ... 12:49:59`）。`/proc/cpuinfo` 的 Hardware 仍是
`Freescale i.MX 6SoloLite based Wario Board`——duet 与 wario 共用
`board-mx6sl_wario.c`，靠 `lab126_board_is()` 运行时分流。这一条来自源码与本机编译出的
duet `vmlinux` 字符串，**不是**设备上直接读 `/proc/cpuinfo`，故标为源码级证据。

### 路线 A 的两道静态门禁

- 用本项目的 duet 配置编出的内核，`Module.symvers` 覆盖原厂 `dhd.ko` 的
  **185/185** 个未定义符号（PW3 对照是 184/185，缺 `in_falcon`；它由 duet 板级内建代码导出）。
- 把 `CONFIG_LOCALVERSION` 设为 `-lab126` 重编后，模块 vermagic 与原厂
  `3.0.35-lab126 preempt mod_unload ARMv7 p2v8 ` **逐字一致**（含末尾空格）。
- 真机上 `insmod` 三个本项目编出的模块（`crc-ccitt`、`nls_ascii`、`nls_utf8`）**3/3 成功**、
  `rmmod` 3/3 复原，`dmesg` 无 `version magic` / `Unknown symbol`。这是**反向**实证：
  证明交叉工具链的产物能被原厂内核接受，不等于证明原厂模块能在新内核里跑起来。

## 仍未验证（不要在补丁里冒充结论）

1. **运行时 ABI**：原厂模块编给原厂内核，本项目给它加了 Android 补丁；vermagic 与符号相同
   不保证结构体布局、导出函数语义一致。要上机 `insmod` 看 `dmesg` 与扫描行为。
2. **加载分支**：启动程序里加载厂商模块的分支在 v0.1.0 之前删除（2026-10-29），尚未补回；
   本补丁只把四个文件在启动时读进 cpio，没有写加载逻辑。
3. **启动参数**：`firmware_path` / `nvram_path` 要指到 cpio 展开后的新路径，尚未定。
4. **补丁 0310 / 1045**：写的是 ath6kl 与等 SDIO 卡侦测的事实，需按 BCM4343W 重写。
5. **交接页表**：2G/2G 下私有页表要照抄活页表、保留模块区（上游第六节），尚未实现与验证。
6. **实际 Wi‑Fi**：能否关联、扫描、唤醒后恢复，全部未测。

按上游「不预先写一条没有机器能跑的路径」的规矩，本补丁集只落**已实测的那部分**：
设备数据的读取与归档，以及让这部分可测的机型参数化。其余六项留给有目标机器的人继续。

## 这组改动做了什么

- `boot/devicedata/devicedata.h`：把 `dd_profile` 里的两条 Wi‑Fi 硬编码路径扩成一张按机型的计划
  （校准大小、cpio 路径、目录表、要现场读取的原厂模块表、波形路径）。
- `boot/devicedata/devicedata.c`：PW3 行为逐字不变；新增 `dd_duet_5_16_2_1_1`。
  `dd_archive` 现在按计划建目录、写固件/NVRAM、逐项读取原厂模块；校准大小检查由 2048
  改成 profile 里的实测值（duet 906），失败时把实际与期望都写进 `why`。
- `boot/devicedata/tests/devicedata_test.c`：在假设备上验证 duet 身份与 6 个 cpio 条目，
  并做两处反向验证（PW3 的构建号必须被拒；905 字节的校准必须被拒）。
- 设备专属数据一处都没进仓库：只有路径和大小，没有内容、哈希、MAC、序列号或板 ID。
