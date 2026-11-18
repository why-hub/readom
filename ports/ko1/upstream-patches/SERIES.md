# duet Wi-Fi 设备数据接入：上游可合并补丁系列

生成：2026-11-14 20:45（GMT+8），21:12 增补第四个补丁，23:15 增补第五个。目标上游：`hbyxabc/readom`（codeload tarball，commit `1ff87b9`）。

## 五个补丁

| 文件 | 内容 |
|---|---|
| `0001-devicedata-make-the-Wi-Fi-archive-a-per-model-plan.patch` | `dd_profile` 从「两条 Wi-Fi 路径 + 硬编码 2048 字节」扩成按机型的计划；新增有实测依据的 `dd_duet_5_16_2_1_1`；`dd_archive` 按计划建目录、写固件/NVRAM、逐项现场读取原厂 `dhd.ko`/`falcon.ko` 进 cpio。PW3 的 cpio 输出逐字不变。 |
| `0002-devicedata-test-the-duet-profile-and-reverse-its-new.patch` | C 测试覆盖 duet 身份与六个 cpio 条目；两处反向验证：PW3 的构建号必须被判为另一固件、905 字节的校准必须被拒并给出原因。 |
| `0003-docs-record-the-Oasis-1-Wi-Fi-assets-measured-on-a-d.patch` | 新增中英成对设计文档，更新 `duet.config` 与移植设计第七节；把「已实测」与「仍未验证的六项」分开写。 |
| `0004-handoff-make-the-private-page-table-policy-a-per-pro.patch` | 私有 TTBR0 表的填法由「无条件清零 `[0, identity_limit)`」改成按 profile 的两种策略：`BUILD_WINDOW`（PW3，逐字不变）与 `COPY_LIVE`（2G/2G，4096 节全照抄活页表，保留模块区）。新增纯函数头文件、8205 项 C 测试、中英成对设计文档；`build_module.py` 只对显式要求的 profile 生成 `TARGET_PGD_POLICY`。**不创建 duet profile**（缺设备侧函数体哈希）。 |
| `0005-handoff-let-a-profile-pin-the-utsname-fingerprint-it.patch` | 交接模块的 utsname 校验从三个 PW3 字面量改成可按 profile 钉住：profile 带 `kernel_identity` 块才生成 `TARGET_KERNEL_RELEASE/VERSION/MACHINE`，`stage.c` 用 `#ifdef` 保护，PW3 走原字面量分支。**来源是真机第一次失败**：前 27 项全过，被 `check=display_identity` 拦下。 |

补丁里**只有路径和大小**，没有文件内容、哈希、MAC、序列号或板 ID；上游 `private-data` 与 `private-data-scanner` 检查均通过。

## 验证结果（在本机 WSL 上游干净树里跑的）

```
python3 tools/run-tests.py              -> 55 passed, 0 failed, 19 not run here
python3 tools/run-tests.py --filter device-data -> ok  devicedata: all checks passed
python3 tools/run-tests.py --filter private-pgd -> ok  PRIVATE_PGD_PASS checks=8205
ko1/tools/build-duet-module-check.sh   -> BUILD_OK pw3 33244 B (PW3 3G/1G 内核树)
                                          BUILD_OK duet 32972 B (duet 2G/2G 内核树)
```

第 0004 枚额外做了**真实内核模块编译**：`pw3_first_kernel.ko` 分别用
`out-android-core`（PW3 3G/1G，`BUILD_WINDOW`）与 `out-android-core-duet`
（2G/2G，`COPY_LIVE`）编出；把 `BUILD_WINDOW` 编进 2G/2G 树仍被原有
`BUILD_BUG_ON` 按设计拦下。这一步抓到了纯 C 测试覆盖不到的漏改调用点
（`native_prepare()` 里的 `audit_plan(s)`），已修并把活页表物理地址记进 `store`。
**编译通过不等于能跑**，真机/QEMU 仍未执行。

19 项 skip 都是上游自己列的环境依赖（构建 VM、AOSP 树、导出镜像、Python 3.11 等），
与这组改动无关。`doc-pairs` 250 项、`reuse` 108 项、`every-test-runs` 69 个测试文件、
`private-data` 960 文件全部通过。

**反向验证两组**：
- `ko1/tools/verify-upstream-reverse.py`（对应 0002/0003）：把 duet profile 里实测的
  906 字节故意改成 905，`device-data` 立刻 8 项失败；恢复后全绿。
- `ko1/tools/verify-pgd-policy-reverse.py`（对应 0004），四项全过：
  ① 故意让 `COPY_LIVE` 分支失效 → `private-pgd` 在第 40 行断言失败（编译仍通过，不是靠
  `-Werror` 的警告凑数）；② 恢复后全绿；③ 用基线版与改动版 `build_module.py` 分别对 PW3
  profile 生成 `target-lock.h`，**1849 字节逐字相同**；④ `pgd_policy=2` 的 profile 才生成
  `TARGET_PGD_POLICY 2u`，基线版不生成。

这满足 `CONTRIBUTING.md`「新加的检查要做反向验证」的要求。

