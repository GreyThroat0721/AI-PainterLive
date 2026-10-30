"""B站扫码登录：生成二维码 -> 轮询 -> 拿 SESSDATA，免手动复制 cookie。

流程（B站 passport 官方接口）：
  1. GET /x/passport-login/web/qrcode/generate  拿 qrcode_key + 登录 url
  2. 把 url 画成二维码给主人扫
  3. GET /x/passport-login/web/qrcode/poll?qrcode_key=... 轮询
     code: 0=成功 86101=未扫码 86090=已扫码待确认 86038=已过期
  4. 成功后从 Set-Cookie 取 SESSDATA / buvid3 / DedeUserID
"""
from __future__ import annotations

import io

import aiohttp

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
GENERATE = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

# 允许画成二维码的域名（防 SSRF）：B站各登录域都会出现，按后缀放行
ALLOWED_SUFFIXES = (".bilibili.com", ".biligame.com")
ALLOWED_EXACT = ("bilibili.com", "b23.tv")


async def generate() -> dict:
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(GENERATE, timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json()
    if data.get("code") != 0:
        return {"ok": False, "reason": data.get("message") or "生成二维码失败"}
    payload = data.get("data") or {}
    return {"ok": True, "key": payload.get("qrcode_key"), "url": payload.get("url")}


def render_png(url: str) -> bytes:
    host = (url.split("/")[2].split(":")[0] if "://" in url else "").lower()
    if not (host in ALLOWED_EXACT or host.endswith(ALLOWED_SUFFIXES)):
        raise ValueError(f"不允许的二维码地址: {host}")
    import qrcode
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def poll(key: str) -> dict:
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(POLL, params={"qrcode_key": key},
                               timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json()
            cookies = {c.key: c.value for c in r.cookies.values()}
    payload = data.get("data") or {}
    code = payload.get("code")
    if code == 0:
        sessdata = cookies.get("SESSDATA", "")
        return {"ok": True, "status": "success", "message": "登录成功",
                "sessdata": sessdata,
                "buvid": cookies.get("buvid3", ""),
                "uid": cookies.get("DedeUserID", ""),
                "nickname": payload.get("nickname") or ""}
    mapping = {86101: "等待扫码", 86090: "已扫码，请在手机上确认", 86038: "二维码已过期，请刷新"}
    return {"ok": False, "status": "waiting" if code != 86038 else "expired",
            "code": code, "message": mapping.get(code, payload.get("message") or "未知状态")}
