#!/usr/bin/env python3
"""日本語Wikipedia SQLite記事ビューア。

既存のSQLiteを --db で受け取り、title指定で記事を表示します。
同じSQLiteに対する2回目以降の起動要求は既存ウィンドウへ送り、
記事を新しいタブで開きます。Python標準ライブラリのみを使用します。
"""

from __future__ import annotations

import argparse
import json
import queue
import socket
import sqlite3
import sys
import threading
import time
import tkinter as tk
import zlib
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

APP_TITLE = "Wikipedia JSONL Viewer"


def get_article(db_path: Path, title: str) -> sqlite3.Row | None:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM article WHERE title = ?", (title.strip(),)
        ).fetchone()
        if row is None:
            row = connection.execute(
                "SELECT * FROM article WHERE title = ? COLLATE NOCASE",
                (title.strip(),),
            ).fetchone()
        return row
    finally:
        connection.close()


def suggest_titles(db_path: Path, query: str, limit: int = 30) -> list[str]:
    query = query.strip()
    if not query:
        return []
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT title FROM article
            WHERE title LIKE ? ESCAPE '\\'
            ORDER BY CASE WHEN title LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END,
                     length(title), title
            LIMIT ?
            """,
            (
                f"%{escape_like(query)}%",
                f"{escape_like(query)}%",
                limit,
            ),
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        connection.close()


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def format_article(row: sqlite3.Row) -> str:
    metadata = []
    if row["page_id"]:
        metadata.append(f"page_id: {row['page_id']}")
    if row["timestamp"]:
        metadata.append(f"更新日時: {row['timestamp']}")
    if row["url"]:
        metadata.append(row["url"])
    header = row["title"]
    if metadata:
        header += "\n" + "  |  ".join(metadata)
    return f"{header}\n{'=' * 72}\n\n{row['text']}"


class ClosableNotebook(ttk.Notebook):
    """各タブの右端に閉じるボタンを持つNotebook。"""

    _style_initialized = False

    def __init__(self, master=None, **kwargs):
        self._initialize_style(master)
        super().__init__(master, style="ClosableNotebook", **kwargs)
        self._active_close_index: int | None = None
        self.bind("<ButtonPress-1>", self._on_close_press, add=True)
        self.bind("<ButtonRelease-1>", self._on_close_release, add=True)

    @classmethod
    def _initialize_style(cls, master) -> None:
        if cls._style_initialized:
            return

        style = ttk.Style(master)
        normal = tk.PhotoImage(master=master, width=14, height=14)
        active = tk.PhotoImage(master=master, width=14, height=14)
        pressed = tk.PhotoImage(master=master, width=14, height=14)

        def draw_x(image: tk.PhotoImage, color: str) -> None:
            for i in range(4, 10):
                image.put(color, (i, i))
                image.put(color, (i, 13 - i))
                if i + 1 < 14:
                    image.put(color, (i + 1, i))
                    image.put(color, (i + 1, 13 - i))

        draw_x(normal, "#666666")
        draw_x(active, "#202020")
        draw_x(pressed, "#000000")

        style.element_create(
            "close",
            "image",
            normal,
            ("active", active),
            ("pressed", pressed),
            border=0,
            sticky="",
        )
        style.layout(
            "ClosableNotebook",
            [("ClosableNotebook.client", {"sticky": "nswe"})],
        )
        style.layout(
            "ClosableNotebook.Tab",
            [
                (
                    "ClosableNotebook.tab",
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                "ClosableNotebook.padding",
                                {
                                    "side": "top",
                                    "sticky": "nswe",
                                    "children": [
                                        (
                                            "ClosableNotebook.focus",
                                            {
                                                "side": "top",
                                                "sticky": "nswe",
                                                "children": [
                                                    (
                                                        "ClosableNotebook.label",
                                                        {"side": "left", "sticky": ""},
                                                    ),
                                                    (
                                                        "ClosableNotebook.close",
                                                        {"side": "left", "sticky": ""},
                                                    ),
                                                ],
                                            },
                                        )
                                    ],
                                },
                            )
                        ],
                    },
                )
            ],
        )

        cls._images = (normal, active, pressed)
        cls._style_initialized = True

    def _on_close_press(self, event) -> str | None:
        element = self.identify(event.x, event.y)
        if "close" not in element:
            return None
        try:
            index = self.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return None
        self.state(["pressed"])
        self._active_close_index = index
        return "break"

    def _on_close_release(self, event) -> str | None:
        if not self.instate(["pressed"]):
            return None
        self.state(["!pressed"])
        element = self.identify(event.x, event.y)
        try:
            index = self.index(f"@{event.x},{event.y}")
        except tk.TclError:
            index = None

        if (
            "close" in element
            and index is not None
            and index == self._active_close_index
        ):
            tab_id = self.tabs()[index]
            tab_widget = self.nametowidget(tab_id)
            self.forget(index)
            tab_widget.destroy()
            self.event_generate("<<NotebookTabClosed>>")

        self._active_close_index = None
        return "break"


class ViewerApp(tk.Tk):
    def __init__(
        self,
        db_path: Path,
        initial_title: str = "",
        ipc_socket: socket.socket | None = None,
    ) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1100x760")
        self.minsize(760, 520)
        self.db_path = db_path
        self._search_job = None
        self._ipc_socket = ipc_socket
        self._ipc_queue: queue.Queue[dict] = queue.Queue()
        self._tab_count = 0

        self.last_search_term = ""
        self.last_search_index = "1.0"

        self.bind("<Control-f>", self.on_search)
        self.bind("<F3>", self.find_next)
        self.bind("<Shift-F3>", self.find_previous)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(self, padding=8)
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(1, weight=1)

        ttk.Label(toolbar, text="タイトル").grid(row=0, column=0, padx=(0, 6))
        self.title_var = tk.StringVar(value=initial_title)
        self.title_entry = ttk.Entry(toolbar, textvariable=self.title_var)
        self.title_entry.grid(row=0, column=1, sticky="ew")
        self.title_entry.bind("<Return>", lambda _event: self.open_exact(new_tab=True))
        self.title_entry.bind("<KeyRelease>", self.schedule_suggestions)

        ttk.Button(
            toolbar,
            text="新しいタブで開く",
            command=lambda: self.open_exact(new_tab=True),
        ).grid(row=0, column=2, padx=(6, 0))

        self.status_var = tk.StringVar(value=f"SQLite: {self.db_path}")
        ttk.Label(self, textvariable=self.status_var, padding=(8, 0, 8, 6)).grid(
            row=1, column=0, sticky="ew"
        )

        pane = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        pane.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))

        left = ttk.Frame(pane)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.results = tk.Listbox(left, exportselection=False, width=30)
        self.results.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.results.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.results.configure(yscrollcommand=scrollbar.set)
        self.results.bind("<Double-Button-1>", lambda _event: self.open_selected())
        self.results.bind("<Return>", lambda _event: self.open_selected())
        pane.add(left, weight=1)

        self.notebook = ClosableNotebook(pane)
        pane.add(self.notebook, weight=4)
        self.notebook.bind("<<NotebookTabClosed>>", self._on_tab_closed)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.title_entry.focus_set()

        self.after(0, self._raise_once)

        if self._ipc_socket is not None:
            threading.Thread(target=self._ipc_server, daemon=True).start()
            self.after(100, self._process_ipc_queue)

        if initial_title:
            self.after(50, lambda: self.open_title(initial_title, new_tab=True))

    def _raise_once(self) -> None:
        """Bring the window to the foreground once, then restore normal z-order."""
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(150, lambda: self.attributes("-topmost", False))

    def _on_tab_closed(self, _event=None) -> None:
        if not self.notebook.tabs():
            self.close_app()
            return
        self._on_tab_changed()

    def _on_tab_changed(self, _event=None) -> None:
        selected = self.notebook.select()
        if not selected:
            self.title(APP_TITLE)
            return
        frame = self.nametowidget(selected)
        article_title = getattr(
            frame, "article_title", self.notebook.tab(selected, "text")
        )
        self.status_var.set(f"表示中: {article_title}")
        self.title(f"{article_title} — {APP_TITLE}")

    def close_app(self) -> None:
        if self._ipc_socket is not None:
            try:
                self._ipc_socket.close()
            except OSError:
                pass
        self.destroy()

    def _ipc_server(self) -> None:
        assert self._ipc_socket is not None
        while True:
            try:
                connection, _address = self._ipc_socket.accept()
            except OSError:
                return
            with connection:
                try:
                    chunks = []
                    while True:
                        data = connection.recv(65536)
                        if not data:
                            break
                        chunks.append(data)
                        if sum(map(len, chunks)) > 1_000_000:
                            raise ValueError("IPCメッセージが大きすぎます。")
                    request = json.loads(b"".join(chunks).decode("utf-8"))
                    self._ipc_queue.put(request)
                    connection.sendall(b'{"ok":true}')
                except Exception as exc:
                    try:
                        response = json.dumps(
                            {"ok": False, "error": str(exc)}, ensure_ascii=False
                        ).encode("utf-8")
                        connection.sendall(response)
                    except OSError:
                        pass

    def _process_ipc_queue(self) -> None:
        while True:
            try:
                request = self._ipc_queue.get_nowait()
            except queue.Empty:
                break
            title = str(request.get("title", "")).strip()
            if title:
                self.open_title(title, new_tab=bool(request.get("new_tab", True)))
                self.deiconify()
                self._raise_once()
        self.after(100, self._process_ipc_queue)

    def schedule_suggestions(self, _event=None) -> None:
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(220, self.refresh_suggestions)

    def refresh_suggestions(self) -> None:
        self._search_job = None
        self.results.delete(0, tk.END)
        query = self.title_var.get().strip()
        if not query:
            return
        try:
            titles = suggest_titles(self.db_path, query)
        except sqlite3.Error as exc:
            self.status_var.set(f"SQLiteエラー: {exc}")
            return
        for title in titles:
            self.results.insert(tk.END, title)

    def open_selected(self) -> None:
        selection = self.results.curselection()
        if not selection:
            return
        title = self.results.get(selection[0])
        self.title_var.set(title)
        self.open_title(title, new_tab=True)

    def open_exact(self, *, new_tab: bool = True) -> None:
        title = self.title_var.get().strip()
        if title:
            self.open_title(title, new_tab=new_tab)

    def open_title(self, title: str, *, new_tab: bool = True) -> None:
        try:
            row = get_article(self.db_path, title)
        except sqlite3.Error as exc:
            messagebox.showerror(APP_TITLE, f"SQLiteエラー: {exc}")
            return
        if row is None:
            self.title_var.set(title)
            self.status_var.set(f"完全一致なし: {title}")
            self.refresh_suggestions()
            return

        article_title = str(row["title"])
        self.title_var.set(article_title)

        if not new_tab and self.notebook.tabs():
            tab_id = self.notebook.select()
            frame = self.nametowidget(tab_id)
            text = frame.article_text
            text.delete("1.0", tk.END)
            text.insert("1.0", format_article(row))
            self.notebook.tab(tab_id, text=article_title)
            frame.article_title = article_title
        else:
            self._tab_count += 1
            frame = ttk.Frame(self.notebook)
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            text = ScrolledText(
                frame,
                wrap=tk.WORD,
                font=("Yu Gothic UI", 11),
                padx=14,
                pady=12,
                undo=False,
            )
            text.grid(row=0, column=0, sticky="nsew")
            frame.article_text = text
            frame.article_title = article_title
            self.notebook.add(frame, text=article_title)
            self.notebook.select(frame)
            text.insert("1.0", format_article(row))

        text.mark_set(tk.INSERT, "1.0")
        text.see("1.0")
        self.status_var.set(f"表示中: {article_title}")
        self.title(f"{article_title} — {APP_TITLE}")

    # find in text function

    def on_search(self, event=None):
        selected_tab = self.notebook.select()
        if not selected_tab:
            return
        frame = self.nametowidget(selected_tab)
        text = frame.article_text

        search_window = tk.Toplevel(self)
        search_window.title("検索")
        search_window.geometry("300x100")
        search_window.grab_set()
        search_window.focus_set()

        search_var = tk.StringVar()
        entry = ttk.Entry(search_window, textvariable=search_var)
        entry.pack(pady=10, fill=tk.X)
        entry.focus_set()

        def do_search():
            term = search_var.get().strip()
            if not term:
                return
            idx = self.find_text(text, term)
            if idx:
                self.last_search_term = term
                self.last_search_index = idx
            search_window.destroy()
            self.focus_set()

        entry.bind("<Return>", lambda event: do_search())
        entry.bind("<Escape>", lambda event: search_window.destroy())
        search_window.bind("<Escape>", lambda event: search_window.destroy())

        ttk.Button(search_window, text="検索", command=do_search).pack()

    def find_text(self, text_widget, term):
        text_widget.tag_remove("search_highlight", "1.0", tk.END)
        start = "1.0"
        idx = text_widget.search(term, start, stopindex=tk.END, nocase=True)
        if idx:
            end_idx = f"{idx}+{len(term)}c"
            text_widget.tag_add("search_highlight", idx, end_idx)
            text_widget.tag_config(
                "search_highlight", background="#FFD54F", foreground="black"
            )
            text_widget.see(idx)
            text_widget.mark_set(tk.INSERT, idx)
            text_widget.focus_set()
            return idx
        else:
            messagebox.showinfo("検索", "見つかりません。")
            return None

    def _show_search_match(self, text, idx):
        text.tag_remove("search_highlight", "1.0", tk.END)
        end_idx = text.index(f"{idx}+{len(self.last_search_term)}c")
        text.tag_add("search_highlight", idx, end_idx)
        text.tag_config("search_highlight", background="#FFD54F", foreground="black")
        text.see(idx)
        text.mark_set(tk.INSERT, idx)
        text.focus_set()
        self.last_search_index = text.index(idx)

    def find_next(self, event=None):
        selected_tab = self.notebook.select()
        if not selected_tab or not self.last_search_term:
            return "break"
        frame = self.nametowidget(selected_tab)
        text = frame.article_text

        start = text.index(f"{self.last_search_index}+{len(self.last_search_term)}c")
        idx = text.search(self.last_search_term, start, stopindex=tk.END, nocase=True)
        if not idx:
            idx = text.search(self.last_search_term, "1.0", stopindex=start, nocase=True)

        if idx:
            self._show_search_match(text, idx)
        else:
            messagebox.showinfo("検索", "見つかりません。")
        return "break"

    def find_previous(self, event=None):
        selected_tab = self.notebook.select()
        if not selected_tab or not self.last_search_term:
            return "break"
        frame = self.nametowidget(selected_tab)
        text = frame.article_text

        start = text.index(self.last_search_index)
        idx = text.search(
            self.last_search_term,
            start,
            stopindex="1.0",
            backwards=True,
            nocase=True,
        )
        if not idx:
            idx = text.search(
                self.last_search_term,
                tk.END,
                stopindex=start,
                backwards=True,
                nocase=True,
            )

        if idx:
            self._show_search_match(text, idx)
        else:
            messagebox.showinfo("検索", "見つかりません。")
        return "break"

    def reset_search(self):
        selected_tab = self.notebook.select()
        if not selected_tab:
            return
        frame = self.nametowidget(selected_tab)
        text = frame.article_text
        text.tag_remove("search_highlight", "1.0", tk.END)


def ipc_port_for(db_path: Path) -> int:
    normalized = str(db_path.resolve()).casefold().encode("utf-8")
    return 49152 + (zlib.crc32(normalized) % 15000)


def send_to_existing_instance(db_path: Path, title: str) -> bool:
    if not title.strip():
        return False
    request = json.dumps(
        {"title": title.strip(), "new_tab": True}, ensure_ascii=False
    ).encode("utf-8")
    port = ipc_port_for(db_path)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5) as connection:
            connection.sendall(request)
            connection.shutdown(socket.SHUT_WR)
            response = connection.recv(4096)
        if not response:
            return False
        result = json.loads(response.decode("utf-8"))
        return bool(result.get("ok"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def create_ipc_listener(db_path: Path) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", ipc_port_for(db_path)))
    listener.listen(8)
    return listener


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="既存SQLiteのWikipedia記事をtitleで表示するタブ式ビューア"
    )
    parser.add_argument("--db", type=Path, required=True, help="記事SQLiteのパス")
    parser.add_argument("--title", default="", help="開く記事タイトル")
    parser.add_argument(
        "--print", dest="print_only", action="store_true", help="GUIを使わず標準出力"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = args.db.resolve()

    if not db_path.is_file():
        print(f"ERROR: SQLiteが見つかりません: {db_path}", file=sys.stderr)
        return 2

    if args.print_only:
        if not args.title.strip():
            print("ERROR: --print には --title が必要です。", file=sys.stderr)
            return 2
        row = get_article(db_path, args.title)
        if row is None:
            print(f"記事が見つかりません: {args.title}", file=sys.stderr)
            return 1
        print(format_article(row))
        return 0

    if send_to_existing_instance(db_path, args.title):
        return 0

    try:
        ipc_socket = create_ipc_listener(db_path)
    except OSError:
        time.sleep(0.15)
        if send_to_existing_instance(db_path, args.title):
            return 0
        ipc_socket = None

    app = ViewerApp(
        db_path=db_path,
        initial_title=args.title,
        ipc_socket=ipc_socket,
    )
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
