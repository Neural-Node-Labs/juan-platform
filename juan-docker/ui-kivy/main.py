"""
Juan Kivy Desktop App — C-19 KIVY_UI
Production-grade desktop client matching the React UI aesthetic.
Runs on Windows, macOS, and Linux.
"""
from __future__ import annotations

# Must set environment BEFORE importing Kivy
import os
os.environ.setdefault("KIVY_NO_CONSOLELOG", "1")

import json
import re
import threading
import time
from datetime import datetime
from typing import Any

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.recycleview import RecycleView
from kivy.uix.recycleview.views import RecycleDataViewBehavior
from kivy.uix.scrollview import ScrollView
from kivy.uix.stacklayout import StackLayout
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle, RoundedRectangle, Line
from kivy.lang import Builder

from utils.client import JuanClient
from utils.config import (
    APP_TITLE, COLORS, HTTP_URL, PASSWORD, SETTINGS_FILE, WS_URL
)

# ── KV layout string ──────────────────────────────────────────────────────────
KV = """
#:import dp kivy.metrics.dp

<RoundedButton>:
    background_normal: ''
    background_color: 0, 0, 0, 0
    canvas.before:
        Color:
            rgba: self.bg_color if self.state == 'normal' else self.bg_hover
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [self.radius_val]

<MessageLabel>:
    size_hint_y: None
    height: self.texture_size[1] + dp(20)
    text_size: self.width - dp(20), None
    halign: 'left'
    valign: 'middle'
    markup: True
    padding: dp(10), dp(10)
    font_size: sp(14)
"""

Builder.load_string(KV)

# ── Custom widgets ────────────────────────────────────────────────────────────

class RoundedButton(Button):
    def __init__(self, bg_color=None, bg_hover=None, radius_val=8, **kwargs):
        super().__init__(**kwargs)
        self.bg_color    = bg_color    or list(COLORS["accent"])
        self.bg_hover    = bg_hover    or list(COLORS["accent_h"])
        self.radius_val  = dp(radius_val)
        self.color       = COLORS["white"]
        self.bold        = True
        self.font_size   = sp(14)

class MessageLabel(Label):
    pass

def _c(name: str) -> list:
    """Get RGBA color list by name."""
    return list(COLORS[name])

def _time_str() -> str:
    return datetime.now().strftime("%H:%M")

def _strip_html(text: str) -> str:
    """Very basic HTML strip for display."""
    return re.sub(r'<[^>]+>', '', text)

# ── Message bubble widget ─────────────────────────────────────────────────────

