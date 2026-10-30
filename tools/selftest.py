"""自检：面板 API 全链路（不需要 ComfyUI 在线，也不触发真实出图）。

用法：先启动 launcher.py 或 python -m app.server，再 python tools/selftest.py
"""
from __future__ import annotations

import asyncio
import sys
import time

import aiohttp

BASE = "http://127.0.0.1:8787"
FAILS: list[str] = []
UID = "t" + str(int(time.time()))[-6:]  # 每次自检用独立 UID，避免历史数据干扰


def check(name: str, ok: bool, extra: str = "") -> None:
    print(("  [OK] " if ok else "  [!!] ") + name + (" " + extra if extra else ""))
    if not ok:
        FAILS.append(name)


async def main() -> int:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
        pages = ["/panel", "/overlay", "/web/app.js", "/web/style.css",
                 "/overlay/image", "/overlay/progress", "/overlay/live2d",
                 "/overlay/quota", "/overlay/stats", "/overlay/caption"]
        for path in pages:
            async with s.get(BASE + path) as r:
                check(f"GET {path}", r.status == 200, str(r.status))

        async with s.get(BASE + "/api/state") as r:
            state = await r.json()
        need = {"config", "session", "sizes", "quota", "memory", "knowledge", "known",
                "review", "comfy", "voices", "ports", "overlay_items", "overlay_mode", "about"}
        check("state 字段齐全", need.issubset(state.keys()))
        basic = state["config"]["basic"]
        check("默认尺寸 1216x832", basic["width"] == 1216 and basic["height"] == 832)
        check("默认称呼 主人", basic["master_name"] == "主人")
        quota = state["config"]["quota"]
        check("免费额度预填 50（全场共用）", quota["free_per_session"] == 50, str(quota["free_per_session"]))
        check("礼物换算预填 1 元", quota["gift_per_quota"] == 1, str(quota["gift_per_quota"]))
        check("单人总额度上限 10", quota["max_total_paid"] == 10, str(quota["max_total_paid"]))
        check("继承上限 5", quota["inherit_cap"] == 5, str(quota["inherit_cap"]))
        check("全场免费池存在", state["session"].get("free_pool") == 50, str(state["session"].get("free_pool")))
        check("作者信息", bool((state.get("about") or {}).get("author")))
        check("画面项 6 个", len(state.get("overlay_items") or []) == 6, str(len(state.get("overlay_items") or [])))
        check("Live2D 模型目录可扫描", isinstance(state.get("live2d_models"), list), str(state.get("live2d_models")))
        check("声线列表非空", len(state.get("voices") or []) > 0, str(len(state.get("voices") or [])))

        async with s.get(BASE + "/api/voices") as r:
            voices = await r.json()
        engines = {v["engine"] for v in (voices.get("voices") or [])}
        check("声线含 edge 引擎", "edge" in engines, str(engines))

        # 直播三键必须确认
        async with s.post(BASE + "/api/live/start", json={}) as r:
            check("开播缺确认 -> 400", r.status == 400, str(r.status))
        async with s.post(BASE + "/api/live/start", json={"confirm": True}) as r:
            body = await r.json()
            check("开播成功", body["session"]["running"] is True)

        # 额度（付费）
        async with s.post(BASE + "/api/quota/grant", json={"uid": UID, "name": "测试宝", "delta": 2}) as r:
            body = await r.json()
            check("加付费额度 +2", body.get("user", {}).get("paid") == 2, str(body.get("user")))
        async with s.post(BASE + "/api/quota/set", json={"uid": UID, "name": "测试宝", "paid": 1}) as r:
            body = await r.json()
            check("设置付费额度 1", body.get("user", {}).get("paid") == 1)

        # 卡片
        async with s.post(BASE + "/api/cards/upsert", json={"bucket": "knowledge",
                                                            "card": {"title": "自检卡片", "body": "内容", "tags": ["测试"]}}) as r:
            body = await r.json()
        cid = (body.get("card") or {}).get("id")
        check("知识卡片写入", bool(cid))
        async with s.post(BASE + "/api/cards/delete", json={"bucket": "knowledge", "id": cid}) as r:
            body = await r.json()
        check("知识卡片删除", all(c["id"] != cid for c in body.get("cards", [])))

        # 记忆
        async with s.post(BASE + "/api/memory/set", json={"uid": UID, "name": "测试宝", "note": "喜欢猫"}) as r:
            body = await r.json()
        check("写入观众记忆", "喜欢猫" in (body.get("item", {}).get("notes") or []))

        # 画面形式切换
        async with s.post(BASE + "/api/overlay", json={"mode": "custom"}) as r:
            body = await r.json()
            check("切换到自定义式", body.get("mode") == "custom")
        async with s.post(BASE + "/api/overlay", json={"items": {"caption": False}}) as r:
            body = await r.json()
            check("关闭字幕项", body.get("items", {}).get("caption") is False)
        async with s.get(BASE + "/overlay/caption") as r:
            check("关闭后该地址 404", r.status == 404, str(r.status))
        async with s.post(BASE + "/api/overlay", json={"mode": "integrated", "items": {"caption": True}}) as r:
            await r.json()

        # 关播断 API / 新一场
        async with s.post(BASE + "/api/live/stop", json={"confirm": True}) as r:
            body = await r.json()
            check("关播成功", body["session"]["running"] is False)
        async with s.get(BASE + "/api/balance") as r:
            body = await r.json()
        check("关播后 API 断开", body.get("ok") is False)
        async with s.post(BASE + "/api/live/new", json={"confirm": True}) as r:
            body = await r.json()
        check("新一场重置免费池与统计",
              body["session"]["revenue_yuan"] == 0 and body["session"]["free_used"] == 0
              and body["session"]["free_pool"] == 50)

    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
