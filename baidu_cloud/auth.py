# -*- coding: utf-8 -*-
"""百度网盘登录：需要登录信息时，打开一个浏览器窗口让用户本人登录。

两种方式（自动选择）：

1. 本机装了 playwright 时：弹出一个真正的浏览器窗口打开 pan.baidu.com，
   用户手动登录（扫码 / 账号密码都可以），脚本每 2 秒检查一次 Cookie，
   检测到 BDUSS（登录成功标志）后自动保存并关闭窗口。

2. 没有 playwright 时：用系统默认浏览器打开 pan.baidu.com，
   登录后用户把浏览器里复制的 Cookie 粘贴回终端，脚本解析出 BDUSS/STOKEN 保存。

凭据保存在本包目录的 credentials.json（已在 .gitignore 里，不会提交）。
后续 baidu_cloud 的离线下载、转存、容量查询模块都从这里取 Cookie。

用法：
    python -m baidu_cloud.auth login   # 打开浏览器窗口登录
    python -m baidu_cloud.auth status  # 查看当前登录状态
    python -m baidu_cloud.auth clear   # 删除本机保存的凭据
"""

from __future__ import annotations

import json
import re
import sys
import time
import webbrowser
from pathlib import Path

PAN_HOME = "https://pan.baidu.com/"
CREDENTIAL_FILE = Path(__file__).resolve().parent / "credentials.json"
LOGIN_TIMEOUT_SECONDS = 600  # 浏览器窗口最多等 10 分钟

# 凭据里真正有用的两枚 Cookie：BDUSS 是登录态，STOKEN 部分接口需要
KEY_COOKIES = ("BDUSS", "STOKEN")


def _save(cookies: list[dict[str, str]]) -> None:
    payload = {
        "saved_at": int(time.time()),
        "cookies": [{"name": c["name"], "value": c["value"], "domain": c.get("domain", "")} for c in cookies],
    }
    CREDENTIAL_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存登录凭据到 {CREDENTIAL_FILE}")


