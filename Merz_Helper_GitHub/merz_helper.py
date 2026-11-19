import ctypes
from ctypes import wintypes
import json
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import pyperclip
from pynput import keyboard

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "speech": "Добрый день! Отправляю информацию.",
    "file_path": "",
    "binds": {
        "send_phone_next": "<f1>",
        "send_speech": "<f2>",
        "send_file": "<f3>",
        "next_phone": "<f4>",
        "prev_phone": "<f5>",
    },
}

ACTION_LABELS = {
    "send_phone_next": "Номер + Enter + далее",
    "send_speech": "Спич + Enter",
    "send_file": "Файл + Enter",
    "next_phone": "Следующий номер",
    "prev_phone": "Предыдущий номер",
}

CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040


class DROPFILES(ctypes.Structure):
    _fields_ = [
        ("pFiles", wintypes.DWORD),
        ("pt", wintypes.POINT),
        ("fNC", wintypes.BOOL),
        ("fWide", wintypes.BOOL),
    ]


def copy_file_to_clipboard_windows(filepath: str) -> bool:
    if os.name != "nt" or not filepath or not os.path.isfile(filepath):
        return False

    abs_path = os.path.abspath(filepath)
    paths_bytes = (abs_path + "\0").encode("utf-16le") + b"\0\0"
    header_size = ctypes.sizeof(DROPFILES)
    total_size = header_size + len(paths_bytes)

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL

    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, total_size)
    if not handle:
        return False

    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        kernel32.GlobalFree(handle)
        return False

    try:
        header = DROPFILES()
        header.pFiles = header_size
        header.pt = wintypes.POINT(0, 0)
        header.fNC = False
        header.fWide = True
        ctypes.memmove(ptr, ctypes.byref(header), header_size)
        ctypes.memmove(ptr + header_size, paths_bytes, len(paths_bytes))
    finally:
        kernel32.GlobalUnlock(handle)

    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        return False

    transferred = False
    try:
        if not user32.EmptyClipboard():
            return False
        if user32.SetClipboardData(CF_HDROP, handle):
            transferred = True
            return True
        return False
    finally:
        user32.CloseClipboard()
        if not transferred:
            kernel32.GlobalFree(handle)


def key_to_token(key):
    if isinstance(key, keyboard.KeyCode):
        if key.char:
            ch = key.char.lower()
            if 1 <= ord(ch) <= 26:
                return chr(ord("a") + ord(ch) - 1)
            return ch
        if key.vk:
            return f"<{key.vk}>"
        return None

    if isinstance(key, keyboard.Key):
        name = key.name.lower()
        mapping = {
            "ctrl_l": "<ctrl>", "ctrl_r": "<ctrl>",
            "shift_l": "<shift>", "shift_r": "<shift>",
            "alt_l": "<alt>", "alt_r": "<alt>",
            "alt_gr": "<alt_gr>", "cmd": "<cmd>",
        }
        return mapping.get(name, f"<{name}>")
    return None


def display_hotkey(value):
    if not value:
        return "Не задан"
    return " + ".join(part.strip("<>").upper() for part in value.split("+"))


class BindHelperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Merz Helper")
        self.root.geometry("560x720")
        self.root.configure(bg="#120d1d")
        self.root.resizable(False, False)

        self.config = self.load_config()
        self.work_mode = True
        self.phones = []
        self.index = 0
        self.sent_count = 0
        self.capturing_action = None
        self.pressed_tokens = set()
        self.hotkey_listener = None
        self.capture_listener = None
        self.bind_buttons = {}

        self.build_ui()
        self.load_values_into_ui()
        self.update_mode_ui()
        self.start_hotkey_listener()
        self.start_capture_listener()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def load_config(self):
        if os.path.isfile(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as stream:
                    data = json.load(stream)
                merged = json.loads(json.dumps(DEFAULT_CONFIG))
                merged["speech"] = data.get("speech", merged["speech"])
                merged["file_path"] = data.get("file_path", merged["file_path"])
                if isinstance(data.get("binds"), dict):
                    merged["binds"].update(data["binds"])
                return merged
            except Exception:
                pass
        return json.loads(json.dumps(DEFAULT_CONFIG))

    def save_config(self):
        self.config["speech"] = self.speech_text.get("1.0", "end").strip()
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as stream:
                json.dump(self.config, stream, ensure_ascii=False, indent=2)
        except Exception as exc:
            messagebox.showerror("Ошибка сохранения", str(exc))

    def build_ui(self):
        bg, card = "#120d1d", "#1c1430"
        accent, hover, border = "#7c3aed", "#9333ea", "#3b2866"
        fg, muted = "#f5f3ff", "#a78bfa"

        top = tk.Frame(self.root, bg="#181028", padx=16, pady=12)
        top.pack(fill="x")
        tk.Label(top, text="MERZ HELPER", font=("Segoe UI", 16, "bold"),
                 fg="#c084fc", bg="#181028").pack(side="left")
        self.status_lbl = tk.Label(top, text="Готов", font=("Segoe UI", 10),
                                   fg="#4ade80", bg="#181028")
        self.status_lbl.pack(side="right")

        mode_bar = tk.Frame(self.root, bg=bg, padx=14, pady=7)
        mode_bar.pack(fill="x")
        tk.Label(mode_bar, text="РЕЖИМ БИНДОВ:", font=("Segoe UI", 9, "bold"),
                 fg=muted, bg=bg).pack(side="left", padx=(0, 10))
        self.work_btn = self.make_button(mode_bar, "WORK", self.enable_work,
                                         "#166534", fg, "#15803d")
        self.work_btn.pack(side="left", padx=(0, 6))
        self.nowork_btn = self.make_button(mode_bar, "NOWORK", self.disable_work,
                                           "#2a1e47", fg, "#3b2866")
        self.nowork_btn.pack(side="left")

        db = tk.Frame(self.root, bg=card, highlightbackground=border,
                      highlightthickness=1, padx=14, pady=12)
        db.pack(fill="x", padx=14, pady=(12, 8))
        dbtop = tk.Frame(db, bg=card)
        dbtop.pack(fill="x")
        self.counter_lbl = tk.Label(dbtop, text="ЗАПИСЬ 0 / 0",
                                    font=("Segoe UI", 10, "bold"), fg=muted, bg=card)
        self.counter_lbl.pack(side="left")
        self.session_lbl = tk.Label(dbtop, text="Отправлено: 0",
                                    font=("Segoe UI", 10, "bold"), fg="#c084fc", bg=card)
        self.session_lbl.pack(side="right")
        self.phone_lbl = tk.Label(db, text="Загрузите базу (.txt)",
                                  font=("Segoe UI", 18, "bold"), fg=fg, bg=card, pady=8)
        self.phone_lbl.pack(fill="x")
        buttons = tk.Frame(db, bg=card)
        buttons.pack(fill="x")
        self.make_button(buttons, "Загрузить базу (.txt)", self.load_phones_file,
                         accent, fg, hover).pack(side="left")
        self.make_button(buttons, "◀ Назад", self.prev_phone, "#2a1e47", fg,
                         "#3b2866").pack(side="right", padx=(6, 0))
        self.make_button(buttons, "Далее ▶", self.next_phone, "#2a1e47", fg,
                         "#3b2866").pack(side="right")

        content = tk.Frame(self.root, bg=card, highlightbackground=border,
                           highlightthickness=1, padx=14, pady=10)
        content.pack(fill="x", padx=14, pady=6)
        tk.Label(content, text="СПИЧ (ТЕКСТ)", font=("Segoe UI", 9, "bold"),
                 fg=muted, bg=card).pack(anchor="w")
        self.speech_text = tk.Text(content, height=3, bg=bg, fg=fg,
                                   insertbackground=fg, relief="flat",
                                   font=("Segoe UI", 10), padx=8, pady=6, wrap="word")
        self.speech_text.pack(fill="x", pady=(4, 5))
        self.speech_text.bind("<FocusOut>", lambda _event: self.save_config())
        self.make_button(content, "Сохранить спич", self.save_config,
                         "#2a1e47", fg, "#3b2866").pack(anchor="e", pady=(0, 8))

        file_row = tk.Frame(content, bg=card)
        file_row.pack(fill="x")
        self.file_lbl = tk.Label(file_row, text="Файл не выбран",
                                 font=("Segoe UI", 9), fg="#d8b4fe",
                                 bg=card, anchor="w")
        self.file_lbl.pack(side="left", fill="x", expand=True)
        self.make_button(file_row, "Выбрать файл", self.choose_file,
                         "#2a1e47", fg, "#3b2866").pack(side="right")

        binds = tk.Frame(self.root, bg=card, highlightbackground=border,
                         highlightthickness=1, padx=14, pady=10)
        binds.pack(fill="both", expand=True, padx=14, pady=(6, 14))
        tk.Label(binds, text="НАСТРОЙКА БИНДОВ — нажмите «Изменить»",
                 font=("Segoe UI", 9, "bold"), fg=muted, bg=card).pack(anchor="w", pady=(0, 6))

        for action, title in ACTION_LABELS.items():
            row = tk.Frame(binds, bg="#140e22", padx=10, pady=6)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=title, font=("Segoe UI", 9, "bold"),
                     fg=fg, bg="#140e22", width=24, anchor="w").pack(side="left")
            btn = self.make_button(row, "Изменить",
                                   lambda key=action: self.start_capture(key),
                                   accent, fg, hover)
            btn.pack(side="right")
            self.bind_buttons[action] = btn
            label = tk.Label(row,
                             text=display_hotkey(self.config["binds"].get(action, "")),
                             font=("Consolas", 9, "bold"), fg="#38bdf8",
                             bg="#140e22", padx=10)
            label.pack(side="right")
            setattr(self, f"key_lbl_{action}", label)

    @staticmethod
    def make_button(parent, text, command, bg, fg, active):
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                         activebackground=active, activeforeground=fg,
                         relief="flat", font=("Segoe UI", 9, "bold"),
                         padx=10, pady=5, cursor="hand2")

    def load_values_into_ui(self):
        self.speech_text.insert("1.0", self.config.get("speech", ""))
        self.update_file_label()
        self.refresh_phone_ui()
        self.refresh_bind_labels()

    def update_file_label(self):
        path = self.config.get("file_path", "")
        self.file_lbl.config(text=f"Файл: {os.path.basename(path)}"
                             if path and os.path.isfile(path) else "Файл не выбран")

    def refresh_bind_labels(self):
        for action in ACTION_LABELS:
            label = getattr(self, f"key_lbl_{action}")
            label.config(text=display_hotkey(self.config["binds"].get(action, "")),
                         fg="#38bdf8")

    def refresh_phone_ui(self):
        total = len(self.phones)
        if not total:
            self.counter_lbl.config(text="ЗАПИСЬ 0 / 0")
            self.phone_lbl.config(text="База не загружена")
        else:
            self.counter_lbl.config(text=f"ЗАПИСЬ {self.index + 1} / {total}")
            self.phone_lbl.config(text=self.phones[self.index])
        self.session_lbl.config(text=f"Отправлено: {self.sent_count}")

    def enable_work(self):
        self.work_mode = True
        self.update_mode_ui()
        self.set_status("WORK: бинды включены", "#4ade80")

    def disable_work(self):
        self.work_mode = False
        self.update_mode_ui()
        self.set_status("NOWORK: бинды отключены, можно печатать", "#facc15")

    def update_mode_ui(self):
        if self.work_mode:
            self.work_btn.config(bg="#166534", relief="sunken")
            self.nowork_btn.config(bg="#2a1e47", relief="flat")
        else:
            self.work_btn.config(bg="#2a1e47", relief="flat")
            self.nowork_btn.config(bg="#9a3412", relief="sunken")

    def set_status(self, text, color="#4ade80"):
        self.root.after(0, lambda: self.status_lbl.config(text=text, fg=color))

    def load_phones_file(self):
        path = filedialog.askopenfilename(
            title="Выберите файл базы номеров",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig", errors="ignore") as stream:
                self.phones = [line.strip() for line in stream if line.strip()]
            self.index = 0
            self.refresh_phone_ui()
            self.set_status(f"Загружено: {len(self.phones)}")
        except Exception as exc:
            messagebox.showerror("Ошибка чтения базы", str(exc))

    def choose_file(self):
        path = filedialog.askopenfilename(title="Выберите файл для отправки")
        if not path:
            return
        self.config["file_path"] = path
        self.save_config()
        self.update_file_label()
        self.set_status("Файл выбран")

    def next_phone(self):
        if self.phones:
            self.index = (self.index + 1) % len(self.phones)
            self.refresh_phone_ui()

    def prev_phone(self):
        if self.phones:
            self.index = (self.index - 1) % len(self.phones)
            self.refresh_phone_ui()

    def start_capture(self, action):
        self.capturing_action = action
        self.pressed_tokens.clear()
        getattr(self, f"key_lbl_{action}").config(text="Нажмите клавишу...", fg="#facc15")
        self.set_status("Ожидание клавиши...", "#facc15")

    def start_capture_listener(self):
        def on_press(key):
            if not self.capturing_action:
                return
            token = key_to_token(key)
            if token:
                self.pressed_tokens.add(token)

        def on_release(key):
            if not self.capturing_action or not self.pressed_tokens:
                return
            modifiers = sorted(t for t in self.pressed_tokens
                               if t in ("<ctrl>", "<alt>", "<shift>", "<cmd>"))
            others = sorted(t for t in self.pressed_tokens if t not in modifiers)
            combo = "+".join(modifiers + others)
            action = self.capturing_action
            self.capturing_action = None
            self.pressed_tokens.clear()
            self.root.after(0, lambda: self.apply_new_bind(action, combo))

        self.capture_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.capture_listener.daemon = True
        self.capture_listener.start()

    def apply_new_bind(self, action, combo):
        self.config["binds"][action] = combo
        self.save_config()
        self.refresh_bind_labels()
        self.restart_hotkey_listener()
        self.set_status(f"Бинд сохранён: {display_hotkey(combo)}")

    def restart_hotkey_listener(self):
        if self.hotkey_listener:
            self.hotkey_listener.stop()
            self.hotkey_listener = None
        self.start_hotkey_listener()

    def start_hotkey_listener(self):
        binds = self.config.get("binds", {})
        actions = {
            "send_phone_next": self.action_send_phone_next,
            "send_speech": self.action_send_speech,
            "send_file": self.action_send_file,
            "next_phone": self.next_phone,
            "prev_phone": self.prev_phone,
        }
        mapping = {}
        for key, callback in actions.items():
            hotkey = binds.get(key)
            if hotkey:
                if hotkey in mapping:
                    self.set_status(f"Повторяется бинд: {display_hotkey(hotkey)}", "#f87171")
                    continue
                mapping[hotkey] = lambda fn=callback: self.root.after(0, lambda: fn() if self.work_mode else None)
        if mapping:
            try:
                self.hotkey_listener = keyboard.GlobalHotKeys(mapping)
                self.hotkey_listener.daemon = True
                self.hotkey_listener.start()
            except Exception as exc:
                self.set_status(f"Ошибка бинда: {exc}", "#f87171")

    def paste_and_enter(self):
        """Paste via the Windows clipboard, allowing web clients time to process it.

        MAX Web uses a browser contenteditable field. A very short gap between
        Ctrl+V and Enter can cause Enter to be handled before the paste event
        has updated the message composer, so use a more forgiving delay.
        """
        def worker():
            controller = keyboard.Controller()
            # Give the user/browser time to finish focusing the message field.
            time.sleep(0.35)
            try:
                with controller.pressed(keyboard.Key.ctrl_l):
                    controller.press("v")
                    controller.release("v")
                # Web apps such as MAX process paste asynchronously.
                time.sleep(0.65)
                controller.press(keyboard.Key.enter)
                controller.release(keyboard.Key.enter)
            except Exception as exc:
                self.set_status(f"Не удалось вставить/отправить: {exc}", "#f87171")
        threading.Thread(target=worker, daemon=True).start()

    def action_send_phone_next(self):
        if self.capturing_action:
            return
        if not self.phones:
            self.set_status("Нет номеров в базе", "#f87171")
            return
        phone = self.phones[self.index]
        pyperclip.copy(phone)
        self.paste_and_enter()
        self.sent_count += 1
        self.next_phone()
        self.set_status(f"Отправлен номер: {phone}")

    def action_send_speech(self):
        if self.capturing_action:
            return
        text = self.speech_text.get("1.0", "end").strip()
        if not text:
            self.set_status("Спич пустой", "#f87171")
            return
        pyperclip.copy(text)
        self.paste_and_enter()
        self.set_status("Спич отправлен")

    def action_send_file(self):
        if self.capturing_action:
            return
        path = self.config.get("file_path", "")
        if not copy_file_to_clipboard_windows(path):
            self.set_status("Файл не выбран, не найден или недоступен", "#f87171")
            return
        self.paste_and_enter()
        self.set_status(f"Файл отправлен: {os.path.basename(path)}")

    def on_close(self):
        self.save_config()
        for listener in (self.hotkey_listener, self.capture_listener):
            if listener:
                try:
                    listener.stop()
                except Exception:
                    pass
        self.root.destroy()


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("Эта программа предназначена для Windows.")
    root = tk.Tk()
    app = BindHelperApp(root)
    root.mainloop()
