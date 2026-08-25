# FuzzyCAD — Fusion 360 add-in

在 CAD 中表达**不确定性**,以支持异步、跨学科的协作。四种不确定性表达:
已达成的共识、开放的参数区间(Need Input)、尚未解决的顾虑、以及互相竞争的
备选方案(Compare / Conflict)。

---

## 架构:一个基类 + 一叠补丁

代码不是一个大文件,而是**一个基础实现 + 很多层补丁**。每个补丁只做一件事,
通过 `install(m)` 往共享的 legacy 模块对象 `m` 上打补丁——包装或替换它的
模块级函数(如 `m._accept`、`m._redraw_marks`、`m._DRAW["move"]`、
`m.FuzzyInputChanged` 等)。

```
FuzzyCAD.py          入口 + 加载器:load 基类,再按固定顺序 install 每个补丁
FuzzyCAD_legacy.py   基础实现(命令、mark、绘制、持久化的底座)
```

**关键性质:补丁之间从不互相 `import`。** 它们唯一的通信方式就是读写同一个 `m`
对象上的属性。正因为如此,文件可以自由归类到子文件夹,而不会有断链——移动文件
时只需要改 `FuzzyCAD.py` 里的加载路径。

### 加载顺序 = 包裹层级(很重要)

每个补丁通常这样包装一个函数:

```python
def install(m):
    old_accept = m._accept          # 抓住当前的实现(可能已被前面的补丁包过)
    def accept(mark):
        ...                         # 自己的逻辑
        return old_accept(mark)     # 委托给内层
    m._accept = accept              # 装回去
```

所以**越晚 install 的补丁,包在越外层,越先被调用**。改 `FuzzyCAD.py` 里的加载
顺序会改变谁先拦截。归类只是移动文件,**顺序保持原样**,所以行为不变。

---

## 目录约定

| 文件夹 | 职责 |
|---|---|
| `core/` | 生命周期、commit 桥接、持久化 / 水合、opacity 与状态对账、面板同步、stage UI、layout、clear-all |
| `tools/` | 各操作工具:fillet、scale / axis-rotate、move scope、hole、direct interactions、依赖提示、依赖跟随 |
| `compare/` | Conflict / Compare 功能(stable、in-place、朝向保持、预览、卡片聚焦) |
| `visuals/` | 手绘线条渲染、剪影、幽灵态、操作提示、卡片、不确定性徽章 |
| `references/` | 坏引用提醒与 hover guard |
| `dev/` | **仅 `DEV_MODE`** 的诊断追踪,研究版(`DEV_MODE = False`)不加载 |
| `_attic/` | 已被取代、不再加载的旧模块;仅留档,任何地方都不 import(见 `_attic/README.md`) |
| `palette/` | 面板与工具条的 HTML / JS / CSS |
| `icons/` | 命令图标 |

`FuzzyCAD.py`、`FuzzyCAD_legacy.py`、`FuzzyCAD.manifest` 留在**根目录**——
Fusion 从这里加载 add-in,manifest 指向 `FuzzyCAD.py`。

---

## 怎么加一个新补丁

1. 在合适的文件夹里新建 `fuzzycad_<名字>.py`,写一个 `install(m)`:

   ```python
   def install(m):
       adsk = m.adsk
       old_accept = m._accept

       def log(msg):
           # 研究版里 m._debug 是 no-op,诊断日志要直接写 app.log
           try:
               (m._app or adsk.core.Application.get()).log("[FuzzyCAD MYPATCH] " + msg)
           except Exception:
               pass

       def accept(mark):
           # ... 自己的逻辑 ...
           return old_accept(mark)

       m._accept = accept
   ```

2. 在 `FuzzyCAD.py` 里注册,**把它放在能得到正确包裹层级的位置**
   (越靠后 = 越外层 = 越先拦截):

   ```python
   _mypatch = _load("fuzzycad_mypatch", "tools/fuzzycad_mypatch.py")
   _mypatch.install(_legacy)
   ```

   - 第一个参数是模块名(`sys.modules` 的 key),**不带 `.py`、不带文件夹**。
   - 第二个参数是相对 `FuzzyCAD.py` 的**文件路径**,要带文件夹前缀。

3. 常见可包装 / 可用的钩子:`m._accept`、`m._redraw_marks`、`m._send_state`、
   `m._DRAW[<tool>]`、`m.FuzzyCommandCreated`、`m.FuzzyInputChanged`、
   `m.FuzzyPreview`、`m.PaletteHTMLHandler`;绘制辅助 `m._visual_stroke` /
   `m._sketchy` / `m._group` / `m._solid`;几何辅助 `m._bbox_center_size` /
   `m._op_matrix` / `m._design` / `m._body`。

### 约定

- **不要**在补丁里 `import` 另一个补丁;所有共享状态都放到 `m` 上。
- 不确定性最终存于 `Design.attributes`(组 `FuzzyCAD`);内部单位是**厘米**。
- 导入的 STEP 是直接建模,没有时间线——`HoleFeatures` 之类会报
  "Environment is not supported",需要走 BaseFeature / 临时 BRep 的路径。
- 决定权归**用户**,不是系统:探测出的耦合要么让用户确认,要么只是可视化提示,
  不做刚性的自动决策。

---

## 开发模式

`FuzzyCAD.py` 顶部的 `DEV_MODE` 控制是否加载 `dev/` 里的追踪补丁,以及
`m._debug` 是不是真的写日志。研究 / 用户实验版保持 `DEV_MODE = False`。
