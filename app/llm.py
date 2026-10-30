"""接入的 LLM API（OpenAI 兼容）：人设、命中式知识注入、聊天、模型列表、连通测试。"""
from __future__ import annotations

import time

import aiohttp

log = None


class LLMError(RuntimeError):
    pass


def build_system_prompt(cfg: dict, store, query: str = "", extra: str = "") -> str:
    basic = cfg["basic"]
    persona = (basic.get("persona") or "").replace("{master}", basic.get("master_name") or "主人")
    parts = [persona]
    # 只注入命中关键词的知识卡片 + 常驻的已知信息，控制上下文长度
    knowledge = store.knowledge_match(query)
    if knowledge:
        parts.append("【相关知识】\n" + knowledge)
    if extra:
        parts.append("【当前情境】\n" + extra)
    parts.append(f"直播规矩：回答要短（一两句），口语化；称呼主人为“{basic.get('master_name') or '主人'}”。")
    parts.append(
        "【情绪动作】每次开口前，先在句首写一个情绪标签，直播间的身体动作会照着它变：\n"
        "[emo:happy] 开心—尾巴拍地、呆毛弹跳；[emo:sad] 难过—尾巴垂下去、低头；\n"
        "[emo:angry] 生气—炸毛、头晃得快；[emo:shy] 害羞—低头、脸红；\n"
        "[emo:surprised] 惊讶—仰头、眼睛睁大；[emo:proud] 得意—抬下巴、慢慢摇尾；[emo:neutral] 平常。\n"
        "需要指定具体表情时再加一个：[exp:脸红] [exp:脸黑] [exp:龇牙] [exp:流口水] [exp:担心] [exp:栗子] [exp:X X] [exp:O O]。\n"
        "标签不会被念出来，只写一个情绪标签就够，别堆砌。例如：[emo:shy] 才、才不是为了你才画的呢。"
    )
    return "\n\n".join(parts)


class LLM:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

    @property
    def conf(self) -> dict:
        return self.cfg["api"]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.conf.get('api_key') or ''}",
                "Content-Type": "application/json"}

    def _base(self) -> str:
        return (self.conf.get("base_url") or "").rstrip("/")

    async def chat(self, messages: list[dict], max_tokens: int = 220) -> dict:
        url = self._base() + "/chat/completions"
        payload = {"model": self.conf.get("model") or "deepseek-chat",
                   "messages": messages, "max_tokens": max_tokens, "temperature": 0.9}
        started = time.time()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=self._headers(),
                                        timeout=aiohttp.ClientTimeout(total=60)) as r:
                    body = await r.text()
                    if r.status != 200:
                        raise LLMError(f"{r.status}: {body[:300]}")
                    data = await r.json()
        except aiohttp.ClientError as exc:
            raise LLMError(f"连不上 API: {exc}") from exc
        text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        usage = data.get("usage") or {}
        tokens = int(usage.get("total_tokens") or 0)
        return {"text": text, "tokens": tokens, "latency_ms": int((time.time() - started) * 1000),
                "cost_yuan": round(tokens / 1000.0 * float(self.conf.get("price_per_1k") or 0), 6)}

    async def list_models(self) -> dict:
        """拉取模型提供商可选模型（OpenAI 兼容 /models）。"""
        url = self._base() + "/models"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._headers(),
                                       timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        return {"ok": False, "reason": f"HTTP {r.status}", "models": []}
                    data = await r.json()
            items = data.get("data") or data.get("models") or []
            ids = sorted({str(it.get("id")) for it in items if isinstance(it, dict) and it.get("id")})
            return {"ok": True, "models": ids}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)[:160], "models": []}

    async def test(self) -> dict:
        """测试模型是否联通：发一句极短请求，返回延迟与回复。"""
        started = time.time()
        try:
            result = await self.chat([{"role": "user", "content": "回一个字：好"}], max_tokens=8)
            return {"ok": True, "model": self.conf.get("model"), "latency_ms": result["latency_ms"],
                    "reply": result["text"][:40]}
        except LLMError as exc:
            return {"ok": False, "reason": str(exc)[:200],
                    "latency_ms": int((time.time() - started) * 1000)}

    async def balance(self) -> dict:
        base = self._base()
        url = self.conf.get("balance_url") or (base + "/user/balance" if "deepseek" in base else "")
        if not url:
            return {"ok": False, "reason": "该 API 未提供余额接口，可在设置里填 balance_url"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._headers(),
                                       timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200:
                        return {"ok": False, "reason": f"HTTP {r.status}"}
                    data = await r.json()
            infos = data.get("balance_infos") or []
            if infos:
                item = infos[0]
                return {"ok": True, "currency": item.get("currency", "CNY"),
                        "total": item.get("total_balance"),
                        "granted": item.get("granted_balance"),
                        "topped": item.get("topped_up_balance")}
            if "balance" in data:
                return {"ok": True, "total": data.get("balance")}
            return {"ok": False, "reason": "返回里没有余额字段"}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)[:160]}
