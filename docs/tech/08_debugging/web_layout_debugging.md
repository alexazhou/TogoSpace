# Web 页面布局排查指南

## 概述

本文档记录如何在 **不改代码** 的前提下，仅使用浏览器开发者工具排查以下 Web 前端布局问题：

- 页面内容没有撑满浏览器可视高度
- 底部出现大块空白
- 输入框、发送按钮掉出首屏
- 页面看起来“滑不动”
- 实际问题到底是“页面没撑满”还是“内容太少”

这份文档的目标不是直接给出修复方案，而是帮助先把问题类型判断准确。

---

## 适用场景

适合用于排查以下用户反馈：

- “页面下面空了一大截”
- “聊天区没有撑满整个网页”
- “输入框不见了”
- “页面不能上下滑动”
- “我和你看到的页面不一样”

---

## 核心原则

- **先看当前浏览器窗口这一屏，不看 full page。**
- **先判断是哪一层没有撑满，再判断该不该滚动。**
- **先确认问题发生在真实窗口场景，避免只看 mobile emulation 的理想视口。**
- **不要一上来改代码，先把“问题属于哪一类”分清楚。**

---

## 第一步：只看当前视口

排查这类问题时，最容易误判的地方，是把“页面完整截图”当成“当前用户实际看到的页面”。

正确做法：

- 使用浏览器开发者工具或浏览器自动化工具时，只截 **当前视口**。
- 不要使用 full-page screenshot。
- 如果用户给的是“桌面浏览器窗口变窄”的截图，就按那个窗口高度去检查，不要默认切到标准手机高度。

判断标准：

- 如果当前视口底部有明显空白，而聊天卡片没有贴到底部，优先怀疑是“页面没撑满”。
- 如果聊天卡片已经贴到底，但消息区内部空着，优先怀疑是“内容少导致内部留白”。

---

## 第二步：量页面各层容器高度

在浏览器 Console 中执行下面这段脚本：

```js
(() => {
  const sels = ['.shell', '.workspace', '.workspace-grid', '.chat'];
  return sels.map((sel) => {
    const el = document.querySelector(sel);
    if (!el) return { sel, found: false };
    const r = el.getBoundingClientRect();
    return {
      sel,
      top: Math.round(r.top),
      bottom: Math.round(r.bottom),
      viewportBottomDelta: Math.round(window.innerHeight - r.bottom),
      height: Math.round(r.height),
      overflowY: getComputedStyle(el).overflowY,
    };
  });
})();
```

这段脚本的作用：

- 逐层看外层容器是不是已经贴近浏览器底部
- 直接计算“当前容器距离视口底部还差多少像素”

判读方式：

- `viewportBottomDelta = 0` 或接近 `0`
  说明这一层已经贴到底部。
- `viewportBottomDelta` 很大
  说明这一层没有撑满页面，下方空白大概率就是它留下来的。
- 如果 `.shell` 已经到底，而 `.chat` 没到底
  说明不是整个页面高度的问题，而是中间布局链路有一层没有把空间传递下去。

---

## 第三步：确认滚动归属在哪一层

在浏览器 Console 中执行：

```js
(() => {
  const sels = ['#app', '.shell', '.workspace', '.chat', '.message-viewport', '.message-stream'];
  return sels.map((sel) => {
    const el = document.querySelector(sel);
    if (!el) return { sel, found: false };
    return {
      sel,
      clientHeight: el.clientHeight,
      scrollHeight: el.scrollHeight,
      scrollTop: el.scrollTop,
      overflowY: getComputedStyle(el).overflowY,
    };
  });
})();
```

这段脚本的作用：

- 确认页面到底允许哪一层滚动
- 区分“不能滚”是因为没内容可滚，还是因为滚动被锁死在外层/内层

判读方式：

- `scrollHeight > clientHeight`
  说明这一层理论上有滚动内容。
- `overflowY = hidden`
  说明这一层即使有超出内容，也不会直接滚。
- 如果外层很多层都是 `hidden`，而真正应该滚动的那一层也没有足够高度
  就容易出现：
  “下面空着，但页面又滑不动”。

---

## 第四步：确认输入框是否真的在首屏

不要用肉眼猜输入框“好像在”或“好像不在”，直接量。