**第 0005 枚的回归证明**（`ko1/tools/verify-kernel-identity-pin.py`，不需要编译）：
把**改动前**（`/mnt/port` 上游树）与**改动后**（staged 树）的 `target_lock()`
喂同一份 PW3 profile、同一份 kernel/initramfs/cmdline 与固定 footprint，
两边输出 **2063 字节逐字相同**；同一对比在 duet profile 上只多出
`TARGET_KERNEL_RELEASE/VERSION/MACHINE` 三条 `#define`，无删除行。
`stage.c` 的 `#else` 分支仍保留 `"#8 PREEMPT Tue Aug 1 12:49:59 UTC 2023"` 字面量，
所以 PW3 的模块字节不变。duet 侧实测：模块 `82af7534…`（33,224 B，含三个新宏），
launcher `8ae83289…`。

## 基线与应用

- 干净基线：WSL `/mnt/duet-pr`，由 `D:\wsl\downloads\readom-main.tar.gz` 解出后
  `git init` + 一次 baseline commit（`29cc5d4`）。原 tarball 不含本项目的 `ko1/` 目录。
- 四个 commit：`1a4b71d` → `7070e4e` → `b00e159` → `025fbe3`。前三个用
  `git format-patch -3` 导出，第四个用 `git format-patch --start-number 4 -1` 导出。
- 复现改动：`ko1/tools/build-upstream-wifi-patch.py`（0001–0003）、
  `ko1/tools/build-pgd-policy-patch.py`（0004）从基线精确替换并生成文档；
  `verify-upstream-reverse.py` / `verify-pgd-policy-reverse.py` 做反向验证。
- 应用：在作者仓库的新分支上 `git am *.patch`（补丁是标准 git 格式，含 commit message）。

## 诚实边界：这组补丁只解决了「把文件取到」的一半

上游第七节推荐路线 A，但明确写着「不预先写一条没有机器能跑的路径」。因此本系列**没有**
写任何未验证的加载逻辑。以下六项仍需目标机器，已在设计文档里逐条列出：

1. 原厂模块在新内核的**运行时 ABI**（vermagic 与 185/185 符号只是静态门禁）；
2. 启动程序里被删除的**厂商模块加载分支**（2026-10-29 删，未补回）；
3. `firmware_path` / `nvram_path` 指向 cpio 展开后的新路径；
4. 补丁 `0310` / `1045` 按 BCM4343W 重写（现为 ath6kl 事实）；
5. 2G/2G 下私有页表照抄活页表、保留模块区 —— **0004 已把机制、C 测试、反向验证，
   以及两种内核树上的真实模块编译补齐**；但仍缺 duet profile、QEMU 2G 测试内核跑通、
   真机执行；发行件里那个 helper 是已编译的 PW3 二进制，必须重编才能用；
6. Wi-Fi 实际扫描、关联与唤醒恢复。

**真机 `insmod` 3/3 是反向实证**（本项目编出的模块能进原厂内核），不能当作第 1 项的结论。

## 已知不一致：helper.c 的身份校验还没上游化

0005 只把**模块**（`stage.c`）的 utsname 校验做成了 profile 参数。
**helper.c 的四处身份校验仍是 PW3 字面量**：

| 位置 | 内容 | duet 实际 |
|---|---|---|
| `helper.c:643` | `/usr/sbin/eips` 的 SHA-256 + 62,176 B | 同尺寸，SHA 不同（`12282d95…`） |
| `helper.c:659` | `uname.version` = `#8 … 12:49:59` | `#6 … 12:57:50` |
| `helper.c:232` | `/sbin/init.exe` 的 SHA-256 + 111,824 B | 111,820 B（`61e51893…`） |
| `launcher-template.sh:34` | fallback 行复用的 eips 哈希 | 同上 |

本机的做法是在 staged 构建树里用 `ko1/tools/patch-duet-identities.py`
按 `capture/duet-identities.json` 的实测值替换，**没有改动 `/mnt/port`**。
这是一条能跑通的构建侧补丁，不是可上游的形态；要做成 0006 得给 helper
加一份自己的生成头（它目前不 include 模块的 `target-lock.h`）。
`/sbin/init` 与 `/etc/upstart/init.sh`（1360 B，`ad624cba…`）在 duet 上与 PW3 **相同**，
不需要替换。

## 不在此列的改动

内核侧的改动（给 `imx2_wdt` 补 suspend/resume、以及在 `enter_state()` 里拒绝 suspend 的对照开关）
落在本机 3.0.35 内核树里，不属于 readom 仓库，因此不构成上游补丁。
机理、日志证据与复发记录见 [`../docs/07-suspend-watchdog.md`](../docs/07-suspend-watchdog.md)。

## 是否已提 PR

**没有，且本阶段明确不提。** 用户 2026-11-14 20:52 决定：「等真机试过没问题再提」。
补丁只生成在本机，尚未推送、未开 PR、未向作者提交；**也不要先开「设备数据接入」的独立 PR**，
等阶段 C 至少跑通一轮真机验证、把结果写回文档后再由用户重新决定。

真机验证的完整依赖链、现成发行件解析结果、空间/备份状态与分阶段顺序见
`ko1/DEVICE-TEST-PLAN.md`。当前结论：上游 v0.1.0 有现成 `Readom.sh` + system 镜像，
**不需要从零构建 AOSP**；交接页表的机制改动已由 0004 补齐（仍需重编 helper 才生效），
duet 版入口还缺 duet 交接配置 JSON、`stock_functions` 四项哈希、QEMU 2G 预验证，
以及第 1 项运行时 ABI 的真机结论。
