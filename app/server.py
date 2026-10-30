"""HTTP 层：控制面板 API + 直播画面 + SSE + 图片服务。"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import re
import subprocess
import time

from aiohttp import web

from urllib.parse import quote, unquote

from . import bili_login
from . import config as config_mod
from . import voice
from .bili import BiliClient
from .pipeline import Bus, Pipeline
from .store import Store

log = logging.getLogger("server")
BASE = config_mod.BASE
RUNTIME = config_mod.RUNTIME


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


def _sapi_voices() -> list[str]:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Add-Type -AssemblyName System.Speech;"
             "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()"
             "|ForEach-Object{$_.VoiceInfo.Name}"],
            capture_output=True, text=True, timeout=25, encoding="utf-8", errors="ignore",
            creationflags=voice.CREATE_NO_WINDOW)
        return [line.strip() for line in out.stdout.splitlines() if line.strip()]
    except Exception:
        return []


class Panel:
    def __init__(self, cfg: dict, store: Store, bus: Bus, pipe: Pipeline) -> None:
        self.cfg = cfg
        self.store = store
        self.bus = bus
        self.pipe = pipe
        self.comfy_status: dict = {"online": False}
        self.balance: dict = {}
        self.voices: list[str] = []
        self.bili: BiliClient | None = None
        self.bili_task: asyncio.Task | None = None
        self.api_enabled = True

    # ---- B站连接 ----
    async def bili_reconnect(self) -> None:
        if self.bili_task:
            self.bili_task.cancel()
            self.bili_task = None
        if self.bili:
            self.bili.stop()
            self.bili = None
        room = int(self.cfg["bili"].get("room_id") or 0)
        if room <= 0:
            return
        self.bili = BiliClient(room, self.cfg["bili"].get("sessdata", ""),
                               self.cfg["bili"].get("buvid", ""),
                               on_danmaku=self.pipe.on_danmaku,
                               on_gift=self.pipe.on_gift,
                               on_state=self._on_link)
        self.bili_task = asyncio.create_task(self.bili.run())

    async def _on_link(self, ok: bool) -> None:
        self.bus.publish({"type": "link", "connected": ok})

    def state(self) -> dict:
        port = int(self.cfg["server"]["port"]) if "server" in self.cfg else 8787
        base = f"http://127.0.0.1:{port}"
        return {
            "config": config_mod.redact(self.cfg),
            "session": self.store.session_view(),
            "sizes": config_mod.SIZES,
            "quota": self.store.list_quota(),
            "memory": [{"uid": uid, **item} for uid, item in self.store.memory().items()],
            "knowledge": self.store.cards("knowledge"),
            "known": self.store.cards("known"),
            "review": self.store.pending_reviews(),
            "comfy": self.comfy_status,
            "balance": self.balance,
            "voices": self.voices,
            "api_enabled": self.api_enabled,
            "danmaku": "connected" if (self.bili and self.bili.connected) else "off",
            "danmaku_count": self.pipe.danmaku_count,
            "bili_msgs": getattr(self.bili, "msg_count", 0),
            "last_danmaku": self.pipe.last_danmaku,
            "owner_uid": int(getattr(self.bili, "owner_uid", 0) or 0),
            "master_check": getattr(self.pipe, "last_master_check", {}),
            "queue": self.pipe.queue.qsize(),
            "ports": [
                {"name": "控制面板", "url": base + "/panel"},
                {"name": "直播画面 · 集成式", "url": base + "/overlay"},
                {"name": "ComfyUI", "url": self.cfg["comfy"].get("url")},
                {"name": "Live2D 人物 · 透明底", "url": base + "/live2d"},
                {"name": "Live2D 桌宠（外部）", "url": "http://127.0.0.1:34567"},
            ],
            "overlay_items": [
                {**item, "url": base + item["path"],
                 "enabled": (self.cfg["basic"].get("overlay_items") or {}).get(item["key"], True)}
                for item in config_mod.OVERLAY_ITEMS
            ],
            "overlay_mode": self.cfg["basic"].get("overlay_mode", "integrated"),
            "overlay_integrated": self.cfg["basic"].get("overlay_integrated", True),
            "live2d_models": self._scan_live2d(),
            "about": self.cfg.get("about") or {},
        }

    def _scan_live2d(self) -> list[str]:
        root = pathlib.Path((self.cfg["basic"].get("live2d") or {}).get("models_dir") or "")
        found: list[str] = []
        try:
            if root.is_dir():
                for item in root.iterdir():
                    if item.is_dir() and any(item.glob("*.model3.json")):
                        found.append(item.name)
        except Exception:
            pass
        return sorted(found)


# ---------------- 路由 ----------------
async def page_panel(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(BASE / "web" / "index.html", headers={"Cache-Control": "no-store, must-revalidate"})


async def page_overlay(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(BASE / "overlay" / "index.html", headers={"Cache-Control": "no-store, must-revalidate"})


def _model_file(conf: dict) -> str:
    """找出选中模型目录下的 .model3.json —— 前端不能把文件名写字，
    否则换一个模型就加载不了。"""
    try:
        root = pathlib.Path((conf or {}).get("models_dir") or "")
        name = (conf or {}).get("model") or ""
        if not name or not root.is_dir():
            return ""
        hits = sorted((root / name).glob("*.model3.json"))
        return hits[0].name if hits else ""
    except Exception:
        return ""


async def page_live2d(request: web.Request) -> web.StreamResponse:
    # 配置直接注入 HTML：CEF 的 fetch 可能永不返回，页面不能依赖它才能启动
    import json as _json
    try:
        conf = request.app["panel"].cfg["basic"].get("live2d") or {}
    except Exception:  # noqa: BLE001
        conf = {}
    inject = ("<script>window.__L2D_CONF=" + _json.dumps(
        {"enabled": bool(conf.get("enabled")), "model": conf.get("model") or "", "file": _model_file(conf)},
        ensure_ascii=False) + ";</script>")
    html = (BASE / "overlay" / "live2d.html").read_text(encoding="utf-8")
    html = html.replace("</head>", inject + "</head>", 1)
    return web.Response(text=html, content_type="text/html",
                        headers={"Cache-Control": "no-store, must-revalidate"})


async def api_model_file(request: web.Request) -> web.StreamResponse:
    """把 Live2D 模型目录（moc3/贴图/exp3）暴露给渲染页，禁止越界访问。"""
    panel: Panel = request.app["panel"]
    root = pathlib.Path(panel.cfg["basic"]["live2d"]["models_dir"]).resolve()
    target = (root / request.match_info.get("tail", "")).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise web.HTTPNotFound()
    if not target.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(target, headers={"Cache-Control": "max-age=600"})


async def page_index(request: web.Request) -> web.StreamResponse:
    raise web.HTTPFound("/panel")


async def api_state(request: web.Request) -> web.Response:
    return _json(request.app["panel"].state())


async def api_config(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    incoming = await request.json()
    for section, value in (incoming or {}).items():
        if isinstance(value, dict) and isinstance(panel.cfg.get(section), dict):
            panel.cfg[section].update(value)
        else:
            panel.cfg[section] = value
    config_mod.save(panel.cfg)
    panel.pipe.reload()
    if request.query.get("bili") == "1":
        await panel.bili_reconnect()
    return _json({"ok": True, "config": config_mod.redact(panel.cfg)})


async def api_comfy_status(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    panel.comfy_status = await panel.pipe.comfy.status()
    return _json(panel.comfy_status)


async def api_comfy_connect(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    if body.get("url"):
        panel.cfg["comfy"]["url"] = body["url"]
        config_mod.save(panel.cfg)
        panel.pipe.reload()
    panel.comfy_status = await panel.pipe.comfy.status()
    return _json(panel.comfy_status)


async def api_balance(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    if not panel.api_enabled:
        return _json({"ok": False, "reason": "直播已关闭，API 断开"})
    panel.balance = await panel.pipe.llm.balance()
    return _json(panel.balance)


async def api_live(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    action = request.match_info["action"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not body.get("confirm"):
        raise web.HTTPBadRequest(text="需要二次确认")
    if action == "start":
        panel.store.set_running(True)
        panel.api_enabled = True
        await panel.bili_reconnect()
        panel.bus.publish({"type": "live", "running": True})
    elif action == "stop":
        panel.store.set_running(False)
        panel.api_enabled = False
        if panel.bili:
            panel.bili.stop()
            panel.bili = None
        if panel.bili_task:
            panel.bili_task.cancel()
            panel.bili_task = None
        panel.bus.publish({"type": "live", "running": False})
    elif action == "new":
        panel.store.new_session()
        panel.bus.publish({"type": "live", "new_session": True})
    else:
        raise web.HTTPNotFound(text="unknown action")
    return _json({"ok": True, "session": panel.store.session_view()})


async def api_review(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    body = await request.json()
    action = request.match_info["action"]
    rid = str(body.get("id") or "")
    if action == "approve":
        item = await panel.pipe.approve(rid)
    elif action == "reject":
        item = await panel.pipe.reject(rid)
    else:
        raise web.HTTPNotFound(text="unknown action")
    return _json({"ok": bool(item), "item": item})


async def api_quota_remove(request: web.Request) -> web.Response:
    panel = request.app["panel"]
    body = await request.json() if request.can_read_body else {}
    uid = str((body or {}).get("uid") or "").strip()
    if not uid:
        return _json({"ok": False, "reason": "缺 uid"}, status=400)
    ok = panel.store.remove_quota(uid)
    return _json({"ok": ok, "quota": panel.store.list_quota()})


async def api_quota(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    body = await request.json()
    action = request.match_info["action"]
    uid = str(body.get("uid") or "")
    name = str(body.get("name") or "")
    if not uid:
        raise web.HTTPBadRequest(text="uid 必填")
    if action == "set":
        user = panel.store.set_quota(uid, name, body.get("paid"))
    elif action == "grant":
        user = panel.store.grant_paid(uid, name, int(body.get("delta") or 0))
    elif action == "free":
        panel.cfg["quota"]["free_per_session"] = int(body.get("free") or 0)
        config_mod.save(panel.cfg)
        panel.store.state["session"]["free_pool"] = int(body.get("free") or 0)
        panel.store.save()
        user = panel.store.user(uid, name)
    else:
        raise web.HTTPNotFound(text="unknown action")
    return _json({"ok": True, "user": user, "quota": panel.store.list_quota()})


async def api_memory(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    body = await request.json()
    action = request.match_info["action"]
    uid = str(body.get("uid") or "")
    if action == "set":
        item = panel.store.update_memory(uid, body.get("name"), body.get("note"))
        return _json({"ok": True, "item": item})
    if action == "delete":
        panel.store.delete_memory(uid)
        return _json({"ok": True})
    if action == "mode":
        panel.cfg["memory"]["mode"] = body.get("mode") or "danmaku"
        panel.cfg["memory"]["interval_sec"] = int(body.get("interval_sec") or 300)
        panel.cfg["memory"]["max_people"] = int(body.get("max_people") or 100)
        config_mod.save(panel.cfg)
        return _json({"ok": True, "memory": panel.cfg["memory"]})
    raise web.HTTPNotFound(text="unknown action")


async def api_cards(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    body = await request.json()
    action = request.match_info["action"]
    bucket = body.get("bucket") or "knowledge"
    if bucket not in ("knowledge", "known"):
        raise web.HTTPBadRequest(text="bucket 只能是 knowledge / known")
    if action == "upsert":
        card = panel.store.upsert_card(bucket, body.get("card") or {})
        return _json({"ok": True, "card": card, "cards": panel.store.cards(bucket)})
    if action == "delete":
        panel.store.delete_card(bucket, str(body.get("id") or ""))
        return _json({"ok": True, "cards": panel.store.cards(bucket)})
    raise web.HTTPNotFound(text="unknown action")


async def api_inject(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    body = await request.json()
    uname = str(body.get("uname") or "测试观众")
    uid = str(body.get("uid") or "999")
    text = str(body.get("text") or "")
    if body.get("gift"):
        await panel.pipe.on_gift(uname, uid, str(body.get("gift")), int(body.get("num") or 1),
                                 int(body.get("coin") or 1000))
        return _json({"ok": True, "kind": "gift"})
    await panel.pipe.on_danmaku(uname, uid, text)
    return _json({"ok": True, "queue": panel.pipe.queue.qsize()})


async def api_models(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    result = await panel.pipe.llm.list_models()
    result["current"] = panel.cfg["api"].get("model")
    return _json(result)


async def api_model_test(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    if not panel.api_enabled:
        return _json({"ok": False, "reason": "直播已关闭，API 断开"})
    return _json(await panel.pipe.llm.test())


async def api_voices(request: web.Request) -> web.Response:
    return _json({"voices": voice.list_voices()})


async def api_voice_test(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    voice_id = body.get("voice") or panel.cfg["basic"].get("voice") or "zh-CN-XiaoyiNeural"
    engine = body.get("engine") or panel.cfg["basic"].get("voice_engine") or "edge"
    text = body.get("text") or "你好呀，我是你的直播间画手，这个声音好听吗？"
    path = await voice.synth(text, voice_id, engine, panel.cfg["basic"].get("voice_rate") or "+0%")
    if not path:
        return _json({"ok": False, "reason": "合成失败：edge-tts 需要联网，离线请选系统声线"})
    fell_back = path.suffix == ".wav" and engine == "edge"
    if fell_back:
        # 试听不播降级音：否则主人会以为"这个音色就是这个机械女声"
        return _json({"ok": False, "fallback": True,
                      "reason": f"{voice_id} 这次没合成出来（微软服务抖动），再点一次试试，或换个音色"})
    asyncio.create_task(voice.play(path))
    return _json({"ok": True, "url": f"/tts/{path.name}", "voice": voice_id, "engine": engine})


async def api_tts_file(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    if "/" in name or "\\" in name or ".." in name:
        raise web.HTTPForbidden(text="bad name")
    path = voice.TTS_DIR / name
    if not path.exists():
        raise web.HTTPNotFound(text="no such tts")
    return web.FileResponse(path)


async def page_overlay_item(request: web.Request) -> web.StreamResponse:
    panel: Panel = request.app["panel"]
    key = request.match_info["item"]
    if key not in {item["key"] for item in config_mod.OVERLAY_ITEMS}:
        raise web.HTTPNotFound(text="unknown overlay item")
    if not panel.cfg["basic"].get("overlay_items", {}).get(key, True):
        raise web.HTTPNotFound(text="该画面项已在设置中关闭")
    return web.FileResponse(BASE / "overlay" / "item.html", headers={"Cache-Control": "no-store, must-revalidate"})


async def api_overlay(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    basic = panel.cfg["basic"]
    if body.get("mode") in ("integrated", "custom"):
        basic["overlay_mode"] = body["mode"]
    if isinstance(body.get("items"), dict):
        basic["overlay_items"] = {**basic.get("overlay_items", {}), **body["items"]}
    if "integrated" in body:
        basic["overlay_integrated"] = bool(body["integrated"])
    config_mod.save(panel.cfg)
    return _json({"ok": True, "mode": basic["overlay_mode"],
                  "integrated": basic.get("overlay_integrated", True),
                  "items": basic["overlay_items"]})


async def api_bili_qr(request: web.Request) -> web.Response:
    result = await bili_login.generate()
    if not result.get("ok"):
        return _json(result)
    return _json({"ok": True, "key": result["key"], "url": result["url"],
                  "img": "/api/bili/qrcode/img?url=" + quote(result["url"], safe="")})


async def api_bili_qr_img(request: web.Request) -> web.Response:
    url = unquote(request.query.get("url", ""))
    try:
        png = bili_login.render_png(url)
    except Exception as exc:
        raise web.HTTPBadRequest(text=str(exc))
    return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "no-store"})


async def api_bili_qr_poll(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    key = request.query.get("key", "")
    if not key:
        raise web.HTTPBadRequest(text="missing key")
    result = await bili_login.poll(key)
    if result.get("ok") and result.get("sessdata"):
        panel.cfg["bili"]["sessdata"] = result["sessdata"]
        if result.get("buvid"):
            panel.cfg["bili"]["buvid"] = result["buvid"]
        config_mod.save(panel.cfg)
        await panel.bili_reconnect()
    safe = {k: v for k, v in result.items() if k != "sessdata"}
    safe["sessdata_set"] = bool(result.get("sessdata"))
    return _json(safe)


async def api_open_folder(request: web.Request) -> web.Response:
    panel: Panel = request.app["panel"]
    body = await request.json() if request.can_read_body else {}
    target = str((body or {}).get("path") or panel.cfg["basic"].get("image_dir") or "")
    path = pathlib.Path(target)
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(["explorer", str(path)])
        return _json({"ok": True, "path": str(path)})
    except Exception as exc:
        return _json({"ok": False, "reason": str(exc)[:200]})


async def api_img(request: web.Request) -> web.StreamResponse:
    date = request.match_info["date"]
    name = request.match_info["name"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or "/" in name or "\\" in name or ".." in name:
        raise web.HTTPForbidden(text="bad path")
    path = pathlib.Path(request.app["panel"].cfg["basic"].get("image_dir")) / date / name
    if not path.exists():
        raise web.HTTPNotFound(text="no such image")
    return web.FileResponse(path)


async def api_rawimg(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    if "/" in name or "\\" in name or ".." in name:
        raise web.HTTPForbidden(text="bad name")
    path = RUNTIME / "images" / "_raw" / name
    if not path.exists():
        raise web.HTTPNotFound(text="no such raw image")
    return web.FileResponse(path)


async def events(request: web.Request) -> web.StreamResponse:
    bus: Bus = request.app["bus"]
    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })
    await resp.prepare(request)
    q = bus.subscribe()
    try:
        await resp.write(b": painter-live\n\n")
        for ev in bus.recent[-20:]:
            await resp.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
                continue
            await resp.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        bus.unsubscribe(q)
    return resp


def create_app() -> web.Application:
    cfg = config_mod.load()
    port = int(cfg.get("server", {}).get("port") or 8787)
    cfg.setdefault("server", {"host": "127.0.0.1", "port": port})
    store = Store(cfg)
    bus = Bus()
    pipe = Pipeline(cfg, store, bus)
    panel = Panel(cfg, store, bus, pipe)

    app = web.Application(client_max_size=8 * 1024 * 1024)
    app["cfg"] = cfg
    app["store"] = store
    app["bus"] = bus
    app["pipe"] = pipe
    app["panel"] = panel

    app.router.add_get("/", page_index)
    app.router.add_get("/panel", page_panel)
    app.router.add_get("/overlay", page_overlay)
    app.router.add_get("/live2d", page_live2d)
    app.router.add_get("/model/{tail:.*}", api_model_file)
    app.router.add_get("/events", events)
    app.router.add_get("/img/{date}/{name}", api_img)
    app.router.add_get("/rawimg/{name}", api_rawimg)
    app.router.add_static("/web", BASE / "web", show_index=False)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/config", api_config)
    app.router.add_get("/api/comfy/status", api_comfy_status)
    app.router.add_post("/api/comfy/connect", api_comfy_connect)
    app.router.add_get("/api/balance", api_balance)
    app.router.add_post("/api/live/{action}", api_live)
    app.router.add_post("/api/review/{action}", api_review)
    app.router.add_post("/api/quota/remove", api_quota_remove)
    app.router.add_post("/api/quota/{action}", api_quota)
    app.router.add_post("/api/memory/{action}", api_memory)
    app.router.add_post("/api/cards/{action}", api_cards)
    app.router.add_post("/api/inject", api_inject)
    app.router.add_post("/api/open-folder", api_open_folder)
    app.router.add_get("/api/bili/qrcode", api_bili_qr)
    app.router.add_get("/api/bili/qrcode/img", api_bili_qr_img)
    app.router.add_get("/api/bili/qrcode/poll", api_bili_qr_poll)
    app.router.add_get("/api/models", api_models)
    app.router.add_post("/api/model/test", api_model_test)
    app.router.add_get("/api/voices", api_voices)
    app.router.add_post("/api/voice/test", api_voice_test)
    app.router.add_get("/tts/{name}", api_tts_file)
    app.router.add_get("/overlay/{item}", page_overlay_item)
    app.router.add_post("/api/overlay", api_overlay)
    assets_dir = RUNTIME / "assets"
    if assets_dir.is_dir():
        app.router.add_static("/assets", assets_dir, show_index=False)

    async def on_startup(_app: web.Application) -> None:
        pipe.load_assets()
        pipe.start()
        if store.state["session"].get("running"):
            await panel.bili_reconnect()

        async def _warm() -> None:
            """耗时探测放后台：先让端口通，再补语音列表与 ComfyUI 状态。"""
            try:
                panel.voices = await asyncio.to_thread(voice.load_sapi_voices)
            except Exception:
                pass
            try:
                panel.comfy_status = await pipe.comfy.status()
            except Exception:
                pass

        _app["warm_task"] = asyncio.create_task(_warm())
        log.info("控制面板: http://127.0.0.1:%s/panel", port)

    async def on_cleanup(_app: web.Application) -> None:
        if panel.bili:
            panel.bili.stop()
        if panel.bili_task:
            panel.bili_task.cancel()
        if pipe.worker:
            pipe.worker.cancel()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    cfg = config_mod.load()
    host = cfg.get("server", {}).get("host", "127.0.0.1")
    port = int(cfg.get("server", {}).get("port") or 8787)
    web.run_app(create_app(), host=host, port=port, print=None)


if __name__ == "__main__":
    main()
