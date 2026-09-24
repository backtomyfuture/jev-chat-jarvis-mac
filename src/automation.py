"""WeChat macOS Automation Layer.

Provides high-reliability window positioning, focus acquisition, contact
discovery, and automated message delivery for WeChat (fully compatible with 4.x).
"""

from __future__ import annotations

import argparse
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import AppKit
import Quartz

# Keycodes
RETURN_KEYCODE = 36  # Return / Enter
V_KEYCODE = 9        # 'v'
F_KEYCODE = 3        # 'f'

WECHAT_BUNDLE_ID = "com.tencent.xinWeChat"
WECHAT_NAMES = ("微信", "WeChat", "Weixin")


@dataclass
class WindowBounds:
    x: float
    y: float
    w: float
    h: float
    wid: int = 0
    pid: int = 0


@dataclass
class VisibleContact:
    name: str
    x: float
    y: float
    w: float
    h: float


class WeChatAutomation:
    """End-to-end automation controller for WeChat macOS."""

    def __init__(self):
        self._last_win: Optional[WindowBounds] = None

    # -------------------------------------------------------------------------
    # 1. 窗口与焦点管理 (Window & Focus)
    # -------------------------------------------------------------------------

    def bring_to_front(self, wait_s: float = 0.3) -> bool:
        """Force WeChat main window to system frontmost via System Events.

        AppleScript activation is mandatory on macOS to break through background
        throttling and ensure the window expands to full geometry.
        """
        cmd = """
        tell application "WeChat" to activate
        tell application "System Events"
            tell process "WeChat"
                set frontmost to true
            end tell
        end tell
        """
        res = subprocess.run(["osascript", "-e", cmd], capture_output=True)
        if wait_s > 0:
            time.sleep(wait_s)
        return res.returncode == 0

    def get_window_bounds(self, retry_count: int = 8) -> Optional[WindowBounds]:
        """Dynamically detect current active WeChat main chat window."""
        for _ in range(retry_count):
            opts = Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements
            wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)
            best = None
            for w in wins:
                owner = w.get("kCGWindowOwnerName") or ""
                if owner in WECHAT_NAMES:
                    b = w.get("kCGWindowBounds") or {}
                    pw, ph = float(b.get("Width", 0)), float(b.get("Height", 0))
                    if pw >= 450 and ph >= 300:
                        if best is None or pw * ph > best.w * best.h:
                            best = WindowBounds(
                                x=float(b.get("X", 0)),
                                y=float(b.get("Y", 0)),
                                w=pw,
                                h=ph,
                                wid=int(w.get("kCGWindowNumber") or 0),
                                pid=int(w.get("kCGWindowOwnerPID") or 0),
                            )
            if best is not None:
                self._last_win = best
                return best
            time.sleep(0.04)
        return self._last_win

    def click_input_box(self, win: Optional[WindowBounds] = None) -> bool:
        """Focus WeChat's input box using double penetration clicks.

        Golden coordinates:
          - X: 52% of window width (centers in the chat panel, avoids sidebar)
          - Y: 100px from window bottom (centers in the message composer,
               strictly avoids the toolbar and bottom Send bar)
        """
        if win is None:
            win = self.get_window_bounds()
        if win is None:
            return False

        target_x = win.x + win.w * 0.52
        target_y = win.y + win.h - 100.0
        point = Quartz.CGPointMake(target_x, target_y)

        # 1. 物理瞬移鼠标
        Quartz.CGWarpMouseCursorPosition(point)
        time.sleep(0.04)

        # 2. 划入事件唤醒 Hover
        move_ev = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, point, Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, move_ev)
        time.sleep(0.05)

        # 3. 双重穿透点击（第一下击破非活动窗口保护，第二下打入光标）
        for _ in range(2):
            down = Quartz.CGEventCreateMouseEvent(
                None, Quartz.kCGEventLeftMouseDown, point, Quartz.kCGMouseButtonLeft
            )
            up = Quartz.CGEventCreateMouseEvent(
                None, Quartz.kCGEventLeftMouseUp, point, Quartz.kCGMouseButtonLeft
            )
            Quartz.CGEventSetIntegerValueField(down, Quartz.kCGMouseEventClickState, 1)
            Quartz.CGEventSetIntegerValueField(up, Quartz.kCGMouseEventClickState, 1)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            time.sleep(0.05)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
            time.sleep(0.08)

        time.sleep(0.18)  # 等待光标闪烁就绪
        return True

    # -------------------------------------------------------------------------
    # 2. 发送与剪贴板 (Send & Clipboard)
    # -------------------------------------------------------------------------

    def send_message(self, text: str, press_enter: bool = True) -> bool:
        """Paste text into current focused chat and optionally press Enter.

        Preserves user's original clipboard contents.
        """
        pb = AppKit.NSPasteboard.generalPasteboard()
        old_str = pb.stringForType_(AppKit.NSPasteboardTypeString)

        # 1. 写入文本到剪贴板
        pb.clearContents()
        pb.setString_forType_(text, AppKit.NSPasteboardTypeString)

        # 2. Cmd+V 粘贴
        cmd_flag = Quartz.kCGEventFlagMaskCommand
        down_v = Quartz.CGEventCreateKeyboardEvent(None, V_KEYCODE, True)
        up_v = Quartz.CGEventCreateKeyboardEvent(None, V_KEYCODE, False)
        Quartz.CGEventSetFlags(down_v, cmd_flag)
        Quartz.CGEventSetFlags(up_v, cmd_flag)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down_v)
        time.sleep(0.02)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up_v)

        if press_enter:
            # 等待微信富文本自绘渲染完成
            time.sleep(0.15)

            # 发送纯净 Enter (Flags=0)
            down_enter = Quartz.CGEventCreateKeyboardEvent(None, RETURN_KEYCODE, True)
            up_enter = Quartz.CGEventCreateKeyboardEvent(None, RETURN_KEYCODE, False)
            Quartz.CGEventSetFlags(down_enter, 0)
            Quartz.CGEventSetFlags(up_enter, 0)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down_enter)
            time.sleep(0.025)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up_enter)

        # 还原用户原剪贴板
        time.sleep(0.08)
        if old_str:
            pb.clearContents()
            pb.setString_forType_(old_str, AppKit.NSPasteboardTypeString)
        return True

    # -------------------------------------------------------------------------
    # 3. 联系人检索与切换 (Contact Navigation)
    # -------------------------------------------------------------------------

    def open_chat_by_search(self, contact_name: str) -> bool:
        """Find contact or group chat via Cmd+F search and open conversation.

        Works for any contact in your address book or recent chats.
        """
        self.bring_to_front()

        # 1. 唤醒搜索框 (Cmd+F)
        cmd_flag = Quartz.kCGEventFlagMaskCommand
        down_f = Quartz.CGEventCreateKeyboardEvent(None, F_KEYCODE, True)
        up_f = Quartz.CGEventCreateKeyboardEvent(None, F_KEYCODE, False)
        Quartz.CGEventSetFlags(down_f, cmd_flag)
        Quartz.CGEventSetFlags(up_f, cmd_flag)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down_f)
        time.sleep(0.02)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up_f)
        time.sleep(0.2)  # 等待搜索框激活

        # 2. 写入联系人名并粘贴
        pb = AppKit.NSPasteboard.generalPasteboard()
        old_str = pb.stringForType_(AppKit.NSPasteboardTypeString)
        pb.clearContents()
        pb.setString_forType_(contact_name, AppKit.NSPasteboardTypeString)

        down_v = Quartz.CGEventCreateKeyboardEvent(None, V_KEYCODE, True)
        up_v = Quartz.CGEventCreateKeyboardEvent(None, V_KEYCODE, False)
        Quartz.CGEventSetFlags(down_v, cmd_flag)
        Quartz.CGEventSetFlags(up_v, cmd_flag)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down_v)
        time.sleep(0.02)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up_v)

        time.sleep(0.05)
        if old_str:
            pb.clearContents()
            pb.setString_forType_(old_str, AppKit.NSPasteboardTypeString)

        # 等待搜索结果列表呈现
        time.sleep(0.35)

        # 3. 按 Enter 键进入第一个匹配项
        down_enter = Quartz.CGEventCreateKeyboardEvent(None, RETURN_KEYCODE, True)
        up_enter = Quartz.CGEventCreateKeyboardEvent(None, RETURN_KEYCODE, False)
        Quartz.CGEventSetFlags(down_enter, 0)
        Quartz.CGEventSetFlags(up_enter, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down_enter)
        time.sleep(0.02)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up_enter)

        time.sleep(0.35)  # 等待聊天窗口加载
        return True

    def list_visible_contacts(self) -> list[VisibleContact]:
        """Identify visible contacts/groups in the left sidebar via Apple Vision OCR.

        Returns detected contacts with their screen click coordinates.
        """
        import Vision

        win = self.get_window_bounds()
        if not win:
            return []

        # 截取窗口
        opts = Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageNominalResolution
        img = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow, win.wid, opts
        )
        if img is None:
            # 尝试通过 screencapture 捕获
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png") as tf:
                p = subprocess.run(["screencapture", "-x", "-o", "-l", str(win.wid), tf.name], capture_output=True)
                if p.returncode == 0:
                    url = Quartz.CFURLCreateFromFileSystemRepresentation(None, tf.name.encode(), len(tf.name), False)
                    src = Quartz.CGImageSourceCreateWithURL(url, None)
                    if src:
                        img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)

        if img is None:
            return []

        # 裁剪出左侧会话列表区域：x 约 0.08 到 0.32
        iw, ih = Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img)
        crop_rect = Quartz.CGRectMake(iw * 0.08, ih * 0.06, iw * 0.24, ih * 0.90)
        sidebar_img = Quartz.CGImageCreateWithImageInRect(img, crop_rect)
        if sidebar_img is None:
            return []

        contacts: list[VisibleContact] = []

        def completion(req, err):
            if err:
                return
            for obs in req.results() or []:
                cands = obs.topCandidates_(1)
                if not cands:
                    continue
                text = cands[0].string().strip()
                if not text or len(text) < 1:
                    continue
                # 过滤明显不是人名的时间戳和图标文字
                if text.count(":") == 1 and any(c.isdigit() for c in text):
                    continue
                bb = obs.boundingBox()
                # 转换为屏幕坐标
                scr_x = win.x + win.w * 0.08 + bb.origin.x * (win.w * 0.24)
                # Vision 坐标是底部原点
                scr_y = win.y + win.h * 0.06 + (1.0 - bb.origin.y - bb.size.height) * (win.h * 0.90)
                contacts.append(VisibleContact(
                    name=text,
                    x=scr_x,
                    y=scr_y,
                    w=bb.size.width * win.w * 0.24,
                    h=bb.size.height * win.h * 0.90,
                ))

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(sidebar_img, None)
        request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(completion)
        request.setRecognitionLanguages_(["zh-Hans", "en-US"])
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        handler.performRequests_error_([request], None)
        return contacts

    def click_visible_contact(self, name: str) -> bool:
        """Click a contact directly from the left sidebar if visible."""
        contacts = self.list_visible_contacts()
        target = None
        for c in contacts:
            if name in c.name or c.name in name:
                target = c
                break
        if not target:
            return False

        point = Quartz.CGPointMake(target.x + target.w / 2, target.y + target.h / 2)
        Quartz.CGWarpMouseCursorPosition(point)
        time.sleep(0.04)
        for ev_type in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            ev = Quartz.CGEventCreateMouseEvent(None, ev_type, point, Quartz.kCGMouseButtonLeft)
            Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, 1)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(0.04)
        time.sleep(0.2)
        return True

    # -------------------------------------------------------------------------
    # 4. 一站式高阶接口 (High-Level Workflows)
    # -------------------------------------------------------------------------

    def send_to_contact(self, contact_name: str, message: str) -> bool:
        """Full end-to-end delivery: switch to contact, focus input box, send."""
        # 1. 优先尝试搜索定位并打开会话
        if not self.open_chat_by_search(contact_name):
            return False

        # 2. 聚焦输入框
        win = self.get_window_bounds()
        if not self.click_input_box(win):
            return False

        # 3. 发送消息
        return self.send_message(message, press_enter=True)


