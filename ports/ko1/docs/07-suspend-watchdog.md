# 07 · 锁屏重启（看门狗与 suspend）

## 结论先说

**看到了两条相互独立的路径，掐断了其中一条，另一条还没定位。**

| 路径 | 机理 | 状态 |
|---|---|---|
| A | 系统真入睡 → `imx2_wdt` 喂狗 timer 冻结 → 60 s 后硬件复位 | **已修**，有真机验证 |
| B | 系统根本睡不着（WiFi 拦着），屏幕黑着但机器醒着 | 不会因此重启，但也不省电 |
| C | 唤醒路径上的某个环节（疑似 EPDC resume）卡死 | **未定位**，仍有复发 |

现象上三者都表现为「重启回原厂系统」，靠 `dmesg` 里的 `SRC_SRSR` 锁存位 + 设备本地内核日志才能区分。

---

## 症状

长时间锁屏后，再按电源键没有任何反应，接着屏幕闪一下出现 Kindle 开机画面，
最终回到原厂系统（不是 Android）。属一次性交接，重启后进原厂是预期行为。

出现的频率不固定：有时一晚上两次，有时连测十次都正常——这正是因为上面三条路径的触发条件不同。

---

## 链条 A：看门狗

### 驱动事实

```
imx2-wdt.0: IMX2+ Watchdog Timer enabled. timeout=60s (nowayout=1)
```

`drivers/watchdog/imx2_wdt.c`（3.0.35）里：

- 没有 `.suspend` / `.resume` 回调；
- 喂狗靠 `mod_timer(&wdog->timer, jiffies + wdog->timeout / 2)`，即 30 s 一次（配置 15 s）；
- `imx2_wdt_stop()` 只停 timer，**不清 `WCR.WDE`**。

于是：

1. 系统进 `mem` 睡眠，用户态被冻结，timer 也停摆；
2. 硬件计数器（`WCR` 里那 60 s）继续跑；
3. 60 s 后 SoC 被复位。

### 修法

`tools/patch-ko1-wdt-suspend.py`：给驱动加 suspend/resume。

```c
static int imx2_wdt_suspend(struct device *dev)
{
        struct watchdog_device *wdog = dev_get_drvdata(dev);
        struct imx2_wdt_device *wdev = watchdog_get_drvdata(wdog);

        del_timer_sync(&wdog->timer);
        imx2_wdt_ping(wdog);            /* 先把计数器打满 */
        /* 双写：WDE 是 WCR 的 bit2，手册要求连续两次写入才生效 */
        writew(0, wdev->base + IMX2_WDT_WCR);
        writew(0, wdev->base + IMX2_WDT_WCR);
        return 0;
}

static int imx2_wdt_resume(struct device *dev)
{
        /* 必须放在最前面：先把 WDE 打开，再让别的驱动跑 */
        ...
}
```

两个容易写错的点：

- **`WCR` 要写两次**，imx6 的看门狗寄存器是双写保护，只写一次不生效；
- **resume 里重开 `WDE` 必须是最先做的事**，否则 resume 中途卡住时机器又处于无保护状态。

实测日志（v26）：

```
22:04:23  KO1WDT suspend: WDE cleared, wcr=7739
22:04:24  PM: Some devices failed to suspend
22:04:24  KO1WDT resume: wcr=773d timeout=60s
```

`wcr` 从 `0x7739` 变成 `0x773d`——bit2（WDE）被清掉了，resume 时又置回来。

---

## 路径 B：为什么「有时候才重启」

这条反直觉的观察解释了一切。

本机内核**没有开** `CONFIG_PM_AUTOSLEEP`：

```
%CONFIG_PM_AUTOSLEEP%=n
%CONFIG_PM_WAKELOCKS%=n
```

所以 `libsuspend` 退回 `wakeup_count` 路径（日志里有 `libsuspend: Selected wakeup count`），
这条路要求「写回 wakeup_count 期间没有任何唤醒事件」。

而 **WiFi 开着时，bcmdhd 在 suspend 阶段拆 SDIO 卡（mmc3）会失败**：

```
wifi_platform_prealloc: failed to alloc static mem section 7
PM: Some devices failed to suspend
mmc3: error -110 during resume (card was removed?)
```

PM 核心在 1 s 内回滚，连续失败 4 次后 libsuspend 停止重试，系统就一直醒着、屏幕黑着。

**结果：WiFi 开着时机器根本不睡 → 看门狗 timer 没冻结 → 不会因路径 A 重启。**
只有 WiFi 关掉、bcmdhd 不参与时，suspend 才可能真正成功。
这就是「有时候重启」的来源——出事的永远是 WiFi 关闭时那几次。

> 附一条正面结论：熄屏后 WiFi 掉线是 BCM 的 host-sleep 节能设计，唤醒后自动重连，
> 手动开关也正常，**不是缺陷**。不要照着「WiFi 被 suspend 弄坏了」去改。

---

## 路径 C：还没定位的那条

### 11-17 的验证（通过了）

关 WiFi + 拔 USB + 锁屏 10 分钟：

| 证据 | 值 |
|---|---|
| 内核日志空窗 | `22:40:55` → `22:50`，近 9 分钟零日志（连 healthd 30 s 心跳都停了） |
| `uptime` | 连号到 3325 s，无断点 → 没复位 |
| 唤醒 | 一按就亮，内容正常 |

用户态被冻结 + 无复位，说明路径 A 确实被掐断了。

### 11-18 的复发

第二天早上又重启了一次。因为 `KO1WDT resume` 那行日志曾被 32 KiB 的内核缓冲冲掉，
「resume 到底跑没跑」没有硬证据，所以现在还分不清是：

- resume 半路卡住（系统活着但不显示，用户长按电源 → 表现为重启）；
- 还是另一条复位源（温度、软件复位、`SRC_SRSR` 的其他位）。

### 今晚要补的取证

1. **加大 `CONFIG_LOG_BUF_SHIFT`**，让 suspend/resume 前后的日志不被冲掉（当前 32 KiB 太小）；
2. 每次开机读一次 `SRC_SRSR`（`0x020D8000`），锁存位能区分 POR / WDOG / 温度 / 软件复位，
   已有的板级 `ko1_rst_report` 打印 `KO1RST srsr=%08x gpr10=%08x` 就够用；
3. 设备本地黑匣子：

```sh
nohup logcat -b kernel -v time -f /data/local/tmp/kt.log >/dev/null 2>&1 &
```

`/data` 在 eMMC 上，重启不丢，但**必须在进 Android 之后、锁屏之前**架好，
下一次开机才能从里面读到「死前最后几秒」。

### 如果要把 suspend 彻底排除

`tools/patch-ko1-no-suspend.py`（已写、未应用）：在 `kernel/power/suspend.c` 的
`enter_state()` 开头直接返回 `-EINVAL`，锁屏只熄屏、机器永不入睡。

这是一条**对照实验**，不是修复：如果禁掉 suspend 后仍然重启，问题就一定不在睡眠路径；
代价是待机功耗。留着当诊断开关用。

---

## 留给上游的问题

- i.MX6 系平台上，`imx2_wdt` 缺 suspend/resume 是普遍问题还是有别的约定做法？
  （`nowayout=1` 的配置下，suspend 期间硬件计数器仍在跑这件事看起来不符合预期。）
- `CONFIG_PM_AUTOSLEEP` 在 Android 8.1 的 vendor 内核里通常应该开；本机没开是否有明确原因？
- bcmdhd 在 suspend 阶段拆 SDIO 失败（`section 7` 分配不到）在别的 BCM4343W 机型上是否复现？
