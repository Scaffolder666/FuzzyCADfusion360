# FuzzyCAD 风险清查手册（手动跑一遍 + 看 log）

目标：把每个命令、每种切换都跑一遍，用一份统一的 log 时间线来判断哪里有问题、
以及崩溃到底是代码的锅还是电脑的锅。

## 0. log 在哪

所有生命周期事件即时写入（每行 flush，硬崩也留得住）：

```
%TEMP%\fuzzycad_crash.log
```

Windows：资源管理器地址栏粘 `%TEMP%` 回车 → 找 `fuzzycad_crash.log`。
**每做完一节，或一崩，就把这个文件贴给我**（整段或最后 30~50 行）。

日志里会看到这些“边”事件（都不在每帧路径上，不会拖慢拖动）：

| 事件 | 含义 |
|---|---|
| `ACTION <action> ...` | 你在面板上点了什么（tool / accept / reject / confirm / clearAll / editManipulator / edit …） |
| `TOOL_OPEN / TOOL_OK / TOOL_CLOSE tool=…` | 工具条命令的开 / 确认 / 关，附 `active_cmd` |
| `REOPEN LAUNCH… / CREATE… / EDIT OPEN… / DESTROY… / EDIT CLOSED…` | 点卡片进入编辑的生命周期 |
| `SAFE_FINISH_… / CLOSE_EDIT_SYNC_… / SAFE_TERMINAL_…` | Confirm / Accept / Reject / 切换时安全关闭编辑命令 |
| `ACTIVATE_*` | 最危险的“原生命令激活”窗口里的逐条渲染调用 |

**判断标准**：如果崩溃发生，看 log 最后一行——
- 停在某个 FuzzyCAD 事件（尤其 `ACTIVATE_*` / `SAFE_*` / `CLOSE_EDIT_SYNC_*`）→ 是我们的 bug，我按行定位。
- log 正常收尾、没有半截 → 更像 Fusion / 显卡 / 内存（电脑侧）。

> 建议：每开始一节前，先把 `fuzzycad_crash.log` 删掉或另存，这样每节的时间线是干净的。

---

## A. 单个工具：开 → 调 → 确认 → Accept / Reject

对**每个工具**各跑一遍（导入一个 STEP 或建个方块当对象）：

Move · Rotate · Scale All · Scale X/Y/Z · Axis Rotate · Extrude · Fillet · Hole · Rough Shape

每个工具的标准动作：
1. 点工具条图标 → 选几何 → 拖一下（或输入数字）。
   - 期望 log：`ACTION tool=…` → `TOOL_OPEN` → 交互中无每帧刷屏。
2. 点 **Confirm**（左边栏）或工具自己的 OK。
   - 期望：立刻变“漫画态”；log 出现 `TOOL_OK` 然后 `TOOL_CLOSE`。
3. 右边卡片点 **Accept**；换个对象再来一次点 **Reject**。
   - 期望：`ACTION accept id=…` / `ACTION reject id=…`；对象恢复实体/移除卡片。

**Fillet 专项**（这次改了）：拖半径时应该**只画弧线/示意**，**松手或输入数字后**才出现半透明实体。
- 期望 log：拖动中**不**出现 `compute` 类刷屏；松手约 0.2s 后才有一次 exact 计算。
- 大装配体上重点观察是否还卡。

**Extrude 专项**：拖深度全程线框，Accept 才真正融合。观察是否还会卡爆/闪退。

---

## B. 切换（重点，之前崩就在这）

每一种都**故意快速连点几次**，然后看 log：

1. **卡片 ↔ 卡片**：开着 A 的编辑 → 点 B 的卡片 → 点回 A。
   - 期望：`REOPEN LAUNCH`→`CREATE`→`EDIT OPEN`，旧的 `DESTROY … stale -> no-op`，不崩不卡。
2. **卡片 → 工具条**：开着某卡片编辑 → 点工具条任意工具。
   - 期望：`CLOSE_EDIT_SYNC_BEGIN/DONE`（安全关编辑）→ `TOOL_OPEN`。**不应**再有对 edit_existing 的 terminate。
3. **工具条 → 卡片**：用着某工具（未确认）→ 点一张卡片。
   - 期望：`TOOL_CLOSE` + `REOPEN … EDIT OPEN`，画面不闪崩。
4. **工具条 → 工具条**：用着 Move → 直接点 Scale → 再点 Rotate。
   - 期望：正常切换（这条一直是好的，作为对照）。
5. **编辑中点 Clear all**：开着卡片编辑 → 点 Clear all。
   - 期望：`ACTION clearAll` → `SAFE_FINISH_…`（走安全路径），**不**崩。

---

## C. Compare / Conflict

1. 选第一个 body → 选第二个 body → Confirm。
2. 在 Compare 卡片上做 focus / 切换备选。
3. Accept / Reject Compare。
- 导入的 STEP（多 occurrence）也测一次。
- 期望：无崩溃；log 里 Compare 的开始/确认/结束成对出现。

---

## D. 杂项 / 边角

- 连续快速点同一张卡片很多下（测 re-click 保护 + launch 门闩不卡死）。
- 打开一个之前存过 FuzzyCAD 状态的文件（测 hydrate/持久化）。
- Undo/Redo 后再操作（Fusion timeline 变动后对象是否还有效）。

---

## 交给我

每节结束把 `fuzzycad_crash.log` 贴来（哪节崩了就标一下在哪一步）。我会：
1. 对每种流程核对生命周期事件是否成对、有没有 stale 踩状态、有没有半截；
2. 崩的那步按 file:line 定位并修；
3. 顺便按这次约定的“工具细分”把相关代码归拢。