class MessageBubble(BoxLayout):
    def __init__(self, role: str, content: str, metadata: dict | None = None, **kwargs):
        super().__init__(orientation='horizontal', **kwargs)
        self.size_hint_y = None
        self.padding     = [dp(8), dp(4)]
        self.spacing     = dp(8)

        is_user  = role == 'user'
        is_error = role == 'error'

        if is_user:
            self.add_widget(Widget())  # spacer left

        if not is_user:
            # Avatar
            avatar = Label(
                text='J', bold=True, font_size=sp(13),
                color=COLORS["white"],
                size_hint=(None, None), size=(dp(28), dp(28)),
            )
            with avatar.canvas.before:
                Color(*COLORS["accent"])
                self._avatar_ellipse = RoundedRectangle(
                    pos=avatar.pos, size=avatar.size, radius=[dp(14)]
                )
            avatar.bind(
                pos  = lambda i, v: setattr(self._avatar_ellipse, 'pos', v),
                size = lambda i, v: setattr(self._avatar_ellipse, 'size', v),
            )
            self.add_widget(avatar)

        # Bubble content
        bubble = BoxLayout(
            orientation='vertical',
            size_hint=(0.78 if not is_user else 0.78, None),
            padding=[dp(10), dp(8)],
            spacing=dp(4),
        )

        bg_color = (
            COLORS["error"] if is_error
            else COLORS["user_bubble"] if is_user
            else COLORS["asst_bubble"]
        )

        with bubble.canvas.before:
            Color(*bg_color)
            self._bubble_rect = RoundedRectangle(
                pos=bubble.pos, size=bubble.size,
                radius=[dp(4 if (is_user or is_error) else 12),
                        dp(12), dp(12),
                        dp(12 if (is_user or is_error) else 4)]
            )
        bubble.bind(
            pos  = lambda i, v: setattr(self._bubble_rect, 'pos', v),
            size = lambda i, v: setattr(self._bubble_rect, 'size', v),
        )

        # Text
        text_label = Label(
            text=content,
            color=COLORS["error"] if is_error else COLORS["text"],
            font_size=sp(14), halign='left', valign='top',
            markup=False, size_hint_y=None,
        )
        text_label.bind(
            width   = lambda i, v: setattr(text_label, 'text_size', (v, None)),
            texture_size = lambda i, v: setattr(text_label, 'height', v[1]),
        )
        bubble.add_widget(text_label)

        # Footer: timestamp + tokens
        ts_text = _time_str()
        if metadata and not is_error:
            tok = metadata.get('input_tokens', 0) + metadata.get('output_tokens', 0)
            if tok:
                ts_text += f"  ·  {tok} tok"
        footer = Label(
            text=ts_text,
            color=COLORS["text_faint"], font_size=sp(10),
            size_hint_y=None, height=dp(16), halign='left',
        )
        footer.bind(width=lambda i, v: setattr(footer, 'text_size', (v, None)))
        bubble.add_widget(footer)

        # Bind bubble height to content
        def _update_height(*_):
            bubble.height = text_label.height + dp(16) + dp(20)
            self.height   = bubble.height + dp(8)
        text_label.bind(height=_update_height)
        bubble.height = dp(60)
        self.height   = dp(68)

        self.add_widget(bubble)

        if is_user:
            pass  # no right spacer needed — bubble is right-aligned by spacer left
        else:
            self.add_widget(Widget())  # spacer right

# ── Thinking indicator ────────────────────────────────────────────────────────

class ThinkingWidget(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation='horizontal', **kwargs)
        self.size_hint_y = None
        self.height      = dp(44)
        self.padding     = [dp(8), dp(4)]
        self.spacing     = dp(8)
        self._anim_event = None

        avatar = Label(
            text='J', bold=True, font_size=sp(13),
            color=COLORS["white"],
            size_hint=(None, None), size=(dp(28), dp(28)),
        )
        with avatar.canvas.before:
            Color(*COLORS["accent"])
            RoundedRectangle(pos=avatar.pos, size=avatar.size, radius=[dp(14)])
        self.add_widget(avatar)

        self._dot_label = Label(
            text='● ● ●', color=COLORS["text_faint"],
            font_size=sp(16), size_hint=(0.7, 1),
        )
        self.add_widget(self._dot_label)
        self.add_widget(Widget())

        self._anim_event = Clock.schedule_interval(self._animate, 0.5)

    def _animate(self, dt):
        # Cycle through dot patterns
        cycle = ['●  ·  ·', '·  ●  ·', '·  ·  ●']
        idx   = int(time.time() * 2) % 3
        self._dot_label.text = cycle[idx]

    def dismiss(self):
        if self._anim_event:
            self._anim_event.cancel()

# ── Chat scroll area ──────────────────────────────────────────────────────────

