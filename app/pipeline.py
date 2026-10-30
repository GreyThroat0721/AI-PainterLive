"""业务主流程：触发词 -> 扣额度 -> 排队出图 -> 审核 -> 命名存档 -> 直播反馈。

花名册（命名规则）：<当天序号4位><用户名><UID>.png，存在 <图片根>/YYYY-MM-DD/ 下。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
import pathlib
import random
import re
import shutil
import subprocess
import time

import aiohttp

from . import voice
from .chartag import CharTag
from .comfy import Comfy, ComfyError
from .config import BASE, RUNTIME
from .llm import LLM, LLMError

log = logging.getLogger("pipeline")


_EMO_RE = re.compile(r"\[(?:emo|exp):[^\]]*\]")


def _parse_emotion(text: str) -> tuple[str, str]:
    """从 LLM 回复里抽出 [emo:xxx] / [exp:xxx]，决定 Live2D 的身体动作。"""
    emo, exp = "neutral", ""
    for kind, val in re.findall(r"\[(emo|exp):([^\]]+)\]", text or ""):
        if kind == "emo":
            emo = val.strip().lower()
        else:
            exp = val.strip()
    if emo not in {"neutral", "happy", "sad", "angry", "shy", "surprised", "proud"}:
        emo = "neutral"
    return emo, exp


class Bus:
    def __init__(self) -> None:
        self.subs: set[asyncio.Queue] = set()
        self.recent: list[dict] = []

    def publish(self, ev: dict) -> None:
        ev = {"ts": int(time.time() * 1000), **ev}
        self.recent = (self.recent + [ev])[-60:]
        for q in list(self.subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=400)
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)


# 观众提这些词，才把她本人的外形锚进提示词；否则按观众描述画别人
SELF_KEYS = ("小画", "画手", "你自己", "画自己", "你本人", "女仆装", "呆毛")
# 说了这些就算指定了背景，没说就补白底
BG_KEYS = ("背景", "窗边", "教室", "森林", "花园", "街道", "房间", "海边", "天空", "舞台",
           "卧室", "厨房", "咖啡", "城市", "夜景", "樱花", "草地", "沙滩", "校园", "店里",
           "沙发", "床上", "树下", "雨中", "雪地", "屋顶", "教室", "神社")
# 至少得说清"画谁"，否则先问一句
CHAR_KEYS = ("少女", "女孩", "男孩", "少年", "男", "女", "娘", "人", "角色", "她", "他",
             "猫娘", "女仆", "爷爷", "奶奶", "姐姐", "哥哥", "妹妹", "弟弟", "萝莉", "正太",
             "魔王", "骑士", "公主", "王子", "精灵", "兽人", "人鱼", "鲸", "鱼", "学生", "老师")


# LLM 挂掉时的本地兜底词典：至少把"白发/少女/樱花"这种关键词翻出来
TAG_DICT = {
    "白发": "white hair", "黑发": "black hair", "金发": "blonde hair", "粉发": "pink hair",
    "蓝发": "blue hair", "紫发": "purple hair", "红发": "red hair", "银发": "silver hair",
    "长发": "long hair", "短发": "short hair", "双马尾": "twintails", "马尾": "ponytail",
    "少女": "girl", "女孩": "girl", "少年": "boy", "男孩": "boy", "萝莉": "loli",
    "猫娘": "catgirl", "女仆": "maid", "和服": "kimono", "校服": "school uniform",
    "水手服": "sailor uniform", "制服": "uniform", "裙": "skirt", "白裙": "white dress",
    "樱花": "cherry blossoms", "教室": "classroom", "海边": "beach", "森林": "forest",
    "城市": "city", "夜景": "night city", "下雨": "rain", "雪": "snow", "花园": "garden",
    "天空": "sky", "星": "starry sky", "站": "standing", "坐": "sitting", "跑": "running",
    "笑": "smiling", "哭": "crying", "睡": "sleeping", "吃饭": "eating", "读书": "reading",
    "猫": "cat", "狗": "dog", "耳机": "headphones", "眼镜": "glasses", "帽子": "hat",
    "花": "flowers", "月亮": "moon", "夕阳": "sunset", "蓝天": "blue sky",
}


# 二次元角色名：认识就别一律判成「太模糊」
CHAR_NAMES = ("阿米娅", "刻晴", "雷电将军", "胡桃", "温迪", "凝光", "甘雨", "可莉", "八重神子", "钟离",
              "夜兰", "菲谢尔", "神里绫华", "宵宫", "纳西妲", "仆人", "银狼", "黄泉", "花火", "初雪",
              "初音", "亚丝娜", "雷姆", "拉姆", "芙莉莲", "惠惠", "时崎狂三", "明日香",
              "绫波丽", "爱莉希雅", "琪亚娜", "布洛妮娅", "流萤", "三月七", "卡芙卡")

# 中文角色名 -> 模型认的英文 tag
CHAR_TAGS = {
    "阿米娅": "amiya (arknights), arknights", "刻晴": "keqing (genshin impact)",
    "雷电将军": "raiden shogun (genshin impact)", "胡桃": "hu tao (genshin impact)",
    "温迪": "venti (genshin impact)", "凝光": "ningguang (genshin impact)",
    "甘雨": "ganyu (genshin impact)", "可莉": "klee (genshin impact)",
    "八重神子": "yae miko (genshin impact)", "钟离": "zhongli (genshin impact)",
    "夜兰": "yelan (genshin impact)", "菲谢尔": "fischl (genshin impact)",
    "神里绫华": "kamisato ayaka (genshin impact)", "宵宫": "yoimiya (genshin impact)",
    "纳西妲": "nahida (genshin impact)", "初音": "hatsune miku",
    "雷姆": "rem (re:zero)", "拉姆": "ram (re:zero)", "惠惠": "megumin",
    "时崎狂三": "tokisaki kurumi", "芙莉莲": "frieren",
    "流萤": "firefly (honkai: star rail)", "卡芙卡": "kafka (honkai: star rail)",
    "银狼": "silver wolf (honkai: star rail)", "黄泉": "acheron (honkai: star rail)",
    "花火": "sparkle (honkai: star rail)",
}


def _is_character(text: str) -> bool:
    t = (text or "").lower()
    return any(k.lower() in t for k in CHAR_NAMES)


def _local_tags(text: str) -> str:
    hits = [v for k, v in TAG_DICT.items() if k in (text or "")]
    hits += [v for k, v in CHAR_TAGS.items() if k in (text or "")]
    return ", ".join(dict.fromkeys(hits))


def _wants_self(subject: str) -> bool:
    return any(k in subject for k in SELF_KEYS)


def _has_scene(subject: str) -> bool:
    return any(k in subject for k in BG_KEYS)


# 只挡画面：露点、脱衣、性行为、恋物。命中就不进画图队列，嘴上软挡回去
NSFW_KEYS = ("自慰", "手冲", "撸管", "撸", "脱衣", "全裸", "裸体", "裸着", "露点", "乳头", "下体",
             "性交", "做爱", "床戏", "插入", "口交", "射精", "潮吹", "淫", "情趣内衣", "内裤",
             "捆绑", "调教", "触手", "舔", "裙底", "淫荡", "骚", "发情", "onlyfans", "nsfw",
             "nude", "sex", "ahegao", "bondage")


def _is_nsfw(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in NSFW_KEYS)


def _too_vague(subject: str) -> bool:
    s = (subject or "").strip()
    if not s:
        return True
    # 先认角色：两字角色名（"菲伦""澄闪"）会低于长度门槛，所以这条必须排在 len 判断之前
    if CharTag.series_in(s) or CharTag().from_table(s):
        return False
    if len(s) < 3:
        return True
    if any(k in s for k in CHAR_KEYS) or _is_character(s):
        return False
    return True


def _safe(text: str, limit: int = 16) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text or "")[:limit] or "观众"


class Pipeline:
    def __init__(self, cfg: dict, store, bus: Bus) -> None:
        self.cfg = cfg
        self.store = store
        self.bus = bus
        self.queue: asyncio.Queue = asyncio.Queue()
        self.danmaku_count = 0
        self.last_danmaku: dict = {}
        self.last_master_check: dict = {}
        self._last_chat = 0.0
        self._last_any = 0.0
        self.comfy = Comfy(cfg["comfy"]["url"])
        self.llm = LLM(cfg)
        # 角色名 -> danbooru tag 解析器。本地表零延迟，gelbooru 兜底，缓存落 data/
        self.chartag = CharTag(RUNTIME / "data" / "chartag_cache.json")
        self.template: dict = {}
        self.style = ""
        self.raw_dir = RUNTIME / "images" / "_raw"
        self.worker: asyncio.Task | None = None
        self.api_enabled = True
        self._last_memory: dict[str, float] = {}
        self._voice_q: asyncio.Queue | None = None   # 语音一条一条播，但排队不阻塞弹幕

    # ---------- 生命周期 ----------
    def reload(self) -> None:
        cfg = self.cfg
        self.comfy = Comfy(cfg["comfy"]["url"])
        self.llm = LLM(cfg)
        self.load_assets()

    def load_assets(self) -> None:
        try:
            self.template = json.loads((BASE / self.cfg["comfy"]["workflow"]).read_text(encoding="utf-8"))
        except Exception as exc:
            log.error("工作流读取失败: %s", exc)
            self.template = {}
        style_path = BASE / self.cfg.get("style_lock_file", "prompts/style_lock.txt")
        self.style = ""
        if style_path.exists():
            self.style = " ".join(line.strip() for line in style_path.read_text(encoding="utf-8").splitlines()
                                  if line.strip() and not line.strip().startswith("#"))

    def start(self) -> None:
        if not self.worker or self.worker.done():
            self.worker = asyncio.create_task(self._worker_loop())

    # ---------- 弹幕 / 礼物 ----------
    async def on_danmaku(self, uname: str, uid: str, text: str, is_owner: bool = False) -> None:
        self.danmaku_count += 1
        arrived = time.time()
        self.last_danmaku = {"uname": uname, "uid": uid, "text": text, "ts": time.time()}
        self.bus.publish({"type": "danmaku", "uname": uname, "uid": uid, "text": text})
        log.info("弹幕 %s(%s): %s", uname, uid, text[:40])
        await self._maybe_memorize(uname, uid, text)
        trigger = (self.cfg["basic"].get("trigger") or "").strip()
        subject = ""
        if trigger and trigger in text:
            subject = text.split(trigger, 1)[1].strip("：:，,。.!！?？ \t")
        if subject:
            if _is_nsfw(subject):
                self.bus.publish({"type": "draw", "uname": uname, "subject": subject,
                                  "ok": False, "reason": "画面不合规，已挡回"})
                await self._say(f"{uname}，这个我不画呀，换个正经的试试，"
                                f"比如「白发少女穿水手服站在樱花树下」",
                                target=uname, kind="refuse")
                return
            if _too_vague(subject):
                self.bus.publish({"type": "draw", "uname": uname, "subject": subject,
                                  "ok": False, "reason": "描述太模糊，已追问"})
                await self._say(f"{uname}，画谁呀？说一下长相或设定，比如「白发少女穿水手服」，"
                                f"不说就没法画准了", target=uname, kind="ask")
                return
            res = await self.order(uname, uid, subject)   # 绘图请求：不受任何限流
            self.bus.publish({"type": "draw", "uname": uname, "subject": subject,
                              "ok": bool(res.get("accepted")),
                              "reason": "" if res.get("accepted") else str(res.get("reason") or "")})
            return
        basic = self.cfg["basic"]
        master = str(basic.get("master_uid") or "").strip()
        master_name = str(basic.get("master_bili_name") or "").strip()
        u_norm = (uname or "").strip().lower()
        is_master = bool(is_owner) or (bool(master) and str(uid).strip() == master) or \
                    (bool(master_name) and u_norm == master_name.strip().lower())
        self.last_master_check = {"uid": str(uid), "uname": uname, "bili_owner": bool(is_owner),
                                  "master_uid": master, "master_name": master_name,
                                  "result": bool(is_master), "ts": time.time()}
        log.info("认主判定 uid=%r uname=%r 接口认主=%s master_uid=%r master_name=%r -> %s",
                 uid, uname, is_owner, master, master_name, is_master)
        if time.time() - arrived > 15:
            # 排队排太久了，这条已经没意义，直接丢（不然会隔几分钟才回一句）
            self.bus.publish({"type": "notice", "kind": "skip", "text": f"{uname} 的弹幕过期，已跳过"})
            return
        if not is_master:
            throttle = float(basic.get("danmaku_throttle") or 0)
            now = time.time()
            if throttle > 0 and now - self._last_any < throttle:
                return  # 限流窗口内，这条直接丢掉（礼物走 on_gift，不受影响）
            self._last_any = now
        await self._chat_reply(uname, uid, text, is_master)

    async def _chat_reply(self, uname: str, uid: str, text: str, is_master: bool = False) -> None:
        """没点名要画图时，让 AI 接一句，别让直播间变成哑巴。"""
        basic = self.cfg["basic"]
        if not basic.get("chat_reply", True) or not self.api_enabled:
            return
        if len((text or "").strip()) < 2:
            return
        gap = float(basic.get("chat_interval") or 8)
        if time.time() - self._last_chat < gap:
            return
        self._last_chat = time.time()
        # 认出是主人就必须告诉模型，不然它按人设把谁当外人，语气一样就白认了
        head = ("【这条是主人本人发的！你认得他，语气更熟更软，敢撒娇、敢顶嘴、有你们俩的默契；"
        "可以叫「主人」，但别暴露后台和系统】\n") if is_master else ""
        running = bool(self.store.state["session"].get("running"))
        line = await self._react(
            head + f"直播间观众「{uname}」说：{text}\n"
            f"（内部状态：直播{'进行中' if running else '未开播'}，画图队列 {self.queue.qsize()} 张在排。"
            f"没在画就别提画画、别说马上好）")
        if line:
            await self._say(line, target=uname, kind="reply")

    async def on_gift(self, uname: str, uid: str, gift: str, num: int, coin: int) -> None:
        yuan = coin / 1000.0
        self.store.add_revenue_fen(int(yuan * 100 * 0.5))
        user = self.store.user(uid, uname)
        user["gifts_yuan"] = float(user.get("gifts_yuan") or 0) + yuan
        per = float(self.cfg["quota"].get("gift_per_quota") or 10)
        granted = 0
        while per > 0 and user["gifts_yuan"] >= per:
            if not self._grant_quota(uid, uname, 1):
                break
            user["gifts_yuan"] -= per
            granted += 1
            if self.cfg["memory"].get("mode") == "gift":
                self.store.memorize(uid, uname, f"送过 {gift}×{num}", score=3.0)
        self.store.save()
        self.bus.publish({"type": "gift", "uname": uname, "uid": uid, "gift": gift,
                          "num": num, "yuan": round(yuan, 2), "granted": granted})
        # 小心心也算礼物，别因为不值钱就装没看见
        if gift:
            await self._say(random.choice((
                f"谢谢你的{gift}，好大方呀",
                f"{gift}我收到了，谢谢你呀",
                f"哇，{gift}！谢谢，我记着了",
            )), target=uname, kind="gift")

    def _grant_quota(self, uid: str, uname: str, delta: int) -> bool:
        user = self.store.user(uid, uname)
        cap = int(self.cfg["quota"].get("max_total_paid") or 0)
        if cap > 0 and int(user.get("total_paid") or 0) + delta > cap:
            return False
        self.store.grant_paid(uid, uname, delta)
        return True

    async def _maybe_memorize(self, uname: str, uid: str, text: str) -> None:
        mode = (self.cfg["memory"] or {}).get("mode", "danmaku")
        if mode != "danmaku":
            return
        interval = float(self.cfg["memory"].get("interval_sec") or 300)
        last = self._last_memory.get(uid, 0.0)
        if time.time() - last < interval:
            return
        self._last_memory[uid] = time.time()
        note = f"弹幕说：{text[:40]}"
        self.store.memorize(uid, uname, note, score=1.0)

    # ---------- 点单 ----------
    async def order(self, uname: str, uid: str, subject: str) -> dict:
        subject = subject.strip()[:80]
        if not self.store.state["session"].get("running"):
            self.bus.publish({"type": "notice", "kind": "skip", "text": "直播未开启，忽略点单"})
            return {"accepted": False, "reason": "直播未开启"}
        if not subject:
            return {"accepted": False, "reason": "没说画什么"}
        for word in (self.cfg.get("deny_words") or []):
            if word and word in subject:
                await self._say("这个画不了哦，换一个嘛")
                return {"accepted": False, "reason": "内容被拦"}
        kind = self.store.spend(uid, uname)
        if kind is None:
            await self._say("宝，绘图额度用完啦，送点小礼物就能继续点图哦")
            self.bus.publish({"type": "notice", "kind": "skip", "text": f"{uname} 没额度了"})
            return {"accepted": False, "reason": "额度不足"}
        job = {"uname": uname, "uid": uid, "subject": subject, "kind": kind,
               "id": f"{int(time.time()*1000)}-{random.randint(100,999)}"}
        await self.queue.put(job)
        self.bus.publish({"type": "queued", "uname": uname, "subject": subject,
                          "pos": self.queue.qsize(), "kind": kind})
        return {"accepted": True, "kind": kind, "pos": self.queue.qsize()}

    async def _worker_loop(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self._run(job)
            except (ComfyError, Exception) as exc:  # noqa: BLE001
                log.exception("出图失败")
                self.store.refund(job["uid"], job["uname"], job["kind"])
                self.bus.publish({"type": "error", "uname": job["uname"], "message": str(exc)[:300]})
                await self._say("哎呀，这张画崩了，额度还给你了")
            finally:
                self.queue.task_done()

    async def _translate(self, text: str) -> str:
        """观众说的是中文，模型认的是英文 danbooru tag——不翻译等于没写。"""
        text = (text or "").strip()
        if not text:
            return ""
        # ① 先认角色。本地表命中就直接拿到准确 tag（kal'tsit (arknights), arknights）——
        # 这一步必须排在翻译之前，否则"凯尔希"会被 LLM 编成 kaltsit / kelxi 这种模型不认的写法。
        char_tag = self.chartag.from_table(text)
        rest = text
        if char_tag:
            from .chartag import CHARACTERS
            for name in sorted(CHARACTERS, key=len, reverse=True):
                if name in rest:
                    rest = rest.replace(name, " ", 1)
                    break
            rest = rest.strip("，,。.、 的")
        if not rest:
            return char_tag
        if not any("\u4e00" <= ch <= "\u9fff" for ch in rest):
            return ", ".join(x for x in (char_tag, rest) if x)
        text = rest  # 剩下的中文才交给 LLM，角色已经定死了
        try:
            system = (
                "你是 Danbooru 标签翻译器，把中文画面描述转成 SD 用的英文 tag 串。\n"
                "规则：\n"
                "1. 只输出 tag：英文小写、英文逗号分隔，不要句子、不要解释、不要编号、不要引号。\n"
                "2. tag 用 danbooru 常见写法：white hair / long hair / blue eyes / school uniform / standing / cherry blossoms / outdoors。\n"
                "3. 顺序：人数(1girl/1boy/solo) → 发色+发长+发型 → 瞳色 → 服装 → 动作姿势 → 背景环境 → 表情。\n"
                "4. 原文没提的不要编；没提背景就不写背景。\n"
                "5. 如果原文提到某个具体动漫/游戏角色，先输出这个角色的 danbooru 写法："
                "英文名_(作品英文名)，例如 goldenglow_(arknights)、hu_tao_(genshin_impact)。\n"
                "示例：\n"
                "输入：白发少女站在樱花树下\n"
                "输出：1girl, solo, white hair, long hair, standing, cherry blossoms, outdoors, full body\n"
                "输入：猫娘穿水手服在海边跑步\n"
                "输出：1girl, solo, cat ears, cat tail, sailor uniform, running, beach, day, full body"
            )
            # 推理类模型会把 token 全花在思考上，太少会返回空正文
            result = await self.llm.chat([{"role": "system", "content": system},
                                          {"role": "user", "content": text}], max_tokens=400)
            if result.get("tokens"):
                self.store.add_cost_fen(result["cost_yuan"] * 100)
                self.store.save()
            out = (result.get("text") or "").strip().strip('"').replace("\n", " ")
            out = out.replace("。", ",").strip(", ")
            if out and any(c.isascii() and c.isalpha() for c in out):
                # LLM 猜的角色名可能不存在（把"澄闪"编成 clear_sparkle）。
                # 拿第一个 tag 去 gelbooru 验一次，查得到才信它。
                first = out.split(",")[0].strip()
                if re.fullmatch(r"[a-z0-9_() .:'\-]+", first) and first:
                    verified = await self.chartag.lookup(first.split("(")[0].strip().replace(" ", "_"))
                    if verified and verified != first:
                        out = verified + "," + out[len(first):] if "," in out else verified
                return ", ".join(x for x in (char_tag, out) if x)
            log.warning("LLM 没吐出可用 tag（tokens=%s），改用本地词典", result.get("tokens"))
        except Exception as exc:
            log.warning("提示词翻译失败，改用本地词典: %s", exc)
        fallback = _local_tags(text)
        return ", ".join(x for x in (char_tag, fallback or text) if x)

    async def _run(self, job: dict) -> None:
        if not self.template:
            raise ComfyError("工作流模板没加载成功")
        basic = self.cfg["basic"]
        cfg_c = self.cfg["comfy"]
        self.bus.publish({"type": "start", "uname": job["uname"], "subject": job["subject"]})
        await self._say(random.choice((
            "好，别催，我这就动笔",
            "收到，画着呢，你先等等",
            "嗯，给我一会儿，别老盯着看",
        )), target=job["uname"], kind="draw")
        async def on_progress(value: int, total: int) -> None:
            self.bus.publish({"type": "progress", "value": value, "max": total})

        subject_en = await self._translate(job["subject"])
        log.info("绘图提示词: %s  →  %s", job["subject"], subject_en)
        files = await self.comfy.generate(
            self.template, self.raw_dir, on_progress=on_progress,
            positive=", ".join(x for x in (
                # 画她自己才锚定她的外形；画别人就用中性描述，别把人画成别的角色
                # 观众要的放最前：CLIP 只吃 77 token，排后面的会被直接截掉
                "(" + subject_en + ":1.3)",
                "" if _has_scene(job["subject"]) or _has_scene(subject_en) else "simple white background",
                (basic.get("character_tags") or "") if _wants_self(job["subject"])
                else "1girl, solo, chibi",
                self.style,
                "safe, sfw, fully clothed",
            ) if x),
            negative=cfg_c.get("negative") or "",
            seed=random.randint(0, 2**31 - 1), steps=int(cfg_c.get("steps") or 26),
            cfg=float(cfg_c.get("cfg") or 5.0), width=int(basic["width"]),
            height=int(basic["height"]), checkpoint=cfg_c.get("checkpoint") or "",
            sampler=cfg_c.get("sampler") or "euler_ancestral",
            scheduler=cfg_c.get("scheduler") or "simple", prefix="raw",
        )
        raw = self.raw_dir / files[0]
        if self.cfg["review"].get("full_review"):
            item = self.store.submit_review({"img": str(raw), "uname": job["uname"],
                                             "uid": job["uid"], "kind": job["kind"],
                                             "subject": job["subject"]})
            self.bus.publish({"type": "review", "item": item})
            await self._say(random.choice((
                "画完啦，正送去审核，等一下下",
                "好了好了，等审核过了就能看",
                "画好啦，审核中，别眨眼",
            )), target=job["uname"], kind="draw")
            return
        await self.publish({"img": str(raw), **job})

    # ---------- 审核 ----------
    async def approve(self, rid: str) -> dict | None:
        item = self.store.take_review(rid)
        if not item:
            return None
        await self.publish(item)
        return item

    async def reject(self, rid: str) -> dict | None:
        item = self.store.take_review(rid)
        if not item:
            return None
        if item.get("kind") == "paid" and self.cfg["quota"].get("refund_rejected_paid"):
            self.store.refund(item["uid"], item["uname"], "paid")
        self.store.push_rejected(item)
        try:
            pathlib.Path(item["img"]).unlink(missing_ok=True)
        except Exception:
            pass
        self.bus.publish({"type": "rejected", "item": item})
        line = await self._react("刚刚那张图没过审，请向观众道歉，说明这张不放了，语气软一点，别解释规则")
        await self._say(line or "这张图主人没让放，对不起啦")
        return item

    # ---------- 发布 ----------
    async def publish(self, item: dict) -> pathlib.Path:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        seq = self.store.next_image_seq(today)
        root = pathlib.Path(self.cfg["basic"].get("image_dir") or (BASE / "images"))
        target_dir = root / today
        target_dir.mkdir(parents=True, exist_ok=True)
        name = f"{seq:04d}{_safe(item['uname'])}{item['uid']}.png"
        target = target_dir / name
        src = pathlib.Path(item["img"])
        await asyncio.to_thread(shutil.move, str(src), str(target))
        self.store.add_generated()
        self.store.add_history({"name": name, "date": today, "uname": item["uname"],
                                "uid": item["uid"], "subject": item.get("subject", ""),
                                "kind": item.get("kind"), "ts": time.time()})
        self.bus.publish({"type": "done", "uname": item["uname"], "subject": item.get("subject", ""),
                          "file": name, "date": today, "url": f"/img/{today}/{name}"})
        await self._say(random.choice((
            "喏，画完了，看看还行吗",
            "好了，喜欢就夸我一句",
            "画完了，不许说丑，我改",
        )), target=item.get("uname", ""), kind="draw")
        return target

    # ---------- 与 LLM / 语音 / Live2D ----------
    async def _react(self, instruction: str) -> str:
        if not self.api_enabled:
            return ""
        try:
            from .llm import build_system_prompt
            system = build_system_prompt(self.cfg, self.store, query=instruction)
            result = await self.llm.chat([{"role": "system", "content": system},
                                          {"role": "user", "content": instruction}], max_tokens=300)
            if result.get("tokens"):
                self.store.add_cost_fen(result["cost_yuan"] * 100)
                self.store.save()
            out = (result.get("text") or "").strip()
            if out:
                return out
            # 推理类模型偶尔把 token 全花在思考上，正文空 —— 再要一次
            log.warning("聊天正文为空（tokens=%s），重试一次", result.get("tokens"))
            result = await self.llm.chat([{"role": "system", "content": system},
                                          {"role": "user", "content": instruction}], max_tokens=300)
            if result.get("tokens"):
                self.store.add_cost_fen(result["cost_yuan"] * 100)
                self.store.save()
            return (result.get("text") or "").strip()
        except LLMError as exc:
            log.warning("LLM 调用失败: %s", exc)
            return ""

    async def _say(self, text: str, target: str = "", kind: str = "") -> None:
        if not text:
            return
        emo, exp = _parse_emotion(text)
        clean = _EMO_RE.sub("", text).strip() or text
        limit = int(self.cfg["basic"].get("max_chars") or 0)
        if limit > 0 and len(clean) > limit:
            clean = clean[:limit].rstrip() + "…"
        basic = self.cfg["basic"]
        audio = ""
        try:
            path = await voice.synth(clean, basic.get("voice") or "zh-CN-XiaoyiNeural",
                                     basic.get("voice_engine") or "edge",
                                     basic.get("voice_rate") or "+0%")
            if path:
                audio = f"/tts/{path.name}"
        except Exception as exc:
            log.warning("语音合成失败: %s", exc)
        self.bus.publish({"type": "say", "text": clean, "emotion": emo, "exp": exp,
                          "audio": audio, "target": target, "kind": kind})
        if emo or exp:
            self.bus.publish({"type": "emotion", "name": emo, "exp": exp})
        if not audio:
            return
        # server=本机扬声器；browser=由直播画面（字幕区）播，OBS 才收得到声音
        if (basic.get("voice_output") or "server") == "server":
            # 串行播但用队列：直接 create_task 会两句叠一起，直接 await 又会把弹幕处理拖死
            if self._voice_q is None:
                self._voice_q = asyncio.Queue()
                asyncio.create_task(self._voice_loop())
            self._voice_q.put_nowait(voice.TTS_DIR / audio.rsplit("/", 1)[-1])
        l2d = self.cfg["basic"].get("live2d") or {}
        if l2d.get("enabled"):
            await self._live2d(text)

    async def _voice_loop(self) -> None:
        """一个 worker 按顺序播，谁也不插队。"""
        while True:
            path = await self._voice_q.get()
            try:
                await voice.play(path)
            except Exception as exc:  # noqa: BLE001
                log.warning("播放失败: %s", exc)
            finally:
                self._voice_q.task_done()

    async def _live2d(self, text: str) -> None:
        l2d = self.cfg["basic"].get("live2d") or {}
        url = "http://127.0.0.1:34567/ingest"
        sid = f"say{int(time.time()*1000)}"
        frames = [round(0.5 * abs(math.sin(i / 3.0)), 3) for i in range(80)]
        payload = [
            {"type": "speak_request", "id": sid, "ts": int(time.time() * 1000),
             "play": False, "sampleRate": 48000,
             "segments": [{"emotion": "happy", "content": text}]},
            {"type": "envelope", "id": sid, "seq": 0, "ts": int(time.time() * 1000), "frames": frames},
        ]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload,
                                        headers={"X-Live2D-Token": "dsh-live2d-local"},
                                        timeout=aiohttp.ClientTimeout(total=4)) as r:
                    if r.status != 200:
                        log.debug("live2d ingest %s", r.status)
        except Exception as exc:
            log.debug("Live2D 不可用: %s", exc)
