# 03 · 微信读书闪退（ART 堆）

闪退的表象很多，真正的原因只有一个：**2G/2G 地址空间里放不下一整块 256 MiB 连续区域**。
这一篇记的是判断过程，因为前两个判断都是错的，而错的地方很典型。

---

## 三个错误判断，按发生顺序

1. **"栈溢出"** —— 从 native crash 的栈深度看像是。加了栈尺寸没有改善，也没有解释为什么只有微信读书。
2. **"地址空间撞顶"** —— 方向对了，但归因错了：以为是 mmap 区域被顶到栈，于是去动 `mmap_rnd_bits`
   （见 [01](01-wifi.md) 最后那一节，那条弯路就是从这里来的）。
3. **定案**：`CursorWindow` 报 `-12`（`ENOMEM`），同时 `dex2oat` 说 `No region space mem map`。
   两个不同进程、两种不同的内存申请同时拿不到，指向同一个资源：**ART 的 CC region space**。

## 为什么 256m 拿不到

`duet` 是 2G/2G 布局，32 位进程的用户地址空间是 2 GiB，mmap 区域从 `TASK_SIZE/3`（约 715 MiB）向上长。
扣掉 code、boot image、已分配的堆之后，剩下约 1.2 GiB。

ART 的 concurrent-copying region space 要**一整块连续** `dalvik.vm.heapsize` 字节：

```
256 MiB  ≈ 整个可用窗口的 1/5，且要求连续
```

碎片一多就申请失败，而且失败时不是"少给点"，是整个 region space 建不起来——进程直接起不来。
PW3 是 3G/1G（`PAGE_OFFSET=0xC0000000`），窗口更宽，同一个 256m 在 PW3 上没问题，所以这个值一直没被质疑过。

## 怎么做

`dalvik.vm.heapsize` 从 `256m` 降到 `192m`（`tools/patch-buildprop-heapsize.py`）。
这是一个**字节级改写**：`build.prop` 大小不变，`dalvik.vm.heapsize=` 的偏移也不变（另一个补丁依赖这个偏移），
所以 ext4 的 inode、块、权限、SELinux 标签全不动，只需重 pin preinit 里的 `system.img` 哈希。

## 真机结果

- 微信读书能起来，连续存活 **11 分钟以上**，能进书架、能进阅读页；
- 没有再出现 `CursorWindow -12` 或 `No region space mem map`。

## 一个必须说清楚的边界

`dex2oat` 的 AOT 编译在这台机器上是**平台性失败**，不是微信读书的问题：
拿系统自带的 `settings` 去触发 AOT 也一样 abort。所以这不是"微信读书修好了"，而是
"在 JIT/解释执行路径上它不再因为堆申请失败而崩"。真要修 AOT，得回到 2G/2G 下的 ART 内存布局本身，
那是另一个量级的工作。

## 顺带

`patch-buildprop-heapsize.py` 与 [05 型号名](05-model-name.md) 的补丁都依赖
`build.prop` 里那几个固定偏移。两个补丁的先后顺序会影响偏移计算，改的时候要一起看，
每次都要重新核对 `dalvik.vm.heapsize=` 的实际偏移再写。