class ChatScroll(ScrollView):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.do_scroll_x = False
        self.scroll_type = ['bars', 'content']
        self.bar_width   = dp(4)

        self._stack = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            spacing=dp(4),
            padding=[0, dp(8)],
        )
        self._stack.bind(minimum_height=self._stack.setter('height'))
        self.add_widget(self._stack)

        self._thinking: ThinkingWidget | None = None

    def add_message(self, role: str, content: str, metadata: dict | None = None):
        bubble = MessageBubble(role=role, content=content, metadata=metadata)
        if self._thinking:
            self._stack.remove_widget(self._thinking)
            self._thinking.dismiss()
            self._thinking = None
        self._stack.add_widget(bubble)
        Clock.schedule_once(lambda dt: self._scroll_bottom(), 0.1)

    def show_thinking(self):
        if self._thinking:
            return
        self._thinking = ThinkingWidget()
        self._stack.add_widget(self._thinking)
        Clock.schedule_once(lambda dt: self._scroll_bottom(), 0.05)

    def hide_thinking(self):
        if self._thinking:
            self._stack.remove_widget(self._thinking)
            self._thinking.dismiss()
            self._thinking = None

    def clear(self):
        self.hide_thinking()
        self._stack.clear_widgets()

    def _scroll_bottom(self, *_):
        if self.height < self._stack.height:
            self.scroll_y = 0

# ── Settings popup ────────────────────────────────────────────────────────────

class SettingsPopup(Popup):
    def __init__(self, current: dict, on_save: Any, **kwargs):
        super().__init__(
            title='Settings', size_hint=(0.85, 0.7),
            background_color=COLORS["surface"],
            title_color=COLORS["text"],
            separator_color=COLORS["border"],
            **kwargs
        )
        self._on_save = on_save

        root = BoxLayout(orientation='vertical', spacing=dp(12), padding=dp(16))

        def _row(label_text: str, widget) -> BoxLayout:
            row = BoxLayout(orientation='horizontal', size_hint_y=None,
                            height=dp(40), spacing=dp(10))
            row.add_widget(Label(
                text=label_text, color=COLORS["text_muted"],
                font_size=sp(13), size_hint=(0.35, 1), halign='right',
            ))
            row.add_widget(widget)
            return row

        self._ws_input = TextInput(
            text=current.get("ws_url", WS_URL),
            multiline=False, font_size=sp(13),
            background_color=COLORS["surface2"],
            foreground_color=COLORS["text"],
            cursor_color=COLORS["accent"],
            size_hint=(0.65, 1),
        )
        self._http_input = TextInput(
            text=current.get("http_url", HTTP_URL),
            multiline=False, font_size=sp(13),
            background_color=COLORS["surface2"],
            foreground_color=COLORS["text"],
            cursor_color=COLORS["accent"],
            size_hint=(0.65, 1),
        )
        self._pw_input = TextInput(
            text='',
            password=True, multiline=False, font_size=sp(13),
            hint_text='(leave blank to keep current)',
            background_color=COLORS["surface2"],
            foreground_color=COLORS["text"],
            cursor_color=COLORS["accent"],
            size_hint=(0.65, 1),
        )

        root.add_widget(_row("WebSocket URL", self._ws_input))
        root.add_widget(_row("HTTP URL",      self._http_input))
        root.add_widget(_row("Password",      self._pw_input))

        root.add_widget(Widget())  # spacer

        btns = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        cancel_btn = RoundedButton(
            text='Cancel',
            bg_color=list(COLORS["surface2"]),
            bg_hover=list(COLORS["border"]),
        )
        cancel_btn.color = COLORS["text_muted"]
        cancel_btn.bind(on_release=self.dismiss)
        save_btn = RoundedButton(text='Save')
        save_btn.bind(on_release=self._save)
        btns.add_widget(cancel_btn)
        btns.add_widget(save_btn)
        root.add_widget(btns)

        self.content = root

    def _save(self, *_):
        self._on_save({
            "ws_url":   self._ws_input.text.strip(),
            "http_url": self._http_input.text.strip(),
            "password": self._pw_input.text,
        })
        self.dismiss()

# ── Auth screen ───────────────────────────────────────────────────────────────

