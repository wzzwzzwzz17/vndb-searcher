# -*- coding: utf-8 -*-
"""百度网盘 Web 接口客户端（Cookie 登录态，不依赖开放平台 access_token）。

为什么用 Cookie 而不是官方 API：离线下载（磁力/BT）只有 Web 端有，
开放平台没有对应接口。Cookie 取自 `baidu_cloud.auth`——需要登录时会打开
真实浏览器窗口让用户本人登录，脚本不接触密码。

主要能力：
- 容量查询 / 列目录 / 建目录 / 删除 / 递归统计体积
- 分享链接解析与转存（share.py）
- 磁力离线下载（offline.py，需要 SVIP）
- 分片上传（upload.py）
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote

import requests

try:  # 允许单独测试本包
    from . import auth
except ImportError:  # pragma: no cover
    import auth  # type: ignore[no-redef]

PAN = "https://pan.baidu.com"
PCS = "https://d.pcs.baidu.com"
APP_ID = "250528"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# 常见 errno 的人话解释，报错时直接告诉用户怎么办
ERRNO_TEXT = {
    -1: "百度网盘接口暂时不可用，稍后重试",
    -6: "登录态无效，请重新执行: python -m baidu_cloud.auth login",
    -7: "账号被限制，或当前 IP 需要验证",
    2: "参数错误",
    4: "请求过于频繁，被限流了，等一会儿再跑",
    5: "登录态失效",
    10: "网盘容量不足，先清理空间或调小 max_total_gb",
    12: "目录已存在",
    105: "分享链接已失效",
    111: "有其他转存任务在进行",
    112: "转存失败，目标目录不存在",
    113: "转存失败，分享已取消",
    114: "转存超过次数限制",
    31064: "该操作需要百度网盘 SVIP（离线下载仅会员可用）",
    31066: "文件或目录不存在",
    31023: "参数错误",
    31024: "文件不存在",
    31061: "文件已存在",
    31075: "需要验证码",
    -62: "请求被拒绝，可能是账号风控或 Cookie 过期",
}


class BaiduError(RuntimeError):
    """百度网盘接口返回非 0 errno，或网络层失败。"""

    def __init__(self, errno: int | str, message: str = "", context: str = ""):
        self.errno = errno
        self.context = context
        self.message = message
        friendly = ERRNO_TEXT.get(errno) if isinstance(errno, int) else None
        # 服务端自己给的说明更具体（例如 HTTP 403），两者都有就一起显示
        if message and friendly:
            text = f"{friendly}（{message}）"
        else:
            text = message or friendly or f"errno={errno}"
        if context:
            text = f"{context}: {text}"
        super().__init__(f"[baidu {errno}] {text}")


class BaiduClient:
    """一个共享会话，内部缓存 bdstoken。"""

    def __init__(self, cookies: dict[str, str] | None = None, timeout: float = 30.0):
        self.timeout = float(timeout)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Referer": f"{PAN}/disk/home",
            "Accept": "application/json, text/plain, */*",
        })
        self._bdstoken = ""
        if cookies is not None:
            # 显式传入（哪怕是空字典）就不去读本机凭据，方便测试与临时切换账号
            self.set_cookies(cookies)
        else:
            self.load_cookies()

    # ---------- 登录态 ----------
    def load_cookies(self) -> bool:
        cookies = auth.cookies_dict()
        if cookies:
            self.set_cookies(cookies)
        return bool(cookies)

    def set_cookies(self, cookies: dict[str, str]) -> None:
        for name, value in (cookies or {}).items():
            if not name or not value:
                continue
            self.session.cookies.set(name, value, domain=".baidu.com", path="/")

    @property
    def logged_in(self) -> bool:
        return bool(self.session.cookies.get("BDUSS"))

    def require_login(self) -> None:
        if not self.logged_in:
            raise BaiduError(-6, "没有登录", "百度网盘")

    def check_login(self) -> bool:
        """真的去问一次接口，确认 Cookie 还有效（BDUSS 过期时本地看着像已登录）。"""
        if not self.logged_in:
            return False
        try:
            self._fetch_bdstoken()
        except BaiduError:
            return False
        return True

    # ---------- 底层请求 ----------
    def _raise_http(self, resp: requests.Response) -> None:
        """非 200 时先看 body：百度有时候用 400 携带真正的 errno，直接抛 HTTP 码会丢信息。

        两种字段风格都要认：`errno`/`errmsg`（api/*）和 `error_code`/`error_msg`（cloud_dl）。
        """
        body = {}
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            body = {}
        errno = body.get("errno", body.get("error_code"))
        if errno not in (None, 0, "0"):
            message = str(body.get("errmsg") or body.get("error_msg") or body.get("show_msg") or "")
            raise BaiduError(errno, message, "百度网盘")
        raise BaiduError(-1, f"HTTP {resp.status_code}", "百度网盘")

    def _get(self, url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> requests.Response:
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise BaiduError(-1, f"连接失败: {exc}", "百度网盘") from exc
        if resp.status_code != 200:
            self._raise_http(resp)
        return resp

    def _post(self, url: str, params: dict[str, Any] | None = None, data: Any = None, **kwargs: Any) -> requests.Response:
        try:
            resp = self.session.post(url, params=params, data=data, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise BaiduError(-1, f"连接失败: {exc}", "百度网盘") from exc
        if resp.status_code != 200:
            self._raise_http(resp)
        return resp

    def _json(self, resp: requests.Response, context: str) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError as exc:
            raise BaiduError(-1, "返回的不是 JSON（可能被跳转到登录页）", context) from exc
        if not isinstance(data, dict):
            raise BaiduError(-1, "返回结构异常", context)
        errno = data.get("errno", 0)
        if errno not in (0, "0", None):
            raise BaiduError(errno, str(data.get("error_msg") or data.get("show_msg") or ""), context)
        return data

    def common_params(self) -> dict[str, Any]:
        return {
            "channel": "chunlei",
            "web": "1",
            "app_id": APP_ID,
            "clienttype": "0",
            "bdstoken": self.bdstoken,
        }

    # ---------- bdstoken ----------
    @property
    def bdstoken(self) -> str:
        if not self._bdstoken:
            self._bdstoken = self._fetch_bdstoken()
        return self._bdstoken

    def _fetch_bdstoken(self) -> str:
        self.require_login()
        resp = self._get(f"{PAN}/api/gettemplatevariable", params={"fields": '["bdstoken"]', **self.common_params_no_token()})
        data = self._json(resp, "取 bdstoken")
        token = ((data.get("result") or {}).get("bdstoken") or "").strip()
        if not token:
            raise BaiduError(-6, "没拿到 bdstoken，登录态可能已失效", "百度网盘")
        return token

    def common_params_no_token(self) -> dict[str, Any]:
        return {"channel": "chunlei", "web": "1", "app_id": APP_ID, "clienttype": "0"}

    # ---------- 账号 / 容量 ----------
    def quota(self) -> dict[str, float]:
        """返回 total/used/free，单位 GB（十进制，1GB=10^9 字节，和网盘页面显示一致）。

        实测 /api/quota 的 free 字段现在恒为 0，实际剩余得用 total-used 算。
        """
        data = self._json(self._get(f"{PAN}/api/quota", params=self.common_params()), "查询容量")
        gb = 1000 ** 3
        total = float(data.get("total") or 0)
        used = float(data.get("used") or 0)
        free = float(data.get("free") or 0) or max(0.0, total - used)
        return {
            "total_gb": round(total / gb, 2),
            "used_gb": round(used / gb, 2),
            "free_gb": round(free / gb, 2),
        }

    def user_info(self) -> dict[str, Any]:
        """账号信息。

        `/api/user/getinfo` 现在回 `{"errmsg":"params error","errno":2}`，
        改用 xpan 的 uinfo 接口（同样是 Cookie 登录态就够）。
        """
        data: dict[str, Any] = {}
        try:
            data = self._json(
                self._get(f"{PAN}/rest/2.0/xpan/nas", params={**self.common_params(), "method": "uinfo"}),
                "取账号信息",
            )
        except BaiduError:
            return {}
        vip_type = int(data.get("vip_type") or 0)
        return {
            "name": data.get("netdisk_name") or data.get("baidu_name") or "",
            "uk": data.get("uk") or "",
            "vip": vip_type > 0,
            "vip_type": vip_type,
        }

    # ---------- 目录 ----------
    def list_dir(self, remote_dir: str, page: int = 1, num: int = 1000) -> list[dict[str, Any]]:
        """列出目录内容；目录本身不存在时抛 errno 31066。"""
        params = {
            "dir": remote_dir or "/",
            "order": "name",
            "desc": "0",
            "showempty": "0",
            "page": str(page),
            "num": str(num),
            **self.common_params(),
        }
        data = self._json(self._get(f"{PAN}/api/list", params=params), f"列目录 {remote_dir}")
        return [item for item in (data.get("list") or []) if isinstance(item, dict)]

    def exists(self, remote_path: str) -> bool:
        parent = posix_parent(remote_path)
        name = posix_name(remote_path)
        try:
            items = self.list_dir(parent)
        except BaiduError as exc:
            if exc.errno == 31066:
                return False
            raise
        return any(str(item.get("server_filename") or "") == name for item in items)

    def mkdir(self, remote_dir: str) -> bool:
        """建目录（已存在返回 False，不报错）。

        实测参数：`dir` 要放**父目录**，新目录的完整路径放在 body 的 `path` 里。
        只给 `dir` 不给 `path` 会回 errno=2（参数错误）。
        """
        target = "/" + str(remote_dir or "").strip("/")
        body = {"path": target, "isdir": "1", "block_list": "[]", "size": "0"}
        params = {"a": "commit", "dir": posix_parent(target), **self.common_params()}
        resp = self._post(f"{PAN}/api/create", params=params, data=body)
        try:
            data = resp.json()
        except ValueError as exc:
            raise BaiduError(-1, "建目录返回不是 JSON", "百度网盘") from exc
        errno = data.get("errno")
        if errno in (0, "0", None):
            return bool(data.get("fs_id"))
        if errno in (12, -8, 31061):  # 已存在
            return False
        raise BaiduError(errno, str(data.get("error_msg") or ""), f"建目录 {remote_dir}")

    def ensure_dir(self, remote_dir: str) -> str:
        """逐级创建目录。"""
        parts = [p for p in str(remote_dir or "/").split("/") if p]
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            self.mkdir(current)
        return "/" if not parts else current

    def delete(self, remote_paths: list[str]) -> bool:
        if not remote_paths:
            return True
        params = {"opera": "delete", "async": "1", "bdstoken": self.bdstoken,
                  "channel": "chunlei", "web": "1", "app_id": APP_ID, "clienttype": "0"}
        body = {"filelist": json.dumps(remote_paths, ensure_ascii=False)}
        resp = self._post(f"{PAN}/api/filemanager", params=params, data=body)
        try:
            data = resp.json()
        except ValueError:
            return False
        return data.get("errno") in (0, "0", None)

    def rename(self, remote_path: str, new_name: str) -> bool:
        params = {"opera": "rename", "bdstoken": self.bdstoken, "channel": "chunlei",
                  "web": "1", "app_id": APP_ID, "clienttype": "0"}
        body = {"filelist": json.dumps([{"path": remote_path, "newname": new_name}], ensure_ascii=False)}
        resp = self._post(f"{PAN}/api/filemanager", params=params, data=body)
        try:
            return resp.json().get("errno") in (0, "0", None)
        except ValueError:
            return False

    # ---------- 递归统计 ----------
    def dir_size_bytes(self, remote_dir: str, max_depth: int = 4) -> int:
        """递归统计目录体积。目录不存在/为空返回 0。"""
        total = 0
        stack: list[tuple[str, int]] = [(remote_dir, 0)]
        visited: set[str] = set()
        while stack:
            path, depth = stack.pop()
            if path in visited or depth > max_depth:
                continue
            visited.add(path)
            try:
                items = self.list_dir(path)
            except BaiduError as exc:
                if exc.errno == 31066:
                    continue
                raise
            for item in items:
                name = str(item.get("server_filename") or "")
                child = f"{path.rstrip('/')}/{name}"
                if int(item.get("isdir") or 0):
                    stack.append((child, depth + 1))
                else:
                    total += int(item.get("size") or 0)
        return total

    def dir_size_gb(self, remote_dir: str) -> float:
        return round(self.dir_size_bytes(remote_dir) / (1000 ** 3), 3)

    # ---------- Cookie 导入 ----------
    @classmethod
    def from_cookie_text(cls, raw: str, save: bool = False) -> "BaiduClient":
        """从浏览器复制的一整行 Cookie 里还原客户端。

        默认**不**写本机凭据文件：只有用户明确要保存（`python -m galsave login --text`）
        时才落盘，避免测试或临时试用的 Cookie 覆盖掉真实登录态。
        """
        cookies = parse_cookie_text(raw)
        if not any(name == "BDUSS" for name in cookies):
            raise BaiduError(-6, "粘贴的 Cookie 里没有 BDUSS", "百度网盘")
        client = cls(cookies=cookies)
        if save:
            auth._save([{"name": k, "value": v, "domain": ".baidu.com"} for k, v in cookies.items()])
        return client


def parse_cookie_text(raw: str) -> dict[str, str]:
    """解析 Cookie 头或 `a=1; b=2` 片段。"""
    out: dict[str, str] = {}
    for piece in str(raw or "").replace("\n", ";").split(";"):
        if "=" not in piece:
            continue
        name, _, value = piece.strip().partition("=")
        name, value = name.strip(), value.strip()
        if name and value:
            out[name] = unquote(value)
    return out


def posix_parent(path: str) -> str:
    path = "/" + str(path or "").strip("/")
    parent = path.rsplit("/", 1)[0]
    return parent or "/"


def posix_name(path: str) -> str:
    return str(path or "").strip("/").rsplit("/", 1)[-1]


def join_posix(base: str, *parts: str) -> str:
    out = "/" + str(base or "").strip("/")
    for part in parts:
        part = str(part or "").strip("/")
        if part:
            out = f"{out.rstrip('/')}/{part}"
    return out
