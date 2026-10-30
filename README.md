# AI 绘画直播间 · Painter Live

作者：**F天青** · B站主页：<https://space.bilibili.com/453851400> · MIT 开源

ComfyUI 出图 + B站弹幕点单 + 全图审核 + 额度体系 + 可视化控制面板（exe 启动、1920×1080、可全屏）。

## 怎么打开（最重要）

**双击 `PainterLive\PainterLive.exe`** 就开了。

```
AI绘画直播间\
  └─ PainterLive\
       └─ PainterLive.exe   ← 双击这个
```

窗口会自己弹出来，地址是 `http://127.0.0.1:8787/panel`。
**不需要装 Python，不需要配环境**。`PainterLive\` 这个文件夹是完整程序，**别把 exe 单独拎出来**，删了旁边的 `_internal\` 就跑不起来。

## 快速开始

```powershell
# 1. 确保 ComfyUI 在跑（默认 http://127.0.0.1:8188）
# 2. 启动
python launcher.py           # 源码运行（先 pip install -r requirements.txt）
# 或双击 PainterLive\PainterLive.exe   （打包版，数据在 exe 同目录 data/）
```

控制面板：<http://127.0.0.1:8787/panel>

## 页面

| 页面 | 内容 |
|---|---|
| 总览 | 余额 / 本场收益（礼物一半）/ 本场花费 / 出图数 / 免费池 / ComfyUI 状态 + **生成进度条** + 开播·关播·新一场（都二次确认） |
| 基础设置 | 人设、主人称呼与B站名、声线（可试听）、语速、**B站扫码登录**、直播间号、API 与模型下拉 + 连通测试、图片尺寸（默认 1216×832）、保存目录、触发词、Live2D 模型（默认「小画」）、联网搜索开关 |
| 绘图额度 | 触发词、**整场共用免费额度（预填 50）**、礼物换算（预填 1 元/张）、单人总额度上限（10）、跨场继承上限（5）、付费未过审是否退还、额度名单增删改 |
| 审核台 | 全图审查开关；通过即放出并改名存档，拒绝不重画且 AI 道歉 |
| 观众记忆 | 弹幕记忆 / 金主记忆、间隔、人数上限、手动增删改 |
| 知识卡片 | 命中式注入（标题/标签命中才读），内容由外部 AI 写入 |
| 已知信息 | 常驻注入的卡片，主人自己写 |
| 直播画面 | 集成式 / 自定义式两种形式 + 全部地址一键复制 |
| 关于 | 作者、B站主页、开源与端口信息 |

## 两种直播画面

**集成式**：一个地址搞定，适合直接当 OBS/直播姬的浏览器源。

```
http://127.0.0.1:8787/overlay
```

**自定义式**：拆成 6 个**透明背景**地址，自己排版叠加，可逐项开关（关掉的不加载、不推送，省资源）。

| 内容 | 地址 |
|---|---|
| 图片展示区 | `/overlay/image` |
| 生成进度条 | `/overlay/progress` |
| Live2D | `/overlay/live2d` |
| 全场免费额度 | `/overlay/quota` |
| 余额 / 收益 / 花费 | `/overlay/stats` |
| 字幕区 | `/overlay/caption` |

集成式的透明变体：`/overlay?bg=0`；隐藏主播位：`/overlay?host=0`。

## 额度规则

- **免费额度是整场共用池**（默认 50 张），开新一场时重新填满
- 礼物换算：每 1 元礼物 = 1 张付费额度（可调）
- 付费额度与 UID 绑定、跨场继承（受继承上限 5 张约束）
- 单人累计获得受总额度上限 10 张约束
- 扣额度顺序：**先付费，再免费池**
- 未过审：免费额度一律扣；付费额度按开关决定是否退还

## B站扫码登录

基础设置 → 接入 → **扫码登录**：点开后用手机 B站 App 扫码确认，SESSDATA 自动写入，不用手动复制 cookie。
走的 B站 passport 官方接口，二维码在本机生成（不经过第三方）。

## 声线

- 默认 **edge-tts**（微软免费音色，19 个中文/粤语/台湾声线，需要联网），合成 mp3 后本机播放
- 离线兜底：Windows SAPI 系统音色
- 面板可一键试听；语速可调（慢/正常/稍快/很快）

## Live2D

模型放 `models/live2d/<模型名>/`，面板里选择即可。分发版不含任何 Live2D 模型，请放入自己的模型。

> 源码运行读项目根下的 `models/live2d/`；exe 运行读 exe 同目录的 `models/live2d/`。
> `build.ps1` 打包后会自动把 `models/` 复制过去，两边内容一致。
画面里给的是透明占位层，可配合桌宠窗口（ghost）叠加显示真实模型。

## 第三方组件与许可

本项目代码以 **MIT** 发布，但 `web/lib/` 下三个文件不是同一套条款，分发前请自己核一遍：

| 文件 | 来源 | 许可 |
|---|---|---|
| `pixi.min.js` | [PixiJS](https://pixijs.com) | MIT |
| `cubism4.min.js` | Live2D Cubism SDK（Web 版） | **Live2D 专有许可** |
| `live2dcubismcore.min.js` | Live2D Cubism Core | **Live2D 专有许可** |

后两个文件用来驱动 Live2D 人物。它们受 [Live2D](https://www.live2d.com) 自己的 SDK 许可约束，**和 MIT 不冲突，但条款不同**。

`live2dcubismcore.min.js` 文件头自带声明，原文是：

```
/**
 * Live2D Cubism Core
 * (C) 2019 Live2D Inc. All rights reserved.
 *
 * This file is licensed pursuant to the license agreement below.
 * This file corresponds to the "Redistributable Code" in the agreement.
 * https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html
 */
```

**它自己写明了属于「Redistributable Code」**，也就是说随应用一起分发是被许可的。但这是 Live2D 的条款，不是 MIT，所以仍然：

- 免费额度、收入门槛、商用范围都由 Live2D 决定，**不是本项目能替你兜底的**
- 稳妥做法：**运行时自行从 Live2D 官网获取**，放进 `web/lib/`，本项目只负责调用
- 如果你打算商用或者收入超过其免费额度线，务必先把授权条款读完

不放这两个文件会怎样：面板和画面照常运行，**只有 Live2D 人物显示不出来**，其余功能（出图、弹幕、礼物、额度、审核）完全不受影响。

## 目录

```
app/          后端（config/store/voice/llm/comfy/bili/pipeline/server）
web/          控制面板（index.html + app.js + style.css）
overlay/      index.html（集成式）+ item.html（自定义式单块）
models/       Live2D 模型
templates/    ComfyUI API 工作流
prompts/      画风前缀
docs/         知识卡片写入说明（给外部 AI 读）
data/         运行时数据（config.json / state.json / tts 缓存）
images/       出图（按日期分目录，0001用户名UID.png）
tools/        selftest.py 自检
launcher.py   exe 入口（pywebview → Edge --app → 浏览器）
build.ps1     打包
```

## 打包

```powershell
.\build.ps1
# 产物：PainterLive\PainterLive.exe
```

## 自检

```powershell
python tools/selftest.py
```

覆盖：10 个页面可达、默认值（1216×832 / 50 / 1 / 10 / 5 / 主人）、免费池、画面项 6 个、模型与声线检测、
开播确认、额度增改、卡片增删、记忆、画面形式切换与开关、关播断 API、新场重置。

## 注意

- `data/config.json` 含 API Key 与 SESSDATA，不要提交到公开仓库
- edge-tts 需要联网；断网会自动降级系统语音
- exe 版的数据在 exe 同目录（dist/PainterLive/data），与源码运行互不影响