```js
(() => {
  const textarea = document.querySelector('textarea');
  const sendButton = Array.from(document.querySelectorAll('button'))
    .find((el) => el.textContent?.trim() === '发送');

  const calc = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {
      top: r.top,
      bottom: r.bottom,
      height: r.height,
      visibleHeight: Math.max(0, Math.min(window.innerHeight, r.bottom) - Math.max(0, r.top)),
      fullyVisible: r.top >= 0 && r.bottom <= window.innerHeight,
    };
  };

  return {
    viewportHeight: window.innerHeight,
    textarea: calc(textarea),
    sendButton: calc(sendButton),
  };
})();
```

判读方式：

- `fullyVisible = true`
  说明该元素完整出现在首屏。
- `visibleHeight > 0` 但 `fullyVisible = false`
  说明该元素只露出一部分。
- `visibleHeight = 0`
  说明该元素完全掉出首屏。

这是判断“输入框有没有显示出来”的最直接方法。

---

## 第五步：区分两类“底部空白”

这一步非常关键。

### 类型 A：页面没撑满

特点：

- 聊天卡片底部距离视口底部还有明显距离
- `.chat` 或 `.workspace` 的 `viewportBottomDelta` 明显大于 0
- 用户看到的是“整个内容块悬在上面，下面是空白背景”

这类问题通常说明：

- 布局高度没有正确传递
- 中间某层没有 `height: 100%` / `minmax(0, 1fr)` / `flex: 1`
- 外层高度设置和内层拉伸规则不匹配

### 类型 B：页面撑满了，但内容区内部是空的

特点：

- `.chat` 已经基本贴到底部
- 空白出现在消息区内部
- `.message-stream` 高度足够，但当前消息少

这类问题通常说明：

- 页面本身高度没问题
- 真正的问题是消息内容少，或者消息区视觉分配不合理
- 要决定的是“是否需要压缩上方头部和输入区”，而不是先怀疑页面没撑满

---

## 第六步：确认用户实际场景

同一个页面，在不同场景下会得到完全不同的结论：

- 浏览器 mobile emulation 的标准手机视口
- 桌面浏览器里把窗口压窄
- 带地址栏和标签栏的真实浏览器窗口
- DevTools 打开/关闭前后的可视高度

因此排查时必须先确认：

- 用户看到的是“手机模拟器”还是“桌面浏览器窗口”
- 当前浏览器窗口高度是多少
- 截图是不是当前窗口可视区域，而不是全页截图

如果这一步不确认，最容易出现的误判是：

- 你检查时输入框在首屏
- 用户看到时输入框却掉出首屏

这通常不是“谁看错了”，而是根本不是同一个可视高度。

---

## 推荐排查顺序

建议按照下面顺序做，不容易漏问题：

1. 截当前视口，不截 full page。
2. 先看聊天卡片是否贴到底部。
3. 跑“各层容器高度”脚本，确认是哪层没撑满。
4. 跑“滚动归属”脚本，确认哪层在锁滚动。
5. 跑“输入框可见性”脚本，确认输入框是完整可见、部分可见，还是完全不可见。
6. 最后才决定是调高度、调滚动，还是调布局结构。

---

## 一个典型误判案例

错误做法：

- 只看输入框 DOM 存不存在
- 看到输入框在页面里，就判断“布局没问题”

为什么错：

- 输入框可能只是“存在”，但实际上只露出一半
- 发送按钮可能完全掉出首屏
- 页面可能没有可用滚动把它带出来

正确做法：

- 必须结合 `getBoundingClientRect()` 结果判断元素到底露出多少
- 必须结合容器高度链路判断页面是不是满高
- 必须结合滚动链路判断用户能不能把元素带进视口

---

## 建议沉淀的结论格式

排查完成后，建议用下面这种格式记录问题，不容易混淆：

```text
问题类型：
- 页面未撑满 / 页面已撑满但内容区内部空白 / 输入区掉出首屏 / 滚动被锁死

证据：
- .chat 距离视口底部 84px
- textarea visibleHeight = 0
- sendButton fullyVisible = false
- .workspace overflowY = hidden
- .message-stream clientHeight = 333, scrollHeight = 333

结论：
- 当前不是消息缺失问题，而是布局高度与滚动归属问题
```

---

## 总结

用浏览器工具排查这类问题时，最重要的是三件事：

- **只看当前用户实际可见的窗口场景**
- **按容器层级量高度，不靠肉眼猜**
- **把“页面没撑满”和“内容区内部空白”分开判断**

只要按这套方法走，通常能在改代码之前先把问题类型判断清楚，避免修错方向。
