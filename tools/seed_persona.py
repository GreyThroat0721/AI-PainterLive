"""把默认人设写进现有的 config.json（源码目录与 exe 目录各一份）。"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app.config import DEFAULTS, USER_BACKUP  # noqa: E402

persona = DEFAULTS["basic"]["persona"]
root = pathlib.Path(__file__).resolve().parent.parent

targets = [root / "data" / "config.json", root / "dist" / "PainterLive" / "data" / "config.json"]
for path in targets:
    if not path.exists():
        print("skip (missing):", path)
        continue
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print("skip (bad json):", path, exc)
        continue
    cfg.setdefault("basic", {})["persona"] = persona
    cfg["basic"].setdefault("master_name", "主人")
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("seeded:", path, "persona_len=", len(persona))

try:
    USER_BACKUP.parent.mkdir(parents=True, exist_ok=True)
    if targets[0].exists():
        USER_BACKUP.write_text(targets[0].read_text(encoding="utf-8"), encoding="utf-8")
        print("user backup:", USER_BACKUP)
except Exception as exc:
    print("user backup failed:", exc)
