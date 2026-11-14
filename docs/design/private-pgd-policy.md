# 私有页表策略：为什么一个常量不能覆盖两种内存布局

> 2026-11-14，阶段 A2。本文记录一处**机制**改动：让交接模块的私有 TTBR0 表
> 按机型选择填法。文中「已验证」只包括本机跑过的检查；真机与 QEMU 均未执行。
> 上游依据见 `docs/design/porting-to-other-models.md` 第六节。

## 契约

`identity_limit` 是「交接将运行的那张表里，1:1 区域的上界」，不是 `TASK_SIZE`。
两种布局都要满足 `pw3_plan.c` 里那条 `identity_limit > boot_ram_base` 检查；
它拒绝的是写坏了的计划，不是某一类机器。

## 两种策略

| 策略 | 使用条件 | 私有表怎么填 |
|---|---|---|
| `PWS_PGD_BUILD_WINDOW` | `PAGE_OFFSET != PHYS_OFFSET`（3G/1G） | 清零 `[0, identity_limit)`，写入控制页那一节，其上逐字照抄活页表 |
| `PWS_PGD_COPY_LIVE` | `PAGE_OFFSET == PHYS_OFFSET`（2G/2G） | 4096 节全部逐字照抄活页表，一节都不清 |

选择权在 profile：`build_module.py` 只在 profile 显式写了 `pgd_policy` 时才往
`target-lock.h` 生成 `TARGET_PGD_POLICY`。PW3 的 profile 没写这个字段，因此它的
`target-lock.h`、生成的 4096 个表项、以及既有断言**逐字不变**。

同一个纯函数 `pws_private_pgd_word()` 被三处共用：`stage.c` 的 `prepare()` 写表、
`audit_plan()` 独立重算每一节、`native-handoff.h` 填原生后端的表。审计方不读取
写表方的中间状态，仍是独立消费者。

## 为什么 duet 不能只改配置文件里的数字

2G/2G 下 `PAGE_OFFSET` 与 `PHYS_OFFSET` 同为 `0x80000000`，内核 lowmem 映射本身
就是 1:1，交接要的窗口不需要另建；而模块区在 `0x7F000000`–`0x80000000`
（`MODULES_VADDR = PAGE_OFFSET − 16 MiB`），正好落在旧代码无条件清零的范围里。
清掉它，写完 TTBR0 之后的下一条指令就取不到。

原来的 `BUILD_BUG_ON(TARGET_IDENTITY_LIMIT > MODULES_VADDR)` 会把 duet 的上界
`0xA0000000` 挡住，这是**故意**的：它防的是「把界限写大来绕过清零」。现在这条
断言只在 `BUILD_WINDOW` 策略下生效；`COPY_LIVE` 策略下保留整张活页表，不存在
「界限以下要清零」这回事，断言也就不再适用。

```c
if (policy == PWS_PGD_COPY_LIVE || index >= identity_sections)
    return live[index];
```

## 已验证与未验证

- 已验证：上游全量测试套件通过；`test-private-pgd.c` 逐节比对两种策略的 4096 个表项，
  并反向锁定「用旧策略套 duet 上界会清零模块区」这一事实；故意破坏策略分支后测试失败、
  恢复后全绿。
- 已验证：PW3 的 profile、生成的 `target-lock.h` 与表项字节不变。
- 部分验证：`pw3_first_kernel.ko` 已用真实的 PW3 内核树（3G/1G，`BUILD_WINDOW`，
  33,244 B）与 duet 内核树（2G/2G，`COPY_LIVE`，32,972 B）各自编译通过；把
  `BUILD_WINDOW` 编进 2G/2G 内核会被原有 `BUILD_BUG_ON` 按设计拦下。
  **仍未验证**：任何一台 duet 真机、QEMU 的 2G 测试内核；上游第六节建议先在
  QEMU vexpress 上跑通「照抄活页表」这条路径。
- 未验证：duet 的 profile 本身。它的 `stock_functions` 四个函数体哈希必须从目标设备的
  `/proc/kallsyms` 现场取样，本机没有这些数据，因此**本补丁不创建 duet profile**，
  也不写任何声称能在该机型启动的代码。

这符合 `CONTRIBUTING.md` 的规矩：可以提交有测试支撑的机制，不预先写一条没有机器
能跑的路径。