class AuthScreen(BoxLayout):
    def __init__(self, on_auth: Any, **kwargs):
        super().__init__(orientation='vertical', **kwargs)
        self._on_auth = on_auth

        with self.canvas.before:
            Color(*COLORS["bg"])
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(
            pos  = lambda i, v: setattr(self._bg, 'pos', v),
            size = lambda i, v: setattr(self._bg, 'size', v),
        )

        self.add_widget(Widget())

        card = BoxLayout(
            orientation='vertical',
            size_hint=(None, None), size=(dp(300), dp(280)),
            pos_hint={'center_x': 0.5, 'center_y': 0.5},
            spacing=dp(14), padding=dp(28),
        )
        with card.canvas.before:
            Color(*COLORS["surface"])
            RoundedRectangle(pos=card.pos, size=card.size, radius=[dp(16)])

        card.add_widget(Label(
            text='Juan', font_size=sp(28), bold=True,
            color=COLORS["text"], size_hint_y=None, height=dp(40),
        ))
        card.add_widget(Label(
            text='Autonomous Agent', font_size=sp(13),
            color=COLORS["text_muted"], size_hint_y=None, height=dp(20),
        ))
        card.add_widget(Widget(size_hint_y=None, height=dp(10)))

        self._pw_input = TextInput(
            hint_text='Password (leave blank if none)',
            password=True, multiline=False,
            font_size=sp(14), size_hint_y=None, height=dp(42),
            background_color=COLORS["surface2"],
            foreground_color=COLORS["text"],
            cursor_color=COLORS["accent"],
        )
        self._pw_input.bind(on_text_validate=self._submit)
        card.add_widget(self._pw_input)

        self._err_label = Label(
            text='', font_size=sp(12),
            color=COLORS["error"], size_hint_y=None, height=dp(20),
        )
        card.add_widget(self._err_label)

        connect_btn = RoundedButton(text='Connect', size_hint_y=None, height=dp(42))
        connect_btn.bind(on_release=self._submit)
        card.add_widget(connect_btn)

        self.add_widget(card)
        self.add_widget(Widget())

    def show_error(self, msg: str):
        self._err_label.text = msg

    def _submit(self, *_):
        self._err_label.text = ''
        self._on_auth(self._pw_input.text)

# ── Main chat screen ──────────────────────────────────────────────────────────

