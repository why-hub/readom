# 05 · 型号名

系统里两处属性不一致，且都不是这台机器：

```
ro.product.model        = KO1                      设置 → 设备信息 → 型号 显示的就是这个
ro.vendor.product.model = Kindle Paperwhite 3      PW3 构建残留
ro.product.manufacturer = Amazon
```

两条都改成 `Kindle Oasis 1`。

## 只改这两条，别动其他身份字段

`ro.product.device` / `ro.product.name` / `ro.build.fingerprint` / `ro.build.flavor` 里的 `pw3`
**有意保留**。理由有两个：

1. **`pw3` 在这个仓库里是平台名，不是机型名**（i.MX6SL + 3.0.35 那一整套）。上游也明确写了
   `gralloc.pw3`、`ro.hardware=pw3`、`init.pw3*.rc`、`sys.pw3.*` 这些别的机型照用、不改名，
   改了 PW3 的构建产物就会变。
2. 本项目早期有过教训：一次性把整套身份字段都改成 duet，**直接启动失败**。
   身份字段是被上游交接模块逐个校验的，改动面越大越容易撞上校验。

## 怎么改：等长度补偿的字节改写

`build.prop` 是 4719 字节，落在两个 ext4 块里，而且 `dalvik.vm.heapsize=` 的偏移（3787）
被 [03 的 heapsize 补丁](03-weread-heapsize.md) 依赖。**文件大小和偏移都不能动。**

所以增长用相邻的注释抵消：

| 位置 | 改动 | 字节 |
|---|---|---|
| `ro.product.model` | `KO1` → `Kindle Oasis 1` | +11 |
| `ro.vendor.product.model` | `Kindle Paperwhite 3` → `Kindle Oasis 1` | −5 |
| abi / abi2 的两行注释（等义改写，缩短 6 字节） | | −6 |
| **合计** | | **0** |

结果：文件仍 4719 字节，`dalvik.vm.heapsize=` 仍在 3787，ext4 的 inode、块分配、`0644`、
SELinux 标签全不变，**只需要重 pin preinit 里的 `system.img` 哈希**，不用重新打包镜像。

工具：`tools/patch-buildprop-model.py`。它会先校验锚点字节、写后回读，
并把 ext4 里 dump 出来的 `build.prop` 与预期逐字节比对；任何一步不符就 abort，不写盘。

## 状态

已部署（`system.img` 换成新哈希、preinit pin 同步、部署自检 `OVERALL PASS`），
**还差一次真机确认**——进「设置 → 设备信息」看型号是不是 `Kindle Oasis 1`。

## 值不值得改

严格说这是外观性改动，不影响功能。之所以做，是因为"设置里显示 Paperwhite"会让人误判设备身份，
排查问题时容易拿错机型的行为做参照——本项目就为此浪费过时间。
