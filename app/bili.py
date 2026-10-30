"""B站直播间接入：弹幕 + 礼物（SEND_GIFT）+ 断线重连。零第三方依赖。

金瓜子换算：1000 gold coin = 1 元（B站通用），收益按礼物价值一半计入场次。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import pathlib
import struct
import sys
import time
import zlib
from urllib.parse import urlencode

import aiohttp

log = logging.getLogger("bili")

# B站 wbi 签名混表：getDanmuInfo 现在必须带 wts + w_rid，否则返回 -352、token 为空，
# 表现就是"ws 连上了但一条弹幕都收不到，还反复重连"
WBI_MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9,
             42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1,
             60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]

# ---------------------------------------------------------------- SEND_GIFT 的 protobuf
#
# 2026-10 实测：cmd=SEND_GIFT 的 data 已经不是完整 JSON，只剩 {"dmscore":…, "pb":"<base64>"}。
# 用户和礼物全在 pb 里。不解开就是 uname 空、giftName 空、价格 0、感谢语整段被跳过 ——
# 表现就是"礼物收到了但面板显示 ×1（¥0，额度+0），一句话都不说"。
# 字段路径（真机样本核对过）：
#   /2     用户名          /8/1   uid          /8/5  粉丝牌等级
#   /10/1  giftId          /10/2  礼物名        /10/3  数量
#   /10/5  单价(金瓜子)     /10/8  支付类型 gold 付费 / silver 免费

# 诊断落盘：日志在某些启动方式下看不到，文件一定看得见。
# exe 下 __file__ 是 _internal 里的虚拟路径，绝不能拿它拼目录。
_RUNTIME_DIR = (pathlib.Path(sys.executable).parent if getattr(sys, "frozen", False)
                else pathlib.Path(__file__).resolve().parent.parent)
_SEEN_CMD: set[str] = set()


def _append(name: str, line: str) -> None:
    try:
        p = _RUNTIME_DIR / "data" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        log.exception("写 %s 失败", name)


def _dump_gift(data: dict) -> None:
    """先把礼物原始 data 落盘再解析 —— 读不出来的那天，文件里得有原件。"""
    _append("gift_dump.jsonl", json.dumps({"t": time.strftime("%F %T"), "data": data},
                                          ensure_ascii=False))


def _pb_varint(buf: bytes, i: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7
        if shift > 63:
            break
    raise ValueError("varint 越界")


def _pb_split(buf: bytes) -> dict[int, list[bytes]]:
    """按 wire format 拆成 {字段号: [原始值…]}。

    wire2 一律留 bytes —— 这时候还分不清是字符串还是子消息，要不要解码由调用方决定。
    """
    out: dict[int, list[bytes]] = {}
    i = 0
    n = len(buf)
    while i < n:
        key, i = _pb_varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            v, i = _pb_varint(buf, i)
            out.setdefault(field, []).append(str(v).encode())
        elif wire == 2:
            ln, i = _pb_varint(buf, i)
            out.setdefault(field, []).append(buf[i:i + ln])
            i += ln
        elif wire == 5:
            i += 4
        elif wire == 1:
            i += 8
        else:  # 不认识的 wire type，后面的字节没法再对齐，直接截断
            break
    return out


def _pb_str(raw: bytes | None) -> str:
    """子消息硬解出来会带控制字符，那种一个字节都不认。"""
    if not raw:
        return ""
    try:
        s = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    return s if all(ord(c) >= 32 or c in "\r\n\t" for c in s) else ""


def _gift_from_pb(b64: str) -> dict:
    top = _pb_split(base64.b64decode(b64))
    user = _pb_split(top[8][0]) if top.get(8) else {}
    g = _pb_split(top[10][0]) if top.get(10) else {}

    def s(m: dict, k: int) -> str:
        return _pb_str((m.get(k) or [b""])[0])

    def n(m: dict, k: int) -> int:
        try:
            return int(s(m, k) or 0)
        except ValueError:
            return 0

    return {
        "uname": s(top, 2),
        "uid": s(user, 1),
        "gift": s(g, 2),
        "num": n(g, 3) or 1,
        "price": n(g, 5),
        "coin_type": s(g, 8),
    }


OP_HANDSHAKE = 7
OP_HEARTBEAT = 2
OP_MESSAGE = 5
OP_AUTH_REPLY = 8
HEADER = struct.Struct(">IHHII")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def pack(op: int, body: bytes = b"") -> bytes:
    return HEADER.pack(HEADER.size + len(body), HEADER.size, 1, op, 1) + body


def unpack(raw: bytes) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytes]] = []
    off, total = 0, len(raw)
    while off + HEADER.size <= total:
        plen, hlen, ver, op, _seq = HEADER.unpack_from(raw, off)
        if plen < HEADER.size or off + plen > total:
            break
        body = raw[off + hlen: off + plen]
        off += plen
        if ver == 2:
            try:
                out.extend(unpack(zlib.decompress(body)))
            except zlib.error:
                pass
        elif ver == 3:
            try:
                import brotli
                out.extend(unpack(brotli.decompress(body)))
            except Exception:
                pass
        else:
            out.append((op, body))
    return out


class BiliClient:
    def __init__(self, room_id: int, sessdata: str = "", buvid: str = "",
                 on_danmaku=None, on_gift=None, on_state=None) -> None:
        self.room_input = int(room_id or 0)
        self.room_id = self.room_input
        self.sessdata = sessdata or ""
        self.buvid = buvid or ""
        self.on_danmaku = on_danmaku
        self.on_gift = on_gift
        self.on_state = on_state
        self.connected = False
        self.msg_count = 0
        self.uid = 0
        self.owner_uid = 0   # 主播（主人）的 uid，从 Room/get_info 拿，比手填靠谱
        self._last_state = None
        self._stop = asyncio.Event()
        self._headers = {"User-Agent": UA, "Referer": "https://live.bilibili.com/"}
        cookie = []
        if self.sessdata:
            cookie.append(f"SESSDATA={self.sessdata}")
        if self.buvid:
            cookie.append(f"buvid3={self.buvid}")
        if cookie:
            self._headers["Cookie"] = "; ".join(cookie)

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        backoff = 3
        while not self._stop.is_set():
            try:
                await self._once()
                backoff = 3
                await asyncio.sleep(1)  # 连接秒断也别立刻重连成风暴
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("弹幕连接异常: %s", exc)
                await self._state(False)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 60)

    async def _state(self, ok: bool) -> None:
        self.connected = ok
        if ok == self._last_state:
            return  # 状态没变就不通知，避免刷屏"弹幕已连接"
        self._last_state = ok
        if self.on_state:
            try:
                await self.on_state(ok)
            except Exception:
                log.debug("state 回调失败", exc_info=True)

    async def _once(self) -> None:
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get("https://api.live.bilibili.com/room/v1/Room/room_init",
                                   params={"id": self.room_input},
                                   timeout=aiohttp.ClientTimeout(total=10)) as r:
                init = await r.json()
            if init.get("code") == 0:
                self.room_id = int(init["data"]["room_id"])
            # 主播 uid 才是「主人」：手填会错，这个接口直接给真值
            try:
                async with session.get("https://api.live.bilibili.com/room/v1/Room/get_info",
                                       params={"room_id": self.room_id},
                                       timeout=aiohttp.ClientTimeout(total=10)) as r:
                    gi = await r.json()
                if gi.get("code") == 0:
                    self.owner_uid = int((gi.get("data") or {}).get("uid") or 0)
                    log.info("房间主播 uid=%s（自动认主）", self.owner_uid)
            except Exception:
                log.warning("取主播 uid 失败", exc_info=True)
            signed = await self._wbi(session, {"id": self.room_id, "type": 0,
                                                 "web_location": "444.8"})
            async with session.get("https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo",
                                   params=signed,
                                   timeout=aiohttp.ClientTimeout(total=10)) as r:
                info = await r.json()
            if info.get("code") != 0:
                log.warning("getDanmuInfo 失败 code=%s（token 拿不到，连上也收不到弹幕）", info.get("code"))
            data = info.get("data") or {}
            token = data.get("token") or ""
            hosts = data.get("host_list") or [{"host": "broadcastlv.chat.bilibili.com", "wss_port": 443}]
            last_error = None
            for host in hosts:
                try:
                    port = int(host.get("wss_port") or 443)
                    async with session.ws_connect(f"wss://{host.get('host')}:{port}/sub",
                                                  origin="https://live.bilibili.com",
                                                  heartbeat=None, max_msg_size=0) as ws:
                        auth = {"uid": self.uid, "roomid": self.room_id, "protover": 2,
                                "platform": "web", "type": 2, "key": token}
                        await ws.send_bytes(pack(OP_HANDSHAKE, json.dumps(auth).encode()))
                        await self._state(True)
                        log.info("弹幕已连接 room=%s host=%s", self.room_id, host.get("host"))
                        beat = asyncio.create_task(self._heartbeat(ws))
                        try:
                            async for msg in ws:
                                if msg.type == aiohttp.WSMsgType.BINARY:
                                    await self._dispatch(msg.data)
                                elif msg.type == aiohttp.WSMsgType.TEXT:
                                    await self._dispatch(msg.data.encode())
                                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                    break
                        finally:
                            beat.cancel()
                    return
                except Exception as exc:
                    last_error = exc
                    log.warning("host %s 失败: %s", host.get("host"), exc)
            if last_error:
                raise last_error

    async def _wbi(self, session, params: dict) -> dict:
        """B站 wbi 签名：nav 拿 img/sub key，混表重排，再 md5。"""
        try:
            async with session.get("https://api.bilibili.com/x/web-interface/nav",
                                   timeout=aiohttp.ClientTimeout(total=10)) as r:
                nav = await r.json()
            mid = (nav.get("data") or {}).get("mid")
            if mid:
                self.uid = int(mid)
            wbi = ((nav.get("data") or {}).get("wbi_img") or {})
            img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
            sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
            if not img or not sub:
                return params
            raw = img + sub
            mixin = "".join(raw[i] for i in WBI_MIXIN if i < len(raw))[:32]
            merged = {k: str(v) for k, v in {**params, "wts": int(time.time())}.items()}
            ordered = {k: merged[k] for k in sorted(merged)}
            query = urlencode(ordered)
            ordered["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
            return ordered
        except Exception as exc:
            log.debug("wbi 签名失败: %s", exc)
            return params

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                await ws.send_bytes(pack(OP_HEARTBEAT, b"[object Object]"))
            except Exception:
                return

    async def _dispatch(self, raw: bytes) -> None:
        for op, body in unpack(raw):
            if op == OP_AUTH_REPLY:
                # 认证回执：code=0 才算真的连上，否则就是"看着连上其实没权限"
                try:
                    code = json.loads(body.decode("utf-8", "ignore")).get("code")
                except Exception:
                    code = None
                if code == 0:
                    self.msg_count += 0
                    log.info("弹幕认证通过 room=%s", self.room_id)
                else:
                    log.warning("弹幕认证失败 code=%s（多半是 SESSDATA 过期或房间号不对）", code)
                    await self._state(False)
                continue
            if op != OP_MESSAGE:
                continue
            self.msg_count += 1
            try:
                payload = json.loads(body.decode("utf-8", "ignore"))
            except Exception:
                continue
            cmd = str(payload.get("cmd") or "")
            if cmd.startswith("DANMU_MSG"):
                await self._on_danmaku(payload)
            elif cmd.startswith("SEND_GIFT"):
                await self._on_gift(payload)
            elif cmd and cmd not in _SEEN_CMD:
                # 每种没处理的 cmd 只记一次，免得心跳把文件刷爆。
                # 下次礼物不出来，先看这个文件里到底是哪个 cmd。
                _SEEN_CMD.add(cmd)
                _append("cmd_dump.log", f"{time.strftime('%F %T')} 未处理的 cmd: {cmd}")

    async def _on_danmaku(self, payload: dict) -> None:
        info = payload.get("info") or []
        try:
            text = info[1]
            user = info[2]
            uid = str(user[0])
            uname = str(user[1])
        except Exception:
            return
        if not isinstance(text, str) or not self.on_danmaku:
            return
        try:
            is_owner = bool(self.owner_uid) and str(uid) == str(self.owner_uid)
            await self.on_danmaku(uname, uid, text, is_owner)
        except Exception:
            log.exception("弹幕回调失败")

    async def _on_gift(self, payload: dict) -> None:
        data = payload.get("data") or {}
        _dump_gift(data)

        def _i(v) -> int:
            try:
                return int(float(v or 0))
            except (TypeError, ValueError):
                return 0

        uname = uid = gift = coin_type = ""
        num, total_coin = 1, 0
        # ---- 第一优先：pb（现在 SEND_GIFT 的真实形态）
        pb = str(data.get("pb") or "")
        if pb:
            try:
                info = _gift_from_pb(pb)
                uname = info["uname"]
                uid = info["uid"]
                gift = info["gift"]
                num = info["num"]
                total_coin = info["price"] * num
                coin_type = info["coin_type"]
            except Exception:
                log.exception("pb 解不开，退回 JSON 字段 data=%s", data)
        # ---- 第二优先：老 JSON 形态（早版本/别的入口）
        if not gift or not uname:
            uname = uname or str(data.get("uname") or "")
            uid = uid or str(data.get("uid") or "")
            gift = gift or str(data.get("giftName") or data.get("gift_name") or "")
            if not gift:
                blind = data.get("blind_gift") or {}
                gift = str(blind.get("original_gift_name") or "")
            if num <= 1:
                num = _i(data.get("num")) or 1
            if not total_coin:
                total_coin = _i(data.get("total_coin")) or _i(data.get("price")) * num
            if not total_coin:
                blind = data.get("blind_gift") or {}
                total_coin = _i(data.get("discount_price")) * num or \
                    _i(blind.get("original_gift_price")) * num
            coin_type = coin_type or str(data.get("coin_type") or "")
        # 直播里礼物读不到就是要命的问题，这条必须留痕
        log.info("礼物 %s ×%d coin=%d type=%s 来源=%s", gift or "(空)", num,
                 total_coin, coin_type or "?", "pb" if pb else "json")
        if not self.on_gift:
            return
        try:
            await self.on_gift(uname, uid, gift, num, total_coin)
        except Exception:
            log.exception("礼物回调失败")
