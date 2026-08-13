"""友伴 桌面版启动器（pywebview）。

把本地 FastAPI 服务塞进一个原生窗口，并暴露文件/目录选择 API，
使「拖拽/选择拿到真实本地绝对路径」成为可能（纯浏览器版做不到）。

用法：
    uv run python desktop_app.py
或双击生成的 desktop.bat / desktop.ps1 / desktop.sh

环境变量：
    HOST / PORT       监听地址（默认 127.0.0.1:8000）
    MB_DESKTOP=1      标记当前为桌面模式（自动放开文件沙箱）
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
import webbrowser
from pathlib import Path

import webview

from app.config import config

_STATIC = Path(__file__).resolve().parent / "static"

# pywebview 新版用 FileDialog 枚举替代旧常量（OPEN_DIALOG/SAVE_DIALOG/FOLDER_DIALOG
# 已弃用，未来版本会移除）。做一层兼容：有枚举就用枚举，否则回退旧常量，
# 避免老版本 pywebview 安装直接 NameError。
try:
    _DLG_OPEN = webview.FileDialog.OPEN
    _DLG_FOLDER = webview.FileDialog.FOLDER
    _DLG_SAVE = webview.FileDialog.SAVE
except AttributeError:  # pragma: no cover - 老版本兜底
    _DLG_OPEN = webview.OPEN_DIALOG
    _DLG_FOLDER = webview.FOLDER_DIALOG
    _DLG_SAVE = webview.SAVE_DIALOG


def _apply_window_icon() -> None:
    """Win32：给顶层窗口设置任务栏/标题栏图标。

    覆盖 edgechromium(WebView2) 渲染器——该渲染器不读取 pywebview 的 icon 参数，
    只有老式 winforms/gtk/qt 才用。这里直接通过 EnumWindows + SendMessage(WM_SETICON)
    钉图标，对任意渲染器都生效。非 Windows 平台直接跳过。
    """
    if not config.IS_WINDOWS:
        return
    try:
        import ctypes.wintypes as wintypes

        user32 = ctypes.windll.user32
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.SendMessageW.restype = wintypes.LRESULT
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.LoadImageW.restype = wintypes.HANDLE

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        WM_SETICON = 0x0080
        ICON_SMALL, ICON_BIG = 0, 1
        ico = str(_STATIC / "icon.ico")
        h_small = user32.LoadImageW(0, ico, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        h_big = user32.LoadImageW(0, ico, IMAGE_ICON, 256, 256, LR_LOADFROMFILE)

        target_title = "友伴 · 本地 AI 助手"
        found: list = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_cb(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(hwnd, buf, length)
            if target_title in buf.value:
                if h_small:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_small)
                if h_big:
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
                found.append(hwnd)
            return True  # 继续枚举

        # 轮询直到找到窗口（标题可能在窗口创建后稍晚设置）
        for _ in range(50):
            user32.EnumWindows(enum_cb, 0)
            if found:
                break
            time.sleep(0.1)
    except Exception:  # noqa: BLE001
        pass


class Api:
    """暴露给前端 JS 的桌面能力：原生文件/目录选择对话框。"""

    def pick_file(self) -> list[str]:
        result = webview.windows[0].create_file_dialog(_DLG_OPEN)
        return list(result) if result else []

    def pick_dir(self) -> list[str]:
        result = webview.windows[0].create_file_dialog(_DLG_FOLDER)
        return list(result) if result else []

    def save_text(self, filename: str, content: str) -> str:
        """桌面端：弹出原生保存对话框把文本写盘，返回实际路径；取消/关闭返回空串。

        解决 WebView2 下 <a download> blob 静默不落盘的问题（无下载处理器）。
        """
        result = webview.windows[0].create_file_dialog(
            _DLG_SAVE,
            save_filename=filename,
            file_types=("Markdown Files (*.md)", "All Files (*.*)"),
        )
        if not result:
            return ""
        # SAVE_DIALOG 返回字符串路径（个别版本包成单元素元组），统一处理
        path = result[0] if isinstance(result, (list, tuple)) else result
        if not path.lower().endswith(".md"):
            path += ".md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path


def _start_server() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        log_level="info",
    )


def main() -> None:
    # 桌面模式：放开文件沙箱（允许用户选择真实本地文件并读写）
    os.environ.setdefault("MB_DESKTOP", "1")

    # 在后台线程启动 FastAPI 服务
    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start()

    url = f"http://{config.HOST}:{config.PORT}/"

    # 等待服务起来（最多 ~10s）
    import socket

    for _ in range(100):
        try:
            with socket.create_connection((config.HOST, config.PORT), timeout=0.3):
                break
        except OSError:
            time.sleep(0.1)

    api = Api()
    window = webview.create_window(
        "友伴 · 本地 AI 助手",
        url,
        js_api=api,
        width=1180,
        height=760,
        min_size=(900, 600),
    )
    # 局部调试用：仅当设置 MB_DEBUG=1 时自动打开 DevTools（避免默认打扰用户）
    if config.MB_DEBUG:
        try:
            window.evaluate_js("if(window.chrome&&chrome.webview)chrome.webview.openDevTools();")
        except Exception:  # noqa: BLE001
            pass
    # Win32 钉图标（覆盖 edgechromium；winforms 等另由 start(icon=) 处理）
    threading.Thread(target=_apply_window_icon, daemon=True).start()
    webview.start(debug=False, icon=str(_STATIC / "icon.ico"))


if __name__ == "__main__":
    main()
