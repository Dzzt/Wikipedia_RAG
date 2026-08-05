from __future__ import annotations

import subprocess
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


class KiwixServer:
    """Wikipedia RAG専用のkiwix-serve子プロセスを管理する。"""

    def __init__(
        self,
        *,
        executable: Path,
        zim_file: Path,
        host: str = "127.0.0.1",
        port: int = 8080,
    ) -> None:
        self.executable = executable.resolve()
        self.zim_file = zim_file.resolve()
        self.host = host
        self.port = port
        self.process: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, timeout: float = 15.0) -> None:
        if not self.executable.is_file():
            raise FileNotFoundError(
                f"kiwix-serve.exeが見つかりません: {self.executable}"
            )
        if not self.zim_file.is_file():
            raise FileNotFoundError(
                f"ZIMファイルが見つかりません: {self.zim_file}"
            )

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            [
                str(self.executable),
                "--address",
                self.host,
                "--port",
                str(self.port),
                "--nodatealiases",
                str(self.zim_file),
            ],
            cwd=str(self.executable.parent),
            creationflags=creationflags,
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    "kiwix-serveが起動直後に終了しました "
                    f"(exit code: {self.process.returncode})"
                )
            try:
                check_kiwix_server(self.base_url, timeout=0.5)
                return
            except RuntimeError:
                time.sleep(0.2)

        self.stop()
        raise RuntimeError(
            f"kiwix-serveの起動確認がタイムアウトしました: {self.base_url}"
        )

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def open_article(
    viewer: str,
    title: str,
    *,
    base_dir: Path,
    kiwix_url: str = "http://127.0.0.1:8080",
    kiwix_zim: str = "wikipedia_ja_all",
) -> None:
    if viewer == "internal":
        open_with_internal_viewer(title=title, base_dir=base_dir)
        return
    if viewer == "kiwix":
        open_with_kiwix(title=title, base_url=kiwix_url, zim_name=kiwix_zim)
        return
    raise ValueError(f"未対応の記事ビューアーです: {viewer}")


def open_with_internal_viewer(*, title: str, base_dir: Path) -> None:
    viewer_script = base_dir / "wikipedia_viewer" / "wikipedia_jsonl_viewer.py"
    viewer_db = base_dir / "wikipedia_viewer" / "wikipedia_articles.sqlite3"
    if not viewer_script.is_file():
        raise FileNotFoundError(f"Viewer script not found: {viewer_script}")
    if not viewer_db.is_file():
        raise FileNotFoundError(f"Article database not found: {viewer_db}")
    subprocess.Popen(
        ["py", str(viewer_script), "--db", str(viewer_db), "--title", title],
        cwd=str(base_dir),
    )


def open_with_kiwix(*, title: str, base_url: str, zim_name: str) -> None:
    check_kiwix_server(base_url)
    encoded_zim = urllib.parse.quote(zim_name, safe="")
    encoded_title = urllib.parse.quote(title.replace(" ", "_"), safe="()")
    url = f"{base_url.rstrip('/')}/content/{encoded_zim}/{encoded_title}"
    if not webbrowser.open(url, new=2):
        raise RuntimeError(f"ブラウザを開けませんでした: {url}")


def check_kiwix_server(base_url: str, timeout: float = 0.5) -> None:
    try:
        with urllib.request.urlopen(base_url, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(
                    f"Kiwix Server returned HTTP {response.status}"
                )
    except OSError as exc:
        raise RuntimeError(
            f"Kiwix Serverへ接続できません: {base_url}"
        ) from exc
