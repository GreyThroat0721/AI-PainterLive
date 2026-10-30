# 给外部 AI 的知识卡片写入说明

这份文档是给"有编程能力的 AI"读的：如何往本项目的知识库里写卡片。
人类主人只需要把本文件丢给 AI，让它照做。

## 一、知识卡片存在哪

`data/state.json` 的 `knowledge.cards` 数组，结构：

```json
{
  "id": "8位十六进制（可省略，服务端自动生成）",
  "title": "卡片标题，例如：梗知识 / 游戏知识",
  "tags": ["梗", "游戏"],
  "body": "卡片正文，纯文本，建议 500 字以内",
  "enabled": true
}
```

## 二、两种写入方式

### 方式 A：走 HTTP API（推荐，服务运行时）

服务默认监听 `http://127.0.0.1:8787`（端口见 `data/config.json` 的 `server.port`）。

新增/更新：

```bash
curl -X POST http://127.0.0.1:8787/api/cards/upsert \
  -H "Content-Type: application/json" \
  -d '{"bucket":"knowledge","card":{"title":"梗知识","tags":["梗"],"body":"……","enabled":true}}'
```

带 `id` 就更新，不带就新增。删除：

```bash
curl -X POST http://127.0.0.1:8787/api/cards/delete \
  -H "Content-Type: application/json" \
  -d '{"bucket":"knowledge","id":"abcd1234"}'
```

`bucket` 取值：`knowledge`（知识卡片）或 `known`（已知信息，结构同卡片）。

### 方式 B：直接改文件（服务停止时）

编辑 `data/state.json` 的 `knowledge.cards`（或 `known.cards`），保持合法 JSON。
**改完必须重启服务**，否则内存里的旧状态会覆盖你的修改。

## 三、写作要求

1. 一张卡片只讲一件事，标题用名词短语（「直播间黑话」「Anima 模型参数」）。
2. `body` 是给 AI 自己读的上下文，写事实、写结论，别写"请看某链接"。
3. 停用比删除安全：不确定时把 `enabled` 设成 `false`。
4. 涉及敏感/违规内容会被主人直接删，别写。
5. 每张卡片控制在 500 字内；总量建议不超过 20 张，别把上下文塞爆。

## 四、服务端怎么用这些卡片

每次调用接入的 LLM API 时，会把所有 `enabled: true` 的卡片拼进 system prompt：

```
【已知信息与知识】
[梗知识] ……
[已知·今日安排] ……
```

所以卡片写得越结构化、越短，AI 表现得越稳。