class ChatScreen(BoxLayout):
    def __init__(self, on_send: Any, on_new_chat: Any, on_settings: Any, **kwargs):
        super().__init__(orientation='horizontal', **kwargs)

        with self.canvas.before:
            Color(*COLORS["bg"])
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(
            pos  = lambda i, v: setattr(self._bg, 'pos', v),
            size = lambda i, v: setattr(self._bg, 'size', v),
        )

        # ── Sidebar ───────────────────────────────────────────────────────────
        sidebar = BoxLayout(
            orientation='vertical',
            size_hint=(None, 1), width=dp(180),
            padding=dp(12), spacing=dp(8),
        )
        with sidebar.canvas.before:
            Color(*COLORS["surface"])
            Rectangle(pos=sidebar.pos, size=sidebar.size)
            Color(*COLORS["border"])
            Line(points=[sidebar.right, sidebar.y, sidebar.right, sidebar.top], width=1)

        brand = Label(
            text='Juan', font_size=sp(20), bold=True,
            color=COLORS["accent"], size_hint_y=None, height=dp(40),
            halign='left',
        )
        brand.bind(width=lambda i, v: setattr(brand, 'text_size', (v, None)))
        sidebar.add_widget(brand)

        new_btn = RoundedButton(
            text='＋  New chat',
            bg_color=list(COLORS["surface2"]),
            bg_hover=list(COLORS["border"]),
            size_hint_y=None, height=dp(38),
        )
        new_btn.color = COLORS["text"]
        new_btn.bold  = False
        new_btn.bind(on_release=lambda *_: on_new_chat())
        sidebar.add_widget(new_btn)

        sidebar.add_widget(Widget())  # push footer down

        self._status_label = Label(
            text='Connecting…', font_size=sp(11),
            color=COLORS["text_faint"], size_hint_y=None, height=dp(24),
            halign='left',
        )
        self._status_label.bind(
            width=lambda i, v: setattr(self._status_label, 'text_size', (v, None))
        )
        sidebar.add_widget(self._status_label)

        settings_btn = RoundedButton(
            text='⚙  Settings',
            bg_color=list(COLORS["surface2"]),
            bg_hover=list(COLORS["border"]),
            size_hint_y=None, height=dp(36),
        )
        settings_btn.color = COLORS["text_muted"]
        settings_btn.bold  = False
        settings_btn.bind(on_release=lambda *_: on_settings())
        sidebar.add_widget(settings_btn)

        self.add_widget(sidebar)

        # ── Main chat area ────────────────────────────────────────────────────
        main = BoxLayout(orientation='vertical', padding=[dp(16), dp(8)])

        self.chat_scroll = ChatScroll(size_hint=(1, 1))
        main.add_widget(self.chat_scroll)

        # Input row
        input_row = BoxLayout(
            orientation='horizontal',
            size_hint_y=None, height=dp(52),
            spacing=dp(8), padding=[0, dp(4)],
        )

        self._text_input = TextInput(
            hint_text='Message Juan…',
            multiline=False, font_size=sp(14),
            background_color=COLORS["surface"],
            foreground_color=COLORS["text"],
            cursor_color=COLORS["accent"],
            hint_text_color=COLORS["text_faint"],
        )
        self._text_input.bind(on_text_validate=self._send)

        self._send_btn = RoundedButton(
            text='↑', size_hint=(None, 1), width=dp(44),
        )
        self._send_btn.bind(on_release=self._send)
        self._on_send = on_send

        input_row.add_widget(self._text_input)
        input_row.add_widget(self._send_btn)
        main.add_widget(input_row)

        self.add_widget(main)

    def _send(self, *_):
        text = self._text_input.text.strip()
        if not text:
            return
        self._text_input.text = ''
        self._on_send(text)

    def set_status(self, status: str, message: str | None = None):
        labels = {
            'connected':    '● Connected',
            'connecting':   '○ Connecting…',
            'disconnected': '○ ' + (message or 'Disconnected'),
            'error':        '✕ ' + (message or 'Error'),
            'auth':         '⚠ Auth required',
        }
        self._status_label.text  = labels.get(status, status)
        colors = {
            'connected': COLORS["ok"], 'error': COLORS["error"],
            'auth': COLORS["accent"],
        }
        self._status_label.color = colors.get(status, COLORS["text_faint"])

    def set_input_enabled(self, enabled: bool):
        self._text_input.disabled  = not enabled
        self._send_btn.disabled    = not enabled

# ── Root layout ───────────────────────────────────────────────────────────────

class RootLayout(FloatLayout):
    pass

# ── Application ──────────────────────────────────────────────────────────────

class JuanApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.title = APP_TITLE
        self._settings = self._load_settings()
        self._client   = JuanClient(
            ws_url   = self._settings.get("ws_url",   WS_URL),
            http_url = self._settings.get("http_url", HTTP_URL),
        )
        self._client.on_message  = self._on_message
        self._client.on_status   = self._on_status_raw
        self._client.on_thinking = self._on_thinking_raw

        self._auth_screen: AuthScreen | None = None
        self._chat_screen: ChatScreen | None = None

    # ── Kivy lifecycle ────────────────────────────────────────────────────────

    def build(self):
        Window.clearcolor = COLORS["bg"]
        Window.minimum_width  = dp(480)
        Window.minimum_height = dp(360)

        self._root = RootLayout()
        self._show_auth()
        return self._root

    def on_stop(self):
        self._client.disconnect()

    # ── Screen management ─────────────────────────────────────────────────────

    def _show_auth(self):
        self._root.clear_widgets()
        self._auth_screen = AuthScreen(on_auth=self._do_auth)
        self._root.add_widget(self._auth_screen)

    def _show_chat(self):
        self._root.clear_widgets()
        self._chat_screen = ChatScreen(
            on_send     = self._send_message,
            on_new_chat = self._new_chat,
            on_settings = self._open_settings,
        )
        self._root.add_widget(self._chat_screen)
        self._client.connect()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _do_auth(self, password: str):
        def _thread():
            ok, err = self._client.authenticate(password)
            if ok:
                Clock.schedule_once(lambda dt: self._show_chat())
            else:
                Clock.schedule_once(
                    lambda dt: self._auth_screen and self._auth_screen.show_error(err)
                )
        threading.Thread(target=_thread, daemon=True).start()

    # ── Messaging ─────────────────────────────────────────────────────────────

    def _send_message(self, text: str):
        if not self._chat_screen:
            return
        # Optimistic user bubble
        self._chat_screen.chat_scroll.add_message('user', text)
        self._chat_screen.chat_scroll.show_thinking()
        self._chat_screen.set_input_enabled(False)

        def _thread():
            err = self._client.send(text)
            if err:
                Clock.schedule_once(lambda dt: self._on_send_error(err))

        threading.Thread(target=_thread, daemon=True).start()

    def _on_send_error(self, err: str):
        if self._chat_screen:
            self._chat_screen.chat_scroll.add_message('error', err)
            self._chat_screen.set_input_enabled(True)

    # ── Callbacks from client (network thread → schedule on UI thread) ────────

    def _on_message(self, data: dict):
        def _ui(dt):
            if self._chat_screen:
                self._chat_screen.chat_scroll.add_message(
                    data.get('role', 'assistant'),
                    data.get('content', ''),
                    data.get('metadata'),
                )
                self._chat_screen.set_input_enabled(True)
        Clock.schedule_once(_ui)

    def _on_status_raw(self, status: str, message: str | None):
        def _ui(dt):
            if status == 'auth':
                self._show_auth()
                if self._auth_screen and message:
                    self._auth_screen.show_error(message)
            elif self._chat_screen:
                self._chat_screen.set_status(status, message)
        Clock.schedule_once(_ui)

    def _on_thinking_raw(self, thinking: bool):
        def _ui(dt):
            if self._chat_screen:
                if thinking:
                    self._chat_screen.chat_scroll.show_thinking()
                else:
                    self._chat_screen.chat_scroll.hide_thinking()
                    self._chat_screen.set_input_enabled(True)
        Clock.schedule_once(_ui)

    # ── Session / Settings ────────────────────────────────────────────────────

    def _new_chat(self):
        if self._chat_screen:
            self._chat_screen.chat_scroll.clear()
        self._client.new_session()

    def _open_settings(self):
        popup = SettingsPopup(
            current=self._settings,
            on_save=self._save_settings,
        )
        popup.open()

    def _save_settings(self, data: dict):
        self._settings.update(data)
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(self._settings, f)
        except Exception:
            pass
        # Reconnect with new settings
        self._client.disconnect()
        self._client = JuanClient(
            ws_url   = data.get("ws_url",   WS_URL),
            http_url = data.get("http_url", HTTP_URL),
        )
        self._client.on_message  = self._on_message
        self._client.on_status   = self._on_status_raw
        self._client.on_thinking = self._on_thinking_raw
        if data.get("password"):
            def _reauth():
                ok, err = self._client.authenticate(data["password"])
                if ok:
                    Clock.schedule_once(lambda dt: self._client.connect())
                else:
                    Clock.schedule_once(lambda dt: self._show_auth())
            threading.Thread(target=_reauth, daemon=True).start()
        else:
            self._client.connect()

    def _load_settings(self) -> dict:
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f)
        except Exception:
            return {"ws_url": WS_URL, "http_url": HTTP_URL}


def main():
    JuanApp().run()


if __name__ == '__main__':
    main()
