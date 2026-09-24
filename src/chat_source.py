"""Chat Intake Module backed by WeChat CLI.

Encapsulates conversation discovery and message history extraction behind a deep interface,
eliminating OCR overhead, layout heuristics, and screen-capture permission issues.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from perception import Message, WindowInfo, find_wechat_window, frontmost_app_is_wechat


@dataclass
class ChatSession:
    chat: str
    username: str
    is_group: bool
    unread: int
    last_message: str
    msg_type: str
    time: str


@dataclass
class ChatSnapshot:
    chat_title: str
    messages: list[Message] = field(default_factory=list)
    window: dict[str, Any] | None = None
    unchanged: bool = False
    is_group: bool = False
    snapshot_key: tuple[str, str] = ("", "")


# Format: [2026-09-23 15:36] 龙龙: 一篇文章
_MSG_PATTERN = re.compile(r"^\[(?P<time>.*?)\]\s*(?P<sender>.*?):\s*(?P<text>.*)$", re.DOTALL)


import threading

class WeChatCliAdapter:
    """Adapter reading live chat data via the local wechat-cli utility."""

    def __init__(self, cli_path: str | None = None):
        self.cli_path = cli_path or self._resolve_cli()
        self._last_snapshot_key: tuple[str, str] | None = None
        self._cached_sessions: list[ChatSession] = []
        self._sessions_ts: float = 0.0
        self._history_cache: dict[str, tuple[float, list[Message], bool]] = {}
        self._cli_lock = threading.Lock()

    @staticmethod
    def _resolve_cli() -> str:
        user_local = os.path.expanduser("~/.local/bin/wechat-cli")
        if os.path.exists(user_local):
            return user_local
        found = shutil.which("wechat-cli")
        if found:
            return found
        homebrew_path = "/opt/homebrew/bin/wechat-cli"
        if os.path.exists(homebrew_path):
            return homebrew_path
        usr_local = "/usr/local/bin/wechat-cli"
        if os.path.exists(usr_local):
            return usr_local
        return "wechat-cli"

    def is_available(self) -> bool:
        return bool(shutil.which(self.cli_path) or os.path.exists(self.cli_path))

    def get_sessions(self, limit: int = 100, max_age: float = 10.0) -> list[ChatSession]:
        """Fetch active recent and historical 1-on-1 sessions with caching, filtering out groups, official accounts, and service accounts."""
        now = time.monotonic()
        if self._cached_sessions and (now - self._sessions_ts < max_age):
            return self._cached_sessions
        if not self._cli_lock.acquire(blocking=False):
            return self._cached_sessions
        try:
            # Query enough sessions to cover recent chats and moderate historical 1-on-1 contacts
            fetch_limit = max(limit * 3, 300)
            cmd = [self.cli_path, "sessions", "--filter-official", "--limit", str(fetch_limit)]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode != 0:
                # Fallback for older CLI versions that don't support --filter-official
                cmd = [self.cli_path, "sessions", "--limit", str(fetch_limit)]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if res.returncode != 0:
                    return self._cached_sessions

            raw = json.loads(res.stdout)
            sessions = []
            excluded_usernames = {
                "brandsessionholder", "brandservicesessionholder",
                "@placeholder_foldgroup", "filehelper", "fmessage",
                "medianote", "floatbottle", "qmessage", "qqmail", "newsapp",
                "weixin", "notifymessage", "tmessage", "mphelper", "qqsafe",
                "qqwanggou001", "youyiku811016", "cmb4008205555",
            }
            for item in raw:
                chat_name = (item.get("chat") or "").strip()
                username = (item.get("username") or "").strip()
                is_group = bool(item.get("is_group"))
                is_official = bool(item.get("is_official", False))
                verify_flag = int(item.get("verify_flag") or 0)

                # 1. 过滤群组
                if is_group or "@chatroom" in username:
                    continue

                # 2. 过滤公众号、服务号、系统号及企微营销客服
                if is_official or verify_flag > 0:
                    continue
                if not chat_name or username.startswith("gh_") or username in excluded_usernames:
                    continue
                if "@openim" in username or "@qy_u" in username:
                    continue
                if chat_name.startswith("@placeholder"):
                    continue

                sessions.append(ChatSession(
                    chat=chat_name,
                    username=username,
                    is_group=False,
                    unread=int(item.get("unread") or 0),
                    last_message=item.get("last_message") or "",
                    msg_type=item.get("msg_type") or "",
                    time=item.get("time") or "",
                ))
                if len(sessions) >= limit:
                    break

            self._cached_sessions = sessions
            self._sessions_ts = now
            return sessions
        except Exception:
            return self._cached_sessions
        finally:
            self._cli_lock.release()

    def get_latest(
        self,
        chat_name: str | None = None,
        limit: int = 15,
        max_age: float = 0.2,
    ) -> tuple[str, list[Message], bool, Any]:
        """Fetch latest messages using `wechat-cli latest` (sub-100ms speed).

        Returns (chat_title, messages, is_group, snapshot_key).
        """
        now = time.monotonic()
        cache_key = chat_name or "__ACTIVE__"
        cached = self._history_cache.get(cache_key)
        if cached and (now - cached[0] < max_age):
            return cached[1], cached[2], cached[3], cached[4]

        if not self._cli_lock.acquire(blocking=False):
            if cached:
                return cached[1], cached[2], cached[3], cached[4]
            return (chat_name or "", [], False, ("", 0))

        try:
            try:
                cmd = [self.cli_path, "latest"]
                if chat_name:
                    cmd.append(chat_name)
                cmd.extend(["--limit", str(limit), "--format", "json"])
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if res.returncode == 0:
                    data = json.loads(res.stdout)
                    chat_title = data.get("chat") or chat_name or ""
                    is_group = bool(data.get("is_group", False))
                    raw_msgs = data.get("messages") or []

                    messages: list[Message] = []
                    y_coord = 0.1
                    last_id = 0

                    for item in raw_msgs:
                        if isinstance(item, dict):
                            sender = (item.get("sender") or "").strip()
                            text = (item.get("text") or "").strip()
                            lid = item.get("local_id")
                            if lid is not None:
                                try:
                                    last_id = max(last_id, int(lid))
                                except (ValueError, TypeError):
                                    pass
                            if not text:
                                continue
                            if is_group:
                                is_me = (sender in ("me", "我"))
                            else:
                                is_me = (sender != chat_title)
                            side = "me" if is_me else "them"
                            messages.append(Message(
                                text=text,
                                side=side,
                                y=y_coord,
                                conf=1.0,
                                sender=sender if not is_me else None,
                                lines=[text],
                            ))
                            y_coord += 0.05
                        elif isinstance(item, str):
                            m = _MSG_PATTERN.match(item.strip())
                            if m:
                                sender = m.group("sender").strip()
                                text = m.group("text").strip()
                            else:
                                sender = ""
                                text = item.strip()
                            is_me = (sender == "me")
                            side = "me" if is_me else "them"
                            messages.append(Message(
                                text=text,
                                side=side,
                                y=y_coord,
                                conf=1.0,
                                sender=sender if not is_me else None,
                                lines=[text],
                            ))
                            y_coord += 0.05

                    snap_key = (chat_title, last_id if last_id > 0 else (messages[-1].text if messages else ""))
                    self._history_cache[cache_key] = (now, chat_title, messages, is_group, snap_key)
                    if chat_title and chat_title != cache_key:
                        self._history_cache[chat_title] = (now, chat_title, messages, is_group, snap_key)
                    return chat_title, messages, is_group, snap_key
            except Exception:
                pass

            # Fallback to get_history if latest failed or returned non-zero
            if chat_name:
                try:
                    msgs, is_g = self.get_history(chat_name, limit=limit, max_age=max_age)
                    last_txt = msgs[-1].text if msgs else ""
                    snap_key = (chat_name, last_txt)
                    return chat_name, msgs, is_g, snap_key
                except Exception:
                    pass
            if cached:
                return cached[1], cached[2], cached[3], cached[4]
            return (chat_name or "", [], False, ("", 0))
        finally:
            self._cli_lock.release()

    def get_history(self, chat_name: str, limit: int = 25, offset: int = 0, max_age: float = 0.5) -> tuple[list[Message], bool]:
        """Fetch recent or paginated historical messages for a conversation."""
        if not chat_name:
            return [], False
        now = time.monotonic()
        cache_key = f"{chat_name}__offset_{offset}__limit_{limit}" if offset > 0 else chat_name
        cached = self._history_cache.get(cache_key)
        if cached and (now - cached[0] < max_age):
            if len(cached) == 5:
                return cached[2], cached[3]
            return cached[1], cached[2]
        if not self._cli_lock.acquire(blocking=False):
            if cached:
                return (cached[2], cached[3]) if len(cached) == 5 else (cached[1], cached[2])
            return [], False
        try:
            cmd = [self.cli_path, "history", chat_name, "--limit", str(limit), "--offset", str(offset), "--format", "json"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode != 0:
                if cached:
                    return (cached[2], cached[3]) if len(cached) == 5 else (cached[1], cached[2])
                return [], False
            data = json.loads(res.stdout)
            is_group = bool(data.get("is_group"))
            raw_msgs = data.get("messages") or []
            messages: list[Message] = []
            y_coord = 0.1
            for raw_line in raw_msgs:
                if isinstance(raw_line, dict):
                    sender = (raw_line.get("sender") or "").strip()
                    text = (raw_line.get("text") or "").strip()
                    is_me = (sender == "me" or (not is_group and sender != chat_name))
                else:
                    m = _MSG_PATTERN.match(raw_line.strip())
                    if m:
                        sender = m.group("sender").strip()
                        text = m.group("text").strip()
                    else:
                        sender = ""
                        text = raw_line.strip()
                    is_me = (sender == "me")
                side = "me" if is_me else "them"
                messages.append(Message(
                    text=text,
                    side=side,
                    y=y_coord,
                    conf=1.0,
                    sender=sender if not is_me else None,
                    lines=[text],
                ))
                y_coord += 0.05
            self._history_cache[cache_key] = (now, messages, is_group)
            return messages, is_group
        except Exception:
            if cached:
                return (cached[2], cached[3]) if len(cached) == 5 else (cached[1], cached[2])
            return [], False
        finally:
            self._cli_lock.release()


_GLOBAL_ADAPTER = WeChatCliAdapter()


def fetch_older_history_via_cli(chat_name: str, offset: int, limit: int = 20) -> list[Message]:
    """Fetch older historical messages for paging backwards in time."""
    msgs, _ = _GLOBAL_ADAPTER.get_history(chat_name, limit=limit, offset=offset)
    return msgs


def read_conversation_via_cli(
    target_chat: str | None = None,
    prev_key: tuple[str, Any] | None = None,
    max_messages: int = 25,
) -> dict[str, Any]:
    """Drop-in deep replacement for perception.read_conversation.

    Reads live conversation state directly from wechat-cli latest with window geometry,
    providing exact sender/recipient identity in milliseconds without screen capture or OCR.
    """
    t0 = time.perf_counter()
    win_info = find_wechat_window()
    if win_info:
        window_dict = {
            "wid": win_info.wid,
            "pid": win_info.pid,
            "title": win_info.title,
            "x": win_info.x,
            "y": win_info.y,
            "w": win_info.w,
            "h": win_info.h,
        }
    else:
        window_dict = {
            "wid": 0,
            "pid": 0,
            "title": "WeChat",
            "x": 100,
            "y": 100,
            "w": 800,
            "h": 600,
        }

    chat_name = target_chat
    if not chat_name:
        sessions = _GLOBAL_ADAPTER.get_sessions(limit=5)
        if sessions:
            chat_name = sessions[0].chat

    if not chat_name:
        chat_title, messages, is_group, snapshot_key = _GLOBAL_ADAPTER.get_latest(None, limit=max_messages)
        if chat_title:
            chat_name = chat_title
        else:
            return {
                "ok": True,
                "unchanged": True,
                "messages": [],
                "chat_title": "请选择聊天对象",
                "window": window_dict,
                "input_rect": None,
                "is_group": False,
                "snapshot_key": ("", 0),
                "timing_ms": {"total": (time.perf_counter() - t0) * 1000, "capture_path": "wechat-cli"},
            }
    else:
        chat_title, messages, is_group, snapshot_key = _GLOBAL_ADAPTER.get_latest(chat_name, limit=max_messages)

    unchanged = bool(prev_key and snapshot_key == prev_key)
    total_ms = (time.perf_counter() - t0) * 1000
    return {
        "ok": True,
        "unchanged": unchanged,
        "messages": messages,
        "chat_title": chat_title or chat_name,
        "window": window_dict,
        "input_rect": None,
        "is_group": is_group,
        "snapshot_key": snapshot_key,
        "timing_ms": {"total": total_ms, "capture_path": "wechat-cli"},
    }


def get_available_sessions(limit: int = 100) -> list[ChatSession]:
    return _GLOBAL_ADAPTER.get_sessions(limit=limit)
