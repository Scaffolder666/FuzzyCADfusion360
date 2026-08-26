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

### 可视化状态机(单一真相源)

一个 mark 的"该画什么"统一由 `m._mark_phase(mark)`(`core/fuzzycad_mark_phase.py`)
决定,返回三个相之一:

| 相 | 何时 | 表现 |
|---|---|---|
| `editing` | 正在调(新命令,或点卡片经 `m._active_edit_id` 重开编辑) | 干净的实时预览;漫画/幽灵外观被抑制 |
| `proposed` | 已提交、open、等 Accept/Reject | 各工具的漫画 / cel-shaded 不确定外观 |
| `resolved` | 已 Accept / Reject | 清空,不画 |

各可视化层(如 `visuals/fuzzycad_fuzzy_boundary.py`)应**读这个相**来开关图层,
而不是各自散落地判断 `is_live`。**fillet 特殊**:它生成新几何,带自己的半透明实体
预览,从 editing 一直显示到 Accept(永不进入漫画外观),并缓存在独立图层里,只在
半径真变时重建——不随每帧重绘 / 转相机重算(见 `tools/fuzzycad_fillet_stability.py`
的 `sync_fillet_solids`)。

### 计算与可视化约定(生成类工具必读)

工具分两族:**变换类**(Move/Rotate/Scale/Axis Rotate,对同一 body 套矩阵)和
**生成类**(Fillet/Extrude/Hole,用 kernel 生成新几何)。生成类容易把机器拖崩,
必须守两条铁律:

**铁律 1 —— 拖动时(每帧)绝不做 kernel 计算。**
`FuzzyPreview.executePreview` 与 `_redraw_marks` 每秒跑几十次。这两处只许:矩阵变换、
偏移算点、画 `_sketchy` 线、从已知点建 CustomGraphics 三角面。**禁止**:加/删 Feature、
fillet/extrude/布尔、`addBRepBody`(会重新三角化)。

**铁律 2 —— kernel 计算只在"落定点"发生,且必须缓存。**
落定点 = 松手 / 改数字 / Accept。算完按参数值缓存;值没变则重绘**永不重算、永不重新
三角化**(见 fillet 的 `sync_fillet_solids`:候选体放独立持久图层,只在半径变时重建)。

**两级可视化**:近似(剪影 / 线框 / 偏移线,便宜、跟手,拖动时用)vs 精确(kernel 算的
半透明实心体,贵,只在落定后缓存着显示)。变换类的"精确"就是 body 套矩阵,零 kernel,
所以能全程实时;生成类拖动只能给近似。

**各工具现状**:
- Move/Rotate/Scale/Axis Rotate — 剪影即精确,合规。
- Fillet — 拖动画弧线 ghost;落定后 `temporary_fillet` 算一次、缓存半透明实心体(独立图层)。
- Extrude — **就是把一个面沿法向平移**,预览是"平移的面 + 侧壁"(对平面几何精确),用
  CustomGraphics 三角面填充,**全程零 kernel**;真布尔只在 `_accept` 发生。切勿再用真
  Feature 当预览(那正是它崩过的原因)。
- Hole — 预览用 `TemporaryBRepManager.createCylinderOrCone`(临时体,不建 Feature、不布尔);
  accept 走 BaseFeature + 临时体。安全。

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
