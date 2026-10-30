"""声线：edge-tts（微软免费音色，主）+ Windows SAPI（离线兜底）。

合成结果缓存成 mp3，本机播放；播放不阻塞主流程（直播说话与出图并行）。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import pathlib
import subprocess
import sys

from .config import RUNTIME

# 打包成 exe 后没有控制台，子进程窗口必须显式隐藏，否则每次播音都弹黑框
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

log = logging.getLogger("voice")
TTS_DIR = RUNTIME / "data" / "tts"

# 只列 edge-tts 服务端真实存在的中文音色（2026-10 实测可用）。
# 早期那批 Xiaomeng/Xiaoshuang/Xiaohan/Xiaoxuan/Xiaoyan/Xiaoyou/Yunye 服务端根本没有，
# 合成会静默失败并降级成 Windows Desktop 音色 —— 听起来就是"选哪个都是同一个机械女声"。
EDGE_VOICES = [
    ("zh-CN-XiaoyiNeural", "晓伊 · 少女音（默认）"),
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 温柔女声"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北 · 东北女声"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮 · 中原女声"),
    ("zh-CN-YunxiNeural", "云希 · 少年音"),
    ("zh-CN-YunyangNeural", "云扬 · 播音男声"),
    ("zh-CN-YunjianNeural", "云健 · 沉稳男声"),
    ("zh-CN-YunxiaNeural", "云夏 · 男孩音"),
    ("zh-HK-HiuGaaiNeural", "曉佳 · 粤语女声"),
    ("zh-HK-HiuMaanNeural", "曉曼 · 粤语女声"),
    ("zh-HK-WanLungNeural", "雲龍 · 粤语男声"),
    ("zh-TW-HsiaoChenNeural", "曉臻 · 台湾女声"),
    ("zh-TW-HsiaoYuNeural", "曉雨 · 台湾女声"),
    ("zh-TW-YunJheNeural", "雲哲 · 台湾男声"),
]

# edge 合成必须串行：并发请求会被服务端限流，返回空音频 → 静默降级成 Desktop 音
_EDGE_LOCK = asyncio.Lock()

SAPI_VOICES: list[tuple[str, str]] = []


def load_sapi_voices() -> list[dict]:
    """读取系统 SAPI 音色（离线可用）。"""
    global SAPI_VOICES
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Add-Type -AssemblyName System.Speech;"
             "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()"
             "|ForEach-Object{$_.VoiceInfo.Name}"],
            capture_output=True, text=True, timeout=20, encoding="utf-8", errors="ignore",
            creationflags=CREATE_NO_WINDOW)
        names = [line.strip() for line in out.stdout.splitlines() if line.strip()]
        # "Microsoft Xxx Desktop" 是给读屏软件用的低质量音色，合成出来又僵又怪，平时不列出来
        kept = [(n, n + " · 系统离线") for n in names if "Desktop" not in n]
        if not kept and names:
            # 系统只有 Desktop 音色时留一个当断网兜底，并在名字上写清楚
            pick = next((n for n in names if "Huihui" in n or "Chinese" in n), names[0])
            kept = [(pick, "系统离线兜底 · 音质一般（断网时用）")]
        SAPI_VOICES = kept
    except Exception:
        SAPI_VOICES = []
    return list_voices()


def list_voices() -> list[dict]:
    items = [{"id": vid, "name": name, "engine": "edge"} for vid, name in EDGE_VOICES]
    items += [{"id": vid, "name": name, "engine": "sapi"} for vid, name in SAPI_VOICES]
    return items


async def synth(text: str, voice: str, engine: str = "edge", rate: str = "+0%") -> pathlib.Path | None:
    text = (text or "").strip()
    if not text:
        return None
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    if engine == "edge":
        import edge_tts
        name = hashlib.md5(f"{voice}|{rate}|{text}".encode("utf-8")).hexdigest()[:16] + ".mp3"
        path = TTS_DIR / name
        if path.exists() and path.stat().st_size > 512:
            return path
        last = ""
        for attempt in range(3):  # 微软这边偶发空音频，三四次总能拿到
            try:
                async with _EDGE_LOCK:  # 串行合成，避免并发被限流
                    communicate = edge_tts.Communicate(text, voice or "zh-CN-XiaoyiNeural",
                                                       rate=rate or "+0%")
                    await asyncio.wait_for(communicate.save(str(path)), timeout=30)
                    await asyncio.sleep(0.15)
                if path.exists() and path.stat().st_size > 512:
                    return path
                last = "empty audio"
            except Exception as exc:
                last = str(exc)[:120]
            try:
                if path.exists() and path.stat().st_size <= 512:
                    path.unlink()  # 别留下坏缓存
            except OSError:
                pass
            await asyncio.sleep(0.5 * (attempt + 1))
        log.warning("edge-tts 音色 %s 不可用（%s），降级系统语音", voice, last)
    return await _synth_sapi(text, voice)


async def _synth_sapi(text: str, voice: str) -> pathlib.Path | None:
    name = hashlib.md5(f"sapi|{voice}|{text}".encode("utf-8")).hexdigest()[:16] + ".wav"
    path = TTS_DIR / name
    if path.exists() and path.stat().st_size > 1024:
        return path
    safe_text = text.replace("'", "''")[:160]
    pick = f"try{{$s.SelectVoice('{voice}')}}catch{{}};" if voice else ""
    script = ("Add-Type -AssemblyName System.Speech;"
              "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              f"{pick}$s.SetOutputToWaveFile('{path}');$s.Speak('{safe_text}');$s.Dispose();")
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-NonInteractive", "-Command", script,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW)
        await asyncio.wait_for(proc.wait(), timeout=25)
        return path if path.exists() and path.stat().st_size > 1024 else None
    except Exception as exc:
        log.debug("SAPI 合成失败: %s", exc)
        return None


async def play(path: pathlib.Path | None) -> None:
    if not path or not path.exists():
        return
    if path.suffix.lower() == ".wav":
        try:
            import winsound
            await asyncio.to_thread(winsound.PlaySound, str(path), winsound.SND_FILENAME)
        except Exception:
            pass
        return
    seconds = 12
    script = (
        "Add-Type -AssemblyName PresentationCore;"
        f"$p=New-Object System.Windows.Media.MediaPlayer;$p.Open([uri]'{path}');$p.Play();"
        "Start-Sleep -Milliseconds 400;"
        "while(-not $p.NaturalDuration.HasTimeSpan){Start-Sleep -Milliseconds 100};"
        f"Start-Sleep -Seconds {seconds};$p.Stop();"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-NonInteractive", "-Command", script,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW)
        await asyncio.wait_for(proc.wait(), timeout=seconds + 20)
    except Exception as exc:
        log.debug("播放失败: %s", exc)


async def speak(text: str, cfg_basic: dict, block: bool = False) -> pathlib.Path | None:
    voice = cfg_basic.get("voice") or "zh-CN-XiaoyiNeural"
    engine = cfg_basic.get("voice_engine") or "edge"
    rate = cfg_basic.get("voice_rate") or "+0%"
    path = await synth(text, voice, engine, rate)
    if path and block:
        await play(path)
    elif path:
        asyncio.create_task(play(path))
    return path
