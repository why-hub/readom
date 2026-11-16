# 02 · 充电指示

目标不是"把图标消掉"，而是**线插着显示在充电、拔掉就不显示**。这个区别很重要：
一开始为了消掉误报，最省事的做法是让 healthd 不认任何充电源，那样拔不拔都一个样，等于把功能删了。

---

## 现象

线根本没插，状态栏也是"充电中"；插上线反而看不出差别。

## 为什么

这台机器有四个 `power_supply` 节点，其中三个都自称 `Mains`，而 healthd 对 `Mains` 的处理是
"给 AC 位"，BatteryService 就把 `plugged=AC` 报给上层：

| 节点 | 谁 | 问题 |
|---|---|---|
| `soda_boost` | 电池 boost 转换器，没插线时给系统供电轨供电 | type 是 `UPS`，`online` = `soda_soft_boost_sts`，**在电池上跑时正好是 1** |
| `soda_chg` | 充电控制 | `online` 原来不是真实插拔状态 |
| `max77696-charger` | PMIC 充电器 A 路 | `online` = `chg_online`（`CHGINA` 位） |
| `max77696-charger-eh` | PMIC 充电器 B 路 | `online` = `chg_online`（`CHGINB` 位） |

第二个坑在 healthd 自己：这个 fork 的 type 映射表是

```
"Battery" -> 4
"UPS"     -> 1     <- 和 Mains 同一个 bit
"Mains"   -> 1
"USB"     -> 2
```

`soda_boost` 报 `UPS`，被折成 AC 位。**它恰恰是"没有外接电源"时才为 1 的那个节点**，
语义完全反了：靠电池运行时它在线，于是系统认为"接着交流电"。

## 怎么做

分两步，各自解决一层：

1. **把 UPS 从 AC 位里拿掉**（`tools/patch-healthd-ups-flag.py`）。
   改 Amazon healthd 里那张 type 表的 `"UPS"` 项，让它不再产生 `BATTERY_PLUGGED_AC`。
2. **只让真正的插线检测节点报 Mains**（`tools/patch-charger-single-mains.py`）。
   - `soda_chg.online` 改成读真实的插拔原子量；
   - `max77696-charger` / `-eh` 不再无条件报 Mains，而是跟随各自 `CHGINx` 位；
   - 顺带加上"只在状态变化时打印"的诊断，logcat 里能直接看到是哪个节点在说谎。

诊断输出在排查时非常有用：`chg=a` 这种一行摘要配上节点变化记录，能立刻区分
"某个节点恒为 1"和"某根线真的被拉起来了"。

## 真机结果

- 插线：状态栏出现充电指示；
- 拔线：指示消失，且不会自己再冒出来；
- 静置期间 `plugged` 不再抖动。

## 提醒

`drivers/power/soda.c` 与 `max77696-charger*.c` 的改动是**内核侧**，healthd 那张表是**二进制补丁**。
如果换一版 healthd，`patch-healthd-ups-flag.py` 需要按新的表偏移重定位——它会在打之前先校验锚点字节，
对不上就 abort，不会写坏。
