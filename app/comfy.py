"""ComfyUI 客户端：状态探测 + 提交 API 工作流 + WS 进度 + 取图。

注入逻辑不认死节点 id，而是按图结构自动找：模型加载器、KSampler 的正/负提示词、
尺寸节点、保存前缀。换工作流不用改代码。
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import pathlib
import uuid

import aiohttp

log = logging.getLogger("comfy")


class ComfyError(RuntimeError):
    pass


class Comfy:
    def __init__(self, base_url: str, timeout_sec: float = 240.0) -> None:
        self.base = (base_url or "http://127.0.0.1:8188").rstrip("/")
        self.ws_base = self.base.replace("https://", "wss://").replace("http://", "ws://")
        self.timeout_sec = timeout_sec

    async def status(self) -> dict:
        info = {"online": False, "url": self.base, "models": [], "error": None}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base}/system_stats",
                                       timeout=aiohttp.ClientTimeout(total=4)) as r:
                    if r.status != 200:
                        info["error"] = f"HTTP {r.status}"
                        return info
                    stats = await r.json()
                # 分发包不查模型清单：这一项会把调用者本机装了哪些模型原样暴露到面板上。
                # 需要自己核对模型的作者，直接看 ComfyUI 的模型目录就行，不必靠面板。
                info["models"] = []
            info["online"] = True
            info["system"] = {"os": stats.get("system", {}).get("os"),
                              "comfyui_version": stats.get("system", {}).get("comfyui_version")}
        except Exception as exc:
            info["error"] = str(exc)[:200]
        return info

    def build(self, template: dict, *, positive: str, negative: str, seed: int, steps: int,
              cfg: float, width: int, height: int, checkpoint: str, sampler: str,
              scheduler: str, prefix: str = "painter") -> dict:
        wf = copy.deepcopy(template)
        if checkpoint:  # 空串表示"用工作流里原来写的那个"，别写空值把 ComfyUI 打崩
            for node in wf.values():
                ins = node.get("inputs") or {}
                for key in ("ckpt_name", "unet_name", "model_name"):
                    if key in ins and isinstance(ins[key], str):
                        ins[key] = checkpoint
        samplers = [nid for nid, n in wf.items() if "KSampler" in str(n.get("class_type", ""))]
        if not samplers:
            raise ComfyError("工作流里找不到 KSampler")
        pos_ids, neg_ids = set(), set()
        for nid in samplers:
            ins = wf[nid].get("inputs") or {}
            for key, bag in (("positive", pos_ids), ("negative", neg_ids)):
                link = ins.get(key)
                if isinstance(link, list) and link:
                    bag.add(str(link[0]))
        if not pos_ids or not neg_ids:
            raise ComfyError("没识别到正/负提示词节点")
        for nid in pos_ids:
            wf[nid]["inputs"]["text"] = positive
        for nid in neg_ids:
            wf[nid]["inputs"]["text"] = negative
        for node in wf.values():
            ins = node.get("inputs") or {}
            if "width" in ins and "height" in ins:
                ins["width"], ins["height"] = int(width), int(height)
            elif "width_override" in ins:
                ins["width_override"], ins["height_override"] = int(width), int(height)
        for idx, nid in enumerate(samplers):
            ins = wf[nid].get("inputs") or {}
            for key, value in (("seed", int(seed)), ("noise_seed", int(seed)), ("steps", int(steps)),
                               ("cfg", float(cfg)), ("sampler_name", sampler),
                               ("scheduler", scheduler)):
                if key in ins:
                    ins[key] = value
            if idx < len(samplers) - 1 and "end_at_step" in ins:
                ins["end_at_step"] = max(1, int(steps) // 3)
        for node in wf.values():
            ins = node.get("inputs") or {}
            if "filename_prefix" in ins:
                ins["filename_prefix"] = prefix
        return wf

    async def generate(self, template: dict, out_dir: pathlib.Path, on_progress=None, **params) -> list[str]:
        wf = self.build(template, **params)
        cid = uuid.uuid4().hex
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base}/prompt",
                                        json={"prompt": wf, "client_id": cid},
                                        timeout=aiohttp.ClientTimeout(total=30)) as r:
                    body = await r.text()
                    if r.status != 200:
                        raise ComfyError(f"/prompt {r.status}: {body[:300]}")
                    pid = json.loads(body)["prompt_id"]
                try:
                    await asyncio.wait_for(self._watch(session, cid, pid, on_progress), self.timeout_sec)
                except asyncio.TimeoutError as exc:
                    raise ComfyError(f"出图超时 {self.timeout_sec:.0f}s") from exc
                return await self._fetch(session, pid, out_dir)
        except aiohttp.ClientError as exc:
            raise ComfyError(f"连不上 ComfyUI({self.base}): {exc}") from exc

    async def _watch(self, session, cid: str, pid: str, on_progress=None) -> None:
        url = f"{self.ws_base}/ws?clientId={cid}"
        async with session.ws_connect(url, heartbeat=None, max_msg_size=0) as ws:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    ev = json.loads(msg.data)
                except Exception:
                    continue
                kind = ev.get("type")
                data = ev.get("data") or {}
                if kind == "progress" and on_progress and data.get("prompt_id") in (None, pid):
                    try:
                        await on_progress(int(data.get("value") or 0), max(int(data.get("max") or 1), 1))
                    except Exception:
                        pass
                if kind == "executing" and data.get("node") is None and data.get("prompt_id") == pid:
                    return
                if kind == "execution_success" and data.get("prompt_id") == pid:
                    return
                if kind == "execution_error":
                    raise ComfyError(f"执行错误: {str(data.get('exception_message') or data)[:300]}")

    async def _fetch(self, session, pid: str, out_dir: pathlib.Path) -> list[str]:
        async with session.get(f"{self.base}/history/{pid}",
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            history = await r.json()
        entry = history.get(pid) or {}
        images = []
        for node in (entry.get("outputs") or {}).values():
            images.extend(node.get("images") or [])
        if not images:
            raise ComfyError("history 里没有输出图片")
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for img in images:
            params = {"filename": img.get("filename", ""), "subfolder": img.get("subfolder", ""),
                      "type": img.get("type", "output")}
            async with session.get(f"{self.base}/view", params=params,
                                   timeout=aiohttp.ClientTimeout(total=60)) as r:
                if r.status != 200:
                    raise ComfyError(f"/view {r.status}")
                data = await r.read()
            name = f"raw-{uuid.uuid4().hex[:10]}.png"
            await asyncio.to_thread((out_dir / name).write_bytes, data)
            saved.append(name)
        return saved