def load_credentials() -> dict | None:
    try:
        data = json.loads(CREDENTIAL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def cookies_dict() -> dict[str, str]:
    """返回后续调百度网盘接口要用的 Cookie 字典。"""
    data = load_credentials()
    if not data:
        return {}
    return {c["name"]: c["value"] for c in data.get("cookies", []) if c.get("name")}


def bduss() -> str:
    return cookies_dict().get("BDUSS", "")


def is_logged_in() -> bool:
    return bool(bduss())


def clear_credentials() -> bool:
    try:
        CREDENTIAL_FILE.unlink()
        return True
    except FileNotFoundError:
        return False


# 优先级：已装好的系统浏览器（Windows 上 Edge 一定在）→ playwright 自带内核。
# 自带内核要单独下载，国内网络经常下不动，所以先试系统浏览器。
LAUNCH_CHANNELS = ("msedge", "chrome", None)


def _launch_browser(p):
    """按 LAUNCH_CHANNELS 依次尝试，返回 (browser, 失败原因列表)。"""
    reasons: list[str] = []
    for channel in LAUNCH_CHANNELS:
        try:
            kwargs = {"headless": False}
            if channel:
                kwargs["channel"] = channel
            return p.chromium.launch(**kwargs), reasons
        except Exception as exc:  # noqa: BLE001 - 换下一个候选
            reasons.append(f"{channel or 'playwright 自带内核'}: {exc}".splitlines()[0])
    return None, reasons


def _login_with_playwright(timeout: int = LOGIN_TIMEOUT_SECONDS) -> bool:
    """弹出有界面的浏览器窗口，用户手动登录，检测到 BDUSS 后自动保存。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False

    print("正在打开浏览器窗口，请在窗口里登录百度网盘（扫码或账号密码都行）…")
    try:
        with sync_playwright() as p:
            browser, reasons = _launch_browser(p)
            if browser is None:
                print("没有可用的浏览器内核：")
                for line in reasons:
                    print(f"  - {line}")
                print("可以执行一次: python -m playwright install chromium（或直接选粘贴 Cookie 方式）")
                return False
            context = browser.new_context()
            page = context.new_page()
            page.goto(PAN_HOME)
            deadline = time.time() + timeout
            while time.time() < deadline:
                bd = next(
                    (c for c in context.cookies(PAN_HOME) if c.get("name") == "BDUSS" and c.get("value")),
                    None,
                )
                if bd:
                    _save(context.cookies(PAN_HOME))
                    print("检测到登录成功（BDUSS），窗口即将关闭。")
                    browser.close()
                    return True
                time.sleep(2)
            browser.close()
    except Exception as exc:  # noqa: BLE001 - 浏览器起不来就退回粘贴 Cookie
        print(f"打开浏览器窗口失败: {exc}")
        return False
    print("等待超时，没有检测到登录。可重新执行本命令再试。")
    return False


def _parse_cookie_text(raw: str) -> list[dict[str, str]]:
    """解析用户粘贴的 Cookie 头或 key=value 片段。"""
    cookies: list[dict[str, str]] = []
    for piece in re.split(r"[;\n]+", raw or ""):
        if "=" not in piece:
            continue
        name, _, value = piece.strip().partition("=")
        name = name.strip()
        value = value.strip()
        if name and value:
            cookies.append({"name": name, "value": value, "domain": ".baidu.com"})
    return cookies


def _login_with_paste() -> bool:
    webbrowser.open(PAN_HOME)
    print("已用默认浏览器打开百度网盘。请登录后：")
    print("  1. 按 F12 打开开发者工具 → Network（网络）标签")
    print("  2. 刷新页面，点击第一个 pan.baidu.com 请求 → Headers（标头）")
    print("  3. 复制「Cookie:」后面的一整行，粘贴到下面（输入完成后回车）")
    try:
        raw = input("Cookie 行: ").strip()
    except (EOFError, KeyboardInterrupt):  # 后台任务 / 重定向输入时
        print("\n当前环境接收不到键盘输入，请在有终端的窗口里执行本命令。")
        return False
    cookies = _parse_cookie_text(raw)
    if not any(c["name"] in KEY_COOKIES for c in cookies):
        print("粘贴的内容里没有找到 BDUSS，可能还没登录或复制不完整。")
        return False
    _save(cookies)
    return True


def login(force: bool = False) -> bool:
    """打开浏览器窗口让用户本人登录。

    force=True 用于「本机有 Cookie 但已经过期」的情况：先删掉旧凭据再登录。
    """
    if is_logged_in():
        if not force:
            print("本机已有保存的登录凭据。如需重新登录，先执行: python -m baidu_cloud.auth clear")
            return True
        print("旧的登录凭据已失效，先删掉再重新登录。")
        clear_credentials()
    if _login_with_playwright():
        return True
    print("没能弹出浏览器窗口（没装 playwright，或缺 Chromium 内核），改用手动粘贴 Cookie 方式。")
    return _login_with_paste()


def verify() -> bool:
    """拿保存的 Cookie 真问一次接口，确认还能用（Cookie 过期时本地看着像已登录）。"""
    try:
        from .client import BaiduClient
    except ImportError:  # 直接 `python baidu_cloud/auth.py` 的场合
        return is_logged_in()
    try:
        return BaiduClient().check_login()
    except Exception as exc:  # noqa: BLE001 - 网络问题时不要误报为未登录
        print(f"检查登录态时出错（可能只是网络问题）: {exc}")
        return is_logged_in()


def status() -> None:
    data = load_credentials()
    if not data or not is_logged_in():
        print("当前没有有效的登录凭据。执行 python -m baidu_cloud.auth login 登录。")
        return
    import datetime

    saved = datetime.datetime.fromtimestamp(data.get("saved_at", 0)).strftime("%Y-%m-%d %H:%M:%S")
    names = [c["name"] for c in data.get("cookies", [])]
    print(f"已保存凭据：{saved}，包含 Cookie: {', '.join(names)}")
    print("登录态：有效" if verify() else "登录态：已失效（执行 python -m baidu_cloud.auth login --force 重新登录）")


def main(argv: list[str]) -> int:
    action = argv[1] if len(argv) > 1 else "status"
    if action == "login":
        ok = login(force="--force" in argv[2:])
        if ok:
            print("登录态验证通过。" if verify() else "凭据已保存，但验证没通过，可能登录没完成。")
        return 0 if ok else 1
    if action == "status":
        status()
        return 0
    if action == "clear":
        print("已删除凭据。" if clear_credentials() else "没有可删除的凭据。")
        return 0
    print("用法: python -m baidu_cloud.auth [login [--force]|status|clear]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
