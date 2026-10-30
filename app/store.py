"""状态层：data/state.json 单文件持久化（原子写）。

免费额度是「整场共用池」，付费额度按 UID 绑定并可跨场继承。
"""
from __future__ import annotations

import json
import pathlib
import re
import time
import uuid

from . import config as config_mod

STATE_PATH = config_mod.DATA_DIR / "state.json"


def _now() -> float:
    return time.time()


def _default_state() -> dict:
    return {
        "session": {"seq": 0, "running": False, "started_at": None,
                    "revenue_fen": 0, "cost_fen": 0, "generated": 0,
                    "free_pool": 50, "free_used": 0},
        "quota": {"users": {}},
        "memory": {"people": {}},
        "knowledge": {"cards": [
            {"id": uuid.uuid4().hex[:8], "title": "梗知识（示例）", "tags": ["梗"],
             "body": "在这里写直播间常用的梗，AI 命中关键词时才读取；删掉这条换成你的。",
             "enabled": True},
        ]},
        "known": {"cards": []},
        "review": {"pending": [], "rejected": []},
        "imgseq": {},
        "history": [],
    }


class Store:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.path = STATE_PATH
        self.load()

    # ---------- 落盘 ----------
    def load(self) -> None:
        state = _default_state()
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                for key, value in saved.items():
                    if isinstance(value, dict) and isinstance(state.get(key), dict):
                        state[key].update(value)
                    else:
                        state[key] = value
            except Exception:
                pass
        self.state = state

    def save(self) -> None:
        config_mod.DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---------- 场次 ----------
    def new_session(self) -> dict:
        q = self.cfg["quota"]
        session = self.state["session"]
        session.update({
            "seq": int(session.get("seq", 0)) + 1, "running": True,
            "started_at": _now(), "revenue_fen": 0, "cost_fen": 0, "generated": 0,
            "free_pool": int(q.get("free_per_session") or 0), "free_used": 0,
        })
        cap = int(q.get("inherit_cap") or 0)
        for user in self.state["quota"]["users"].values():
            user["free_used"] = 0
            if cap > 0:
                user["paid"] = min(int(user.get("paid") or 0), cap)
        self.save()
        return self.session_view()

    def set_running(self, running: bool) -> dict:
        session = self.state["session"]
        session["running"] = bool(running)
        cfg_pool = int(self.cfg["quota"].get("free_per_session") or 0)
        if int(session.get("free_pool") or 0) != cfg_pool:
            session["free_pool"] = cfg_pool   # 面板改了立刻跟上，不用等下一场
        self.save()
        return self.session_view()

    def session_view(self) -> dict:
        s = self.state["session"]
        # 面板改了免费池，这里立刻跟（不用等新一场，不然永远显示旧数）
        cfg_pool = int(self.cfg["quota"].get("free_per_session") or 0)
        if int(s.get("free_pool", 0)) != cfg_pool:
            s["free_pool"] = cfg_pool
        pool = int(s.get("free_pool", 0))
        used = int(s.get("free_used", 0))
        return {
            "seq": s.get("seq", 0),
            "running": bool(s.get("running")),
            "started_at": s.get("started_at"),
            "revenue_yuan": round(s.get("revenue_fen", 0) / 100.0, 2),
            "cost_yuan": round(s.get("cost_fen", 0) / 100.0, 6),
            "generated": s.get("generated", 0),
            "free_pool": pool,
            "free_used": used,
            "free_left": max(0, pool - used),
        }

    def add_revenue_fen(self, fen: int) -> None:
        s = self.state["session"]
        s["revenue_fen"] = int(s.get("revenue_fen", 0)) + int(fen)
        self.save()

    def add_cost_fen(self, fen: float) -> None:
        s = self.state["session"]
        # 单次调用常常只有 0.x 分，取整就永远是 0——这里必须保留小数
        s["cost_fen"] = round(float(s.get("cost_fen", 0)) + float(fen), 4)
        self.save()

    def add_generated(self) -> None:
        s = self.state["session"]
        s["generated"] = int(s.get("generated", 0)) + 1
        self.save()

    # ---------- 额度 ----------
    def user(self, uid: str, name: str = "") -> dict:
        uid = str(uid)
        users = self.state["quota"]["users"]
        user = users.get(uid)
        if user is None:
            user = {"name": name or f"UID{uid}", "paid": 0, "total_paid": 0,
                    "free_used": 0, "gifts_yuan": 0.0, "updated": _now()}
            users[uid] = user
        elif name and name != user.get("name"):
            user["name"] = name
        user.setdefault("free_used", 0)
        user.pop("free", None)
        return user

    def list_quota(self) -> list[dict]:
        rows = []
        for uid, user in self.state["quota"]["users"].items():
            rows.append({"uid": uid, "name": user.get("name"),
                         "paid": int(user.get("paid") or 0),
                         "total_paid": int(user.get("total_paid") or 0),
                         "free_used": int(user.get("free_used") or 0),
                         "gifts_yuan": float(user.get("gifts_yuan") or 0),
                         "updated": user.get("updated")})
        rows.sort(key=lambda r: (-(r["paid"]), -(r["total_paid"]), r.get("name") or ""))
        return rows

    def set_quota(self, uid: str, name: str, paid: int | None = None) -> dict:
        user = self.user(uid, name)
        if paid is not None:
            user["paid"] = max(0, int(paid))
        user["updated"] = _now()
        self.save()
        return user

    def remove_quota(self, uid: str) -> bool:
        """把一个人从额度名单里整条删掉，免得刷屏的人一直挂着。"""
        users = self.state["quota"]["users"]
        if str(uid) not in users:
            return False
        users.pop(str(uid), None)
        self.save()
        return True

    def grant_paid(self, uid: str, name: str, delta: int) -> dict:
        q = self.cfg["quota"]
        user = self.user(uid, name)
        if delta > 0:
            cap = int(q.get("max_total_paid") or 0)
            room = cap - int(user.get("total_paid") or 0) if cap > 0 else delta
            delta = max(0, min(delta, room))
            user["total_paid"] = int(user.get("total_paid") or 0) + delta
        user["paid"] = max(0, int(user.get("paid") or 0) + delta)
        user["updated"] = _now()
        self.save()
        return user

    def spend(self, uid: str, name: str) -> str | None:
        """扣一次绘图额度：先付费，再全场免费池。返回 'paid' / 'free' / None。"""
        user = self.user(uid, name)
        if int(user.get("paid") or 0) > 0:
            user["paid"] = int(user["paid"]) - 1
            kind = "paid"
        else:
            session = self.state["session"]
            pool, used = int(session.get("free_pool", 0)), int(session.get("free_used", 0))
            if used < pool:
                session["free_used"] = used + 1
                user["free_used"] = int(user.get("free_used") or 0) + 1
                kind = "free"
            else:
                return None
        user["updated"] = _now()
        self.save()
        return kind

    def refund(self, uid: str, name: str, kind: str) -> None:
        user = self.user(uid, name)
        if kind == "paid":
            user["paid"] = int(user.get("paid") or 0) + 1
            user["total_paid"] = max(0, int(user.get("total_paid") or 0) - 1)
        elif kind == "free":
            session = self.state["session"]
            session["free_used"] = max(0, int(session.get("free_used", 0)) - 1)
            user["free_used"] = max(0, int(user.get("free_used") or 0) - 1)
        user["updated"] = _now()
        self.save()

    # ---------- 观众记忆 ----------
    def memory(self) -> dict:
        return self.state["memory"]["people"]

    def memorize(self, uid: str, name: str, note: str, score: float = 1.0) -> None:
        people = self.memory()
        item = people.get(uid) or {"name": name, "notes": [], "score": 0.0, "updated": _now()}
        if note and note not in item["notes"]:
            item["notes"] = (item["notes"] + [note])[-12:]
        item["name"] = name or item["name"]
        item["score"] = float(item.get("score") or 0) + score
        item["updated"] = _now()
        people[uid] = item
        self.prune_memory(int(self.cfg["memory"].get("max_people") or 100))
        self.save()

    def prune_memory(self, limit: int) -> None:
        people = self.memory()
        if len(people) <= limit:
            return
        ranked = sorted(people.items(), key=lambda kv: float(kv[1].get("score") or 0))
        for uid, _ in ranked[: max(0, len(people) - limit)]:
            people.pop(uid, None)

    def update_memory(self, uid: str, name: str | None, note: str | None) -> dict:
        people = self.memory()
        item = people.get(uid) or {"name": name or f"UID{uid}", "notes": [], "score": 0.0, "updated": _now()}
        if name:
            item["name"] = name
        if note:
            item["notes"] = (item["notes"] + [note])[-12:]
        item["updated"] = _now()
        people[uid] = item
        self.save()
        return item

    def delete_memory(self, uid: str) -> None:
        self.memory().pop(uid, None)
        self.save()

    # ---------- 卡片 ----------
    def cards(self, bucket: str) -> list[dict]:
        return self.state[bucket]["cards"]

    def upsert_card(self, bucket: str, card: dict) -> dict:
        cards = self.cards(bucket)
        cid = card.get("id") or uuid.uuid4().hex[:8]
        for idx, old in enumerate(cards):
            if old.get("id") == cid:
                cards[idx] = {**old, **card, "id": cid}
                self.save()
                return cards[idx]
        new = {"id": cid, "title": card.get("title", ""), "body": card.get("body", ""),
               "tags": card.get("tags", []), "enabled": bool(card.get("enabled", True))}
        cards.append(new)
        self.save()
        return new

    def delete_card(self, bucket: str, cid: str) -> None:
        self.state[bucket]["cards"] = [c for c in self.cards(bucket) if c.get("id") != cid]
        self.save()

    # ---------- 知识命中 ----------
    def knowledge_match(self, query: str, limit: int = 3) -> str:
        """只注入命中关键词的卡片，避免上下文被塞满。"""
        text = (query or "").lower()
        if not text:
            return ""
        scored = []
        for card in self.cards("knowledge"):
            if not card.get("enabled", True):
                continue
            keys = [str(card.get("title") or "")] + [str(t) for t in (card.get("tags") or [])]
            keys += re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", str(card.get("title") or ""))
            hit = sum(1 for k in keys if k and k.lower() in text)
            if hit:
                scored.append((hit, card))
        scored.sort(key=lambda kv: -kv[0])
        parts = [f"[{c.get('title')}] {c.get('body')}" for _, c in scored[:limit]]
        # 已知信息属于「随时可读」，一直带上
        for card in self.cards("known"):
            parts.append(f"[已知·{card.get('title')}] {card.get('body')}")
        return "\n".join(parts)[:2000]

    def pending_reviews(self) -> list[dict]:
        return self.state["review"]["pending"]

    def submit_review(self, item: dict) -> dict:
        item = {"id": uuid.uuid4().hex[:10], "ts": _now(), **item}
        self.state["review"]["pending"].append(item)
        self.save()
        return item

    def take_review(self, rid: str) -> dict | None:
        for item in list(self.state["review"]["pending"]):
            if item["id"] == rid:
                self.state["review"]["pending"].remove(item)
                self.save()
                return item
        return None

    def push_rejected(self, item: dict) -> None:
        self.state["review"]["rejected"] = (self.state["review"]["rejected"] + [item])[-100:]
        self.save()

    def next_image_seq(self, date_str: str) -> int:
        seq = int(self.state["imgseq"].get(date_str, 0)) + 1
        self.state["imgseq"][date_str] = seq
        self.save()
        return seq

    def add_history(self, record: dict) -> None:
        self.state["history"] = (self.state["history"] + [record])[-500:]
        self.save()