def main():
    parser = argparse.ArgumentParser(description="WeChat macOS Automation Controller")
    parser.add_argument("message", nargs="?", default=None, help="Text to send to current chat")
    parser.add_argument("--to", dest="target_contact", default=None, help="Contact/group name to send to")
    parser.add_argument("--search", dest="search_only", default=None, help="Search and open contact without sending")
    parser.add_argument("--list", action="store_true", help="List visible contacts via OCR")

    args = parser.parse_args()
    bot = WeChatAutomation()

    if args.list:
        bot.bring_to_front()
        contacts = bot.list_visible_contacts()
        print(f"识别到当前可视联系人/群聊 ({len(contacts)} 条):")
        for i, c in enumerate(contacts):
            print(f"  [{i+1}] {c.name} @ 坐标: ({c.x:.1f}, {c.y:.1f})")
        return

    if args.search_only:
        print(f"正在检索并打开: {args.search_only}...")
        ok = bot.open_chat_by_search(args.search_only)
        print(f"结果: {'成功' if ok else '失败'}")
        return

    if args.target_contact and args.message:
        print(f"正在向 [{args.target_contact}] 发送消息: {args.message}")
        ok = bot.send_to_contact(args.target_contact, args.message)
        print(f"发送结果: {'✅ 成功' if ok else '❌ 失败'}")
        return

    # 默认：向当前活跃会话发送
    msg = args.message or "自动化消息发送测试 🚀"
    print(f"正在向当前窗口发送消息: {msg}")
    bot.bring_to_front()
    win = bot.get_window_bounds()
    bot.click_input_box(win)
    ok = bot.send_message(msg, press_enter=True)
    print(f"发送结果: {'✅ 成功' if ok else '❌ 失败'}")


if __name__ == "__main__":
    main()
