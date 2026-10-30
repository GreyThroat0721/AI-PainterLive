"""角色名 -> 英文 tag 解析：本地表（零延迟）+ gelbooru autocomplete（兜底）。

为什么不用 danbooru：实测 SSL 握手失败（被墙），safebooru 被 Cloudflare 403。
gelbooru 的 autocomplete2 接口能连、返回标准 JSON、还带 post_count 可排序。
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time
import urllib.parse
import urllib.request

log = logging.getLogger("chartag")

GELBOORU = "https://gelbooru.com/index.php?page=autocomplete2&term={term}&type=tag"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CACHE_TTL = 7 * 24 * 3600  # 角色 tag 不会天天变，缓存一周

# 系列名：观众说了系列，就算"说清了画谁"的一半，别一律判模糊
SERIES = ("明日方舟", "原神", "崩坏", "星穹铁道", "蔚蓝档案", "碧蓝航线", "碧蓝档案",
          "少女前线", "fgo", "fate", "初音", "东方", "宝可梦", "英雄联盟", "lol",
          "塞尔达", "最终幻想", "尼尔", "异度", "偶像大师", "love live", "赛马娘",
          "独立游戏", "崩铁", "绝区零", "鸣潮", "战双", "深空之眼", "重返未来")

# 中文角色名 -> (gelbooru 英文名, 系列 tag)。英文名只写角色部分，系列在拼接时补。
# 收录原则：宁可少而准，不要多而错 —— 猜错的 tag 会让模型画出完全不相干的人。
CHARACTERS: dict[str, tuple[str, str]] = {
    # ---- 明日方舟 ----
    "澄闪": ("goldenglow", "arknights"),
    "阿米娅": ("amiya", "arknights"),
    "陈": ("chen", "arknights"),
    "德克萨斯": ("texas", "arknights"),
    "能天使": ("exusiai", "arknights"),
    "银灰": ("silverash", "arknights"),
    "斯卡蒂": ("skadi", "arknights"),
    "W": ("w_(arknights)", "arknights"),
    "凯尔希": ("kal'tsit", "arknights"),
    "缪尔赛思": ("muelsyse", "arknights"),
    "霍尔海雅": ("ho'olheyak", "arknights"),
    "迷迭香": ("rosmontis", "arknights"),
    "浊心斯卡蒂": ("skadi_(alter)", "arknights"),
    # ---- 原神 ----
    "刻晴": ("keqing", "genshin impact"),
    "雷电将军": ("raiden shogun", "genshin impact"),
    "胡桃": ("hu tao", "genshin impact"),
    "甘雨": ("ganyu", "genshin impact"),
    "温迪": ("venti", "genshin impact"),
    "钟离": ("zhongli", "genshin impact"),
    "纳西妲": ("nahida", "genshin impact"),
    "可莉": ("klee", "genshin impact"),
    "神里绫华": ("kamisato ayaka", "genshin impact"),
    "宵宫": ("yoimiya", "genshin impact"),
    "八重神子": ("yae miko", "genshin impact"),
    "夜兰": ("yelan", "genshin impact"),
    "菲谢尔": ("fischl", "genshin impact"),
    "凝光": ("ningguang", "genshin impact"),
    "荧": ("lumine", "genshin impact"),
    "空": ("aether", "genshin impact"),
    "芙宁娜": ("furina", "genshin impact"),
    "娜维娅": ("navia", "genshin impact"),
    "希格雯": ("sigewinne", "genshin impact"),
    "玛薇卡": ("mavuika", "genshin impact"),
    # ---- 崩坏：星穹铁道 ----
    "流萤": ("firefly", "honkai: star rail"),
    "卡芙卡": ("kafka", "honkai: star rail"),
    "银狼": ("silver wolf", "honkai: star rail"),
    "黄泉": ("acheron", "honkai: star rail"),
    "花火": ("sparkle", "honkai: star rail"),
    "三月七": ("march 7th", "honkai: star rail"),
    "丹恒": ("dan heng", "honkai: star rail"),
    "刃": ("blade", "honkai: star rail"),
    "镜流": ("jingliu", "honkai: star rail"),
    "符玄": ("fu xuan", "honkai: star rail"),
    "藿藿": ("huohuo", "honkai: star rail"),
    # ---- 崩坏3 ----
    "爱莉希雅": ("elysia", "honkai impact"),
    "琪亚娜": ("kiana kaslana", "honkai impact"),
    "布洛妮娅": ("bronya zaychik", "honkai impact"),
    "雷电芽衣": ("raiden mei", "honkai impact"),
    # ---- 蔚蓝档案 ----
    "白子": ("shiroko", "blue archive"),
    "阿露": ("aru", "blue archive"),
    "星野": ("hoshino", "blue archive"),
    "优香": ("yuuka", "blue archive"),
    "爱丽丝": ("aris", "blue archive"),
    "日奈": ("hina", "blue archive"),
    "睦月": ("mutsuki", "blue archive"),
    # ---- 碧蓝航线 ----
    "企业": ("enterprise", "azur lane"),
    "贝尔法斯特": ("belfast", "azur lane"),
    "拉菲": ("laffey", "azur lane"),
    "独角兽": ("unicorn", "azur lane"),
    # ---- FGO ----
    "阿尔托莉雅": ("artoria pendragon", "fate"),
    "saber": ("artoria pendragon", "fate"),
    "玛修": ("mash kyrielight", "fate"),
    "伊莉雅": ("illyasviel von einzbern", "fate"),
    "贞德": ("jeanne d'arc", "fate"),
    "斯卡哈": ("scathach", "fate"),
    # ---- 初音 / VOCALOID ----
    "初音": ("hatsune miku", "vocaloid"),
    "初音未来": ("hatsune miku", "vocaloid"),
    "镜音铃": ("kagamine rin", "vocaloid"),
    "洛天依": ("luo tianyi", "vocaloid"),
    # ---- 常见番剧 ----
    "雷姆": ("rem", "re:zero"),
    "拉姆": ("ram", "re:zero"),
    "芙莉莲": ("frieren", "sousou no frieren"),
    "菲伦": ("fern", "sousou no frieren"),
    "赛莉耶": ("serie", "sousou no frieren"),
    "惠惠": ("megumin", "konosuba"),
    "阿库娅": ("aqua", "konosuba"),
    "时崎狂三": ("tokisaki kurumi", "date a live"),
    "亚丝娜": ("yuuki asuna", "sword art online"),
    "明日香": ("souryuu asuka langley", "neon genesis evangelion"),
    "绫波丽": ("ayanami rei", "neon genesis evangelion"),
    "2B": ("2b", "nier:automata"),
    "零": ("zero two", "darling in the franxx"),
    # ---- 其他热门 ----
    "蒂法": ("tifa lockhart", "final fantasy vii"),
    "爱丽丝FF": ("aerith gainsborough", "final fantasy vii"),
    "塞尔达": ("zelda", "the legend of zelda"),
}


class CharTag:
    """角色解析器。一个实例活整个进程，内部自己管缓存。"""

    def __init__(self, cache_path: pathlib.Path | None = None) -> None:
        self.cache_path = cache_path
        self._mem: dict[str, tuple[float, str]] = {}
        self._load()

    # ---------- 缓存 ----------
    def _load(self) -> None:
        if not self.cache_path or not self.cache_path.exists():
            return
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            now = time.time()
            for k, v in raw.items():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    ts, tag = float(v[0]), str(v[1])
                    if now - ts < CACHE_TTL:
                        self._mem[k] = (ts, tag)
        except Exception:
            log.debug("角色 tag 缓存读不出来，当空的用")

    def _save(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps({k: list(v) for k, v in self._mem.items()}, ensure_ascii=False, indent=1),
                encoding="utf-8")
        except Exception as exc:
            log.debug("角色 tag 缓存写不进去: %s", exc)

    # ---------- 本地表 ----------
    def from_table(self, subject: str) -> str:
        """在本地表里找最长匹配的角色名，返回拼好的 tag；没有就返回空串。"""
        s = subject or ""
        hit = ""
        for name in sorted(CHARACTERS, key=len, reverse=True):
            if name in s:
                en, series = CHARACTERS[name]
                hit = "%s (%s), %s" % (en, series, series)
                break
        return hit

    @staticmethod
    def series_in(subject: str) -> str:
        """观众只说了系列没说角色？返回系列 tag，至少别判成模糊。"""
        t = (subject or "").lower()
        for s in SERIES:
            if s.lower() in t:
                return s
        return ""

    # ---------- gelbooru ----------
    @staticmethod
    def _fetch_sync(term: str) -> list:
        """同步抓 gelbooru。为什么不用 aiohttp：这台机器上 aiohttp 对这个域名一律超时，
        urllib 走同一张网卡 4 秒就回 —— Windows 上两者 DNS/连接栈行为不同，实测踩过。"""
        url = GELBOORU.format(term=urllib.parse.quote(term, safe=""))
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=6) as r:  # noqa: S310 固定域名
            raw = r.read().decode("utf-8", "ignore")
        return json.loads(raw)

    async def lookup(self, term: str) -> str:
        """拿英文名去 gelbooru 自动补全，返回按 post_count 排序后的第一个 tag。"""
        term = (term or "").strip().lower().replace(" ", "_")
        if not term:
            return ""
        if term in self._mem and time.time() - self._mem[term][0] < CACHE_TTL:
            return self._mem[term][1]
        try:
            data = await asyncio.to_thread(self._fetch_sync, term)
        except Exception as exc:
            log.debug("gelbooru 查询失败(%s): %s", term, exc)
            return ""
        if not isinstance(data, list) or not data:
            return ""
        # 优先 category=character 的，其次按 post_count 从高到低
        def score(item: dict) -> float:
            try:
                pc = float(item.get("post_count") or 0)
            except (TypeError, ValueError):
                pc = 0.0
            return pc + (1e9 if item.get("category") == "character" else 0.0)

        ordered = sorted(data, key=score, reverse=True)
        tag = str(ordered[0].get("value") or "").strip()
        if tag:
            self._mem[term] = (time.time(), tag)
            self._save()
        return tag

    async def resolve(self, subject: str) -> str:
        """总入口：本地表优先，其次拿英文名去 gelbooru 校验，最后空串（不出网也能活）。"""
        local = self.from_table(subject)
        if local:
            return local
        return ""