"""桌面向导：起本地服务 -> 打开一个 1920×1080 的应用窗口（可全屏）。

窗口优先级：pywebview（原生）> Edge --app（零依赖）> 默认浏览器。
"""
from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from aiohttp import web


def _free_port(start: int = 8787) -> int:
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def _serve(app, host: str, port: int) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = web.AppRunner(app, access_log=None)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, host, port)
    loop.run_until_complete(site.start())
    loop.run_forever()


def _find_edge() -> str | None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for item in candidates:
        if Path(item).exists():
            return item
    return None


def open_window(url: str) -> str:
    """打开窗口，返回所用方式。

    webview / edge 会阻塞到窗口关闭（服务因此保持存活）；
    browser 无法等待，调用方需要自己保活。
    """
    try:
        import webview  # pywebview 可选
        webview.create_window("AI 绘画直播间 · 控制面板", url, width=1600, height=900,
                              min_size=(1280, 720), confirm_close=False)
        webview.start()
        return "webview"
    except Exception:
        pass
    edge = _find_edge()
    if edge:
        # 必须用独立 user-data-dir，否则 Edge 会把参数转给常驻实例后自己立刻退出，
        # 窗口一闪、服务跟着死（表现为界面"未找到文件"）。
        profile = _log_path().parent / "edge-profile"
        proc = subprocess.Popen([edge, f"--app={url}", "--window-size=1600,900",
                                 "--start-maximized", "--no-first-run",
                                 f"--user-data-dir={profile}",
                                 "--disable-features=Translate,msEdgeIdentityScope"])
        log(f"edge 已启动 pid={proc.pid}，服务保持到窗口关闭")
        proc.wait()
        log("窗口已关闭")
        return "edge"
    webbrowser.open(url)
    return "browser"


def _log_path() -> Path:
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return root / "launcher.log"


def log(msg: str) -> None:
    try:
        with _log_path().open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def main() -> None:
    from app.server import create_app

    log("launcher 启动")
    app = create_app()
    port = _free_port(8787)
    log(f"选定端口 {port}")
    url = f"http://127.0.0.1:{port}/panel"
    thread = threading.Thread(target=_serve, args=(app, "127.0.0.1", port), daemon=True)
    thread.start()

    # 必须等 /api/state 真的能响应再开窗，否则窗口会停在错误页
    import urllib.request
    ready = False
    for _ in range(200):  # 最多 60 秒
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(0.3)
    if not ready:
        log(f"服务启动超时 port={port}")
        print("[painter] 服务启动超时，仍尝试打开窗口")
    else:
        log(f"服务就绪 port={port}")
    print(f"[painter] 控制面板: {url}")
    log(f"打开窗口 {url}")
    mode = open_window(url)
    if mode == "browser":
        log("浏览器模式：服务继续运行，关掉本进程即可退出")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        log("崩溃: " + traceback.format_exc())
        raise
