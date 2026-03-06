"""Desktop GUI for Subtitle Tool — Batch processing with multi-language support."""

from __future__ import annotations

import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from subtitle_tool.config import (
    EXPORT_FORMATS,
    SUPPORTED_BROWSERS,
    SUPPORTED_LANGUAGES,
    WHISPER_MODELS,
    AppConfig,
    WhisperConfig,
)
from subtitle_tool.exceptions import SubtitleToolError, ValidationError

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════
# Logging handler → GUI textbox
# ═════════════════════════════════════════════════════════════════


class TextBoxHandler(logging.Handler):
    """Route log messages to the GUI textbox."""

    def __init__(self, textbox: ctk.CTkTextbox, app: SubtitleApp) -> None:
        super().__init__()
        self.textbox = textbox
        self.app = app

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record) + "\n"
        try:
            self.app.after(0, self._append, msg)
        except RuntimeError:
            # App already destroyed
            pass

    def _append(self, msg: str) -> None:
        try:
            self.textbox.configure(state="normal")
            self.textbox.insert("end", msg)
            self.textbox.see("end")
            self.textbox.configure(state="disabled")
        except Exception:
            # Widget may have been destroyed
            pass


# ═════════════════════════════════════════════════════════════════
# Video queue item widget
# ═════════════════════════════════════════════════════════════════


class VideoItem(ctk.CTkFrame):
    """A single video row in the queue list."""

    STATUS_COLORS = {
        "waiting": "gray",
        "downloading": "#3498db",
        "transcribing": "#9b59b6",
        "encoding": "#e67e22",
        "done": "#2ecc71",
        "error": "#e74c3c",
        "skipped": "#f39c12",
        "retrying": "#e67e22",
    }

    def __init__(self, parent: ctk.CTkFrame, url: str, index: int) -> None:
        super().__init__(parent, fg_color=("#e8e8e8", "#2b2b2b"), corner_radius=8, height=50)
        self.pack_propagate(False)

        self.url = url
        self.index = index
        self.status = "waiting"

        # Layout
        self.grid_columnconfigure(1, weight=1)

        # Index badge
        self.idx_label = ctk.CTkLabel(
            self, text=f"#{index}", width=35,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="gray",
        )
        self.idx_label.grid(row=0, column=0, padx=(8, 4), pady=8)

        # URL / Title
        display = url if len(url) <= 55 else url[:52] + "..."
        self.title_label = ctk.CTkLabel(
            self, text=display,
            font=ctk.CTkFont(size=12),
            anchor="w",
        )
        self.title_label.grid(row=0, column=1, sticky="w", padx=4, pady=8)

        # Status badge
        self.status_label = ctk.CTkLabel(
            self, text="⏳ Waiting", width=140,
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        self.status_label.grid(row=0, column=2, padx=(4, 4), pady=8)

        # Remove button
        self.remove_btn = ctk.CTkButton(
            self, text="✕", width=30, height=28,
            fg_color="transparent", hover_color=("#d4d4d4", "#404040"),
            text_color="gray", font=ctk.CTkFont(size=14),
            command=self._remove,
        )
        self.remove_btn.grid(row=0, column=3, padx=(0, 6), pady=8)

    def set_status(self, status: str, detail: str = "") -> None:
        self.status = status
        color = self.STATUS_COLORS.get(status, "gray")
        icons = {
            "waiting": "⏳", "downloading": "📥", "transcribing": "🗣️",
            "encoding": "🔄", "done": "✅", "error": "❌", "skipped": "⏭",
            "retrying": "🔁",
        }
        icon = icons.get(status, "")
        text = f"{icon} {status.capitalize()}"
        if detail:
            text += f" · {detail}"
        self.status_label.configure(text=text, text_color=color)

    def set_title(self, title: str) -> None:
        display = title if len(title) <= 50 else title[:47] + "..."
        self.title_label.configure(text=display)

    def _remove(self) -> None:
        if self.status in ("waiting", "done", "error", "skipped"):
            self.destroy()


# ═════════════════════════════════════════════════════════════════
# Main App
# ═════════════════════════════════════════════════════════════════


class SubtitleApp(ctk.CTk):
    """Main application window with batch processing and multi-language support."""

    def __init__(self) -> None:
        super().__init__()

        self.title("🎬 Daisy Subtitle Tool v2.0")
        self.geometry("820x800")
        self.minsize(720, 700)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._is_processing = False
        self._stop_requested = False
        self._video_items: list[VideoItem] = []
        self._build_ui()
        self._setup_logging()

    # ─── UI Build ────────────────────────────────────────────

    def _build_ui(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=20, pady=16)

        # Title
        title_row = ctk.CTkFrame(main, fg_color="transparent")
        title_row.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            title_row, text="🎬 Daisy Subtitle Tool",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            title_row, text="v2.0 · Multi-Language", font=ctk.CTkFont(size=11),
            text_color="#3498db",
        ).pack(side="left", padx=(8, 0), pady=(6, 0))

        # ─── Add URL Row ───
        add_frame = ctk.CTkFrame(main, fg_color="transparent")
        add_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            add_frame, text="YouTube URL",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w")

        url_row = ctk.CTkFrame(add_frame, fg_color="transparent")
        url_row.pack(fill="x", pady=(4, 0))

        self.url_entry = ctk.CTkEntry(
            url_row,
            placeholder_text="Paste YouTube URL and click Add (or press Enter)",
            height=38, font=ctk.CTkFont(size=13),
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.url_entry.bind("<Return>", lambda e: self._add_url())

        ctk.CTkButton(
            url_row, text="+ Add", width=70, height=38,
            command=self._add_url,
        ).pack(side="right", padx=(0, 4))

        ctk.CTkButton(
            url_row, text="📋 Paste Multiple", width=120, height=38,
            fg_color="#555", hover_color="#666",
            command=self._paste_multiple,
        ).pack(side="right")

        # ─── Video Queue ───
        queue_label = ctk.CTkFrame(main, fg_color="transparent")
        queue_label.pack(fill="x", pady=(5, 4))
        ctk.CTkLabel(
            queue_label, text="Video Queue",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")
        self.queue_count = ctk.CTkLabel(
            queue_label, text="0 videos", font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        self.queue_count.pack(side="left", padx=(8, 0))

        ctk.CTkButton(
            queue_label, text="Clear All", width=70, height=26,
            fg_color="transparent", hover_color=("#d4d4d4", "#404040"),
            text_color="gray", font=ctk.CTkFont(size=11),
            command=self._clear_queue,
        ).pack(side="right")

        # Scrollable queue list
        self.queue_frame = ctk.CTkScrollableFrame(
            main, height=140,
            fg_color=("#f0f0f0", "#1e1e1e"), corner_radius=8,
        )
        self.queue_frame.pack(fill="x", pady=(0, 10))

        # ─── Settings Row 1: FFmpeg + Model ───
        settings1 = ctk.CTkFrame(main, fg_color="transparent")
        settings1.pack(fill="x", pady=(0, 6))

        # FFmpeg
        ff_col = ctk.CTkFrame(settings1, fg_color="transparent")
        ff_col.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ctk.CTkLabel(ff_col, text="FFmpeg", font=ctk.CTkFont(size=12)).pack(anchor="w")
        ff_row = ctk.CTkFrame(ff_col, fg_color="transparent")
        ff_row.pack(fill="x", pady=(3, 0))
        self.ffmpeg_entry = ctk.CTkEntry(
            ff_row, height=32, font=ctk.CTkFont(size=12),
            placeholder_text="C:\\ffmpeg\\bin\\ffmpeg.exe",
        )
        self.ffmpeg_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            ff_row, text="...", width=32, height=32,
            command=self._browse_ffmpeg,
        ).pack(side="right")

        # Model
        m_col = ctk.CTkFrame(settings1, fg_color="transparent")
        m_col.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(m_col, text="Model", font=ctk.CTkFont(size=12)).pack(anchor="w")
        self.model_var = ctk.StringVar(value="tiny")
        ctk.CTkOptionMenu(
            m_col, values=list(WHISPER_MODELS),
            variable=self.model_var, width=100, height=32,
        ).pack(pady=(3, 0))

        # Workers (multi-threading)
        wk_col = ctk.CTkFrame(settings1, fg_color="transparent")
        wk_col.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(wk_col, text="🚀 Workers", font=ctk.CTkFont(size=12)).pack(anchor="w")
        self.workers_var = ctk.StringVar(value="2")
        ctk.CTkOptionMenu(
            wk_col, values=["1", "2", "3", "4"],
            variable=self.workers_var, width=60, height=32,
        ).pack(pady=(3, 0))

        # Cookies Browser (for YouTube anti-bot bypass)
        ck_col = ctk.CTkFrame(settings1, fg_color="transparent")
        ck_col.pack(side="left", padx=(0, 0))
        ctk.CTkLabel(ck_col, text="🍪 Cookies", font=ctk.CTkFont(size=12)).pack(anchor="w")
        self.cookies_var = ctk.StringVar(value="none")
        ctk.CTkOptionMenu(
            ck_col, values=list(SUPPORTED_BROWSERS),
            variable=self.cookies_var, width=100, height=32,
        ).pack(pady=(3, 0))

        # ─── Settings Row 2: Language + Export + Watermark + Output ───
        settings2 = ctk.CTkFrame(main, fg_color="transparent")
        settings2.pack(fill="x", pady=(0, 10))

        # Language
        l_col = ctk.CTkFrame(settings2, fg_color="transparent")
        l_col.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(l_col, text="🌍 Language", font=ctk.CTkFont(size=12)).pack(anchor="w")
        lang_display = [f"{code} - {name}" for code, name in SUPPORTED_LANGUAGES.items()]
        self.lang_var = ctk.StringVar(value="en - English")
        ctk.CTkOptionMenu(
            l_col, values=lang_display,
            variable=self.lang_var, width=150, height=32,
        ).pack(pady=(3, 0))

        # Export Format
        e_col = ctk.CTkFrame(settings2, fg_color="transparent")
        e_col.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(e_col, text="📝 Export", font=ctk.CTkFont(size=12)).pack(anchor="w")
        self.export_var = ctk.StringVar(value="srt")
        ctk.CTkOptionMenu(
            e_col, values=list(EXPORT_FORMATS),
            variable=self.export_var, width=80, height=32,
        ).pack(pady=(3, 0))

        # Watermark
        w_col = ctk.CTkFrame(settings2, fg_color="transparent")
        w_col.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(w_col, text="Watermark", font=ctk.CTkFont(size=12)).pack(anchor="w")
        self.wm_entry = ctk.CTkEntry(w_col, width=90, height=32, font=ctk.CTkFont(size=12))
        self.wm_entry.insert(0, "Daisy")
        self.wm_entry.pack(pady=(3, 0))

        # Output
        o_col = ctk.CTkFrame(settings2, fg_color="transparent")
        o_col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(o_col, text="Output", font=ctk.CTkFont(size=12)).pack(anchor="w")
        o_row = ctk.CTkFrame(o_col, fg_color="transparent")
        o_row.pack(fill="x", pady=(3, 0))
        self.out_entry = ctk.CTkEntry(o_row, height=32, font=ctk.CTkFont(size=12))
        self.out_entry.insert(0, "./output")
        self.out_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            o_row, text="📁", width=32, height=32,
            command=self._browse_output,
        ).pack(side="right")

        # ─── Action Buttons ───
        btn_row = ctk.CTkFrame(main, fg_color="transparent")
        btn_row.pack(fill="x", pady=(4, 8))

        self.start_btn = ctk.CTkButton(
            btn_row, text="▶  Start All", height=42,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_start,
        )
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            btn_row, text="⏹ Stop", height=42, width=90,
            fg_color="#c0392b", hover_color="#e74c3c",
            font=ctk.CTkFont(size=14, weight="bold"),
            state="disabled",
            command=self._on_stop,
        )
        self.stop_btn.pack(side="right")

        # ─── Progress ───
        self.progress_bar = ctk.CTkProgressBar(main, height=6)
        self.progress_bar.pack(fill="x", pady=(0, 3))
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(
            main, text="Ready — Add YouTube URLs to get started",
            font=ctk.CTkFont(size=12), text_color="gray",
        )
        self.status_label.pack(anchor="w", pady=(0, 6))

        # ─── Log ───
        self.log_box = ctk.CTkTextbox(
            main, font=ctk.CTkFont(family="Consolas", size=11),
            state="disabled", wrap="word",
        )
        self.log_box.pack(fill="both", expand=True)

        self._load_ffmpeg_path()

    # ─── Logging ─────────────────────────────────────────────

    def _setup_logging(self) -> None:
        self._log_handler = TextBoxHandler(self.log_box, self)
        self._log_handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        root_logger = logging.getLogger("subtitle_tool")
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(self._log_handler)
        # Clean up handler when window closes
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        """Remove log handler before destroying window."""
        root_logger = logging.getLogger("subtitle_tool")
        if hasattr(self, "_log_handler"):
            root_logger.removeHandler(self._log_handler)
        self.destroy()

    # ─── Queue Management ────────────────────────────────────

    def _add_url(self) -> None:
        url = self.url_entry.get().strip()
        if not url:
            return
        if "youtube.com" not in url and "youtu.be" not in url:
            self._set_status("⚠ Not a valid YouTube URL", "orange")
            return

        idx = len(self._video_items) + 1
        item = VideoItem(self.queue_frame, url, idx)
        item.pack(fill="x", pady=2)
        self._video_items.append(item)

        self.url_entry.delete(0, "end")
        self._update_count()

    def _paste_multiple(self) -> None:
        """Open a dialog to paste multiple URLs at once."""
        dialog = ctk.CTkInputDialog(
            text="Paste multiple YouTube URLs (one per line):",
            title="Add Multiple URLs",
        )
        text = dialog.get_input()
        if not text:
            return
        for line in text.strip().split("\n"):
            url = line.strip()
            if url and ("youtube.com" in url or "youtu.be" in url):
                idx = len(self._video_items) + 1
                item = VideoItem(self.queue_frame, url, idx)
                item.pack(fill="x", pady=2)
                self._video_items.append(item)
        self._update_count()

    def _clear_queue(self) -> None:
        if self._is_processing:
            return
        for item in self._video_items:
            item.destroy()
        self._video_items.clear()
        self._update_count()

    def _update_count(self) -> None:
        # Filter out destroyed items
        self._video_items = [i for i in self._video_items if i.winfo_exists()]
        n = len(self._video_items)
        self.queue_count.configure(text=f"{n} video{'s' if n != 1 else ''}")

    # ─── Helpers ─────────────────────────────────────────────

    def _get_language_code(self) -> str:
        """Extract language code from display string like 'en - English'."""
        lang_str = self.lang_var.get()
        return lang_str.split(" - ")[0] if " - " in lang_str else lang_str

    # ─── File Dialogs ────────────────────────────────────────

    def _browse_ffmpeg(self) -> None:
        path = filedialog.askopenfilename(
            title="Select ffmpeg.exe",
            filetypes=[("FFmpeg", "ffmpeg.exe"), ("All", "*.*")],
        )
        if path:
            self.ffmpeg_entry.delete(0, "end")
            self.ffmpeg_entry.insert(0, path)
            self._save_ffmpeg_path(path)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Select Output Folder")
        if path:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, path)

    def _save_ffmpeg_path(self, path: str) -> None:
        (Path.home() / ".subtitle_tool_config").write_text(path, encoding="utf-8")

    def _load_ffmpeg_path(self) -> None:
        cfg = Path.home() / ".subtitle_tool_config"
        if cfg.exists():
            saved = cfg.read_text(encoding="utf-8").strip()
            if saved and (Path(saved).exists() or shutil.which(saved)):
                self.ffmpeg_entry.delete(0, "end")
                self.ffmpeg_entry.insert(0, saved)
                return

        # Auto-detect ffmpeg in system PATH
        found = shutil.which("ffmpeg")
        if found:
            self.ffmpeg_entry.delete(0, "end")
            self.ffmpeg_entry.insert(0, found)
            logger.info("Auto-detected FFmpeg: %s", found)

    # ─── Processing ──────────────────────────────────────────

    def _on_start(self) -> None:
        if self._is_processing:
            return

        self._update_count()
        pending = [i for i in self._video_items if i.status == "waiting"]
        if not pending:
            self._set_status("⚠ No videos in queue", "orange")
            return

        ffmpeg = self.ffmpeg_entry.get().strip() or "ffmpeg"

        # Validate FFmpeg: check as file path OR as command in system PATH
        ffmpeg_valid = False
        if Path(ffmpeg).is_file():
            ffmpeg_valid = True
        elif shutil.which(ffmpeg):
            ffmpeg_valid = True

        if not ffmpeg_valid:
            self._set_status("⚠ FFmpeg not found! Browse to ffmpeg.exe or install it.", "#e74c3c")
            return

        self._is_processing = True
        self._stop_requested = False
        workers = int(self.workers_var.get())
        mode = f"⏳ Processing ({workers} workers)..." if workers > 1 else "⏳ Processing..."
        self.start_btn.configure(state="disabled", text=mode)
        self.stop_btn.configure(state="normal")
        self.progress_bar.set(0)

        # Clear log
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

        if workers > 1:
            threading.Thread(
                target=self._process_all_parallel,
                args=(workers,), daemon=True,
            ).start()
        else:
            threading.Thread(target=self._process_all, daemon=True).start()

    def _on_stop(self) -> None:
        self._stop_requested = True
        self._set_status("⏹ Stopping after current video...", "orange")

    def _process_all(self) -> None:
        """Process all pending videos sequentially (workers=1)."""
        pending = [i for i in self._video_items if i.winfo_exists() and i.status == "waiting"]
        total = len(pending)

        done_count = 0
        error_count = 0

        for vi_idx, item in enumerate(pending):
            if self._stop_requested:
                self.after(0, item.set_status, "waiting", "Stopped")
                continue

            overall = f"[{vi_idx + 1}/{total}]"
            self._set_status(f"{overall} Processing...")

            try:
                self._process_one(item, overall)
                done_count += 1
            except ValidationError as exc:
                self.after(0, item.set_status, "skipped", str(exc)[:40])
                logger.warning("%s Skipped: %s", overall, exc)
            except (SubtitleToolError, Exception) as exc:
                self.after(0, item.set_status, "error", str(exc)[:40])
                logger.error("%s Error: %s", overall, exc)
                error_count += 1

        self._show_summary(done_count, error_count, total)

    def _process_all_parallel(self, max_workers: int) -> None:
        """Process all pending videos with multi-threading."""
        pending = [i for i in self._video_items if i.winfo_exists() and i.status == "waiting"]
        total = len(pending)

        logger.info("🚀 Starting parallel processing: %d videos × %d workers", total, max_workers)

        # Read all GUI values ONCE from main thread context before spawning workers
        # (tkinter widgets should not be read from worker threads)
        gui_config = {
            "ffmpeg": self.ffmpeg_entry.get().strip() or "ffmpeg",
            "output_dir": self.out_entry.get().strip() or "./output",
            "model": self.model_var.get(),
            "language": self._get_language_code(),
            "export": self.export_var.get(),
            "watermark": self.wm_entry.get().strip() or "Daisy",
            "cookies": self.cookies_var.get(),
        }

        done_count = 0
        error_count = 0
        count_lock = threading.Lock()

        def _worker(item: VideoItem, vi_idx: int) -> None:
            """Worker function for a single video."""
            nonlocal done_count, error_count
            if self._stop_requested:
                self.after(0, item.set_status, "waiting", "Stopped")
                return

            worker_id = (vi_idx % max_workers) + 1
            overall = f"[W{worker_id}·{vi_idx + 1}/{total}]"

            logger.info("%s 🔄 Starting video...", overall)
            self.after(0, item.set_status, "downloading", f"W{worker_id}")

            try:
                self._process_one(item, overall, worker_id=worker_id, gui_config=gui_config)
                with count_lock:
                    done_count += 1
                logger.info("%s ✅ Completed!", overall)
            except ValidationError as exc:
                self.after(0, item.set_status, "skipped", str(exc)[:40])
                logger.warning("%s Skipped: %s", overall, exc)
            except (SubtitleToolError, Exception) as exc:
                self.after(0, item.set_status, "error", str(exc)[:40])
                logger.error("%s Error: %s", overall, exc)
                with count_lock:
                    error_count += 1

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_worker, item, vi_idx)
                for vi_idx, item in enumerate(pending)
            ]
            # Wait for all to complete
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logger.error("Worker crashed: %s", exc)
                    with count_lock:
                        error_count += 1

        self._show_summary(done_count, error_count, total)

    def _show_summary(self, done_count: int, error_count: int, total: int) -> None:
        """Display processing summary."""
        lang_name = SUPPORTED_LANGUAGES.get(self._get_language_code(), "")
        workers = int(self.workers_var.get())
        summary = f"✅ Done! {done_count}/{total} succeeded"
        if workers > 1:
            summary += f" ({workers}W)"
        if lang_name:
            summary += f" [{lang_name}]"
        if error_count:
            summary += f", {error_count} failed"
        self._set_status(summary, "#2ecc71" if error_count == 0 else "orange")
        self.after(0, self._reset_buttons)

    def _process_one(
        self,
        item: VideoItem,
        prefix: str,
        worker_id: int = 0,
        gui_config: dict | None = None,
    ) -> None:
        """Process a single video using the shared pipeline with progress.

        Args:
            item: VideoItem widget in the queue.
            prefix: Log prefix like '[1/4]' or '[W1·1/4]'.
            worker_id: Worker thread ID (0 = sequential mode).
            gui_config: Pre-read GUI values for thread safety (used in parallel mode).
        """
        from subtitle_tool.pipeline import process_single_video

        # Use pre-read config if provided (parallel mode), else read from GUI (sequential)
        if gui_config:
            ffmpeg = gui_config["ffmpeg"]
            lang_code = gui_config["language"]
            output_dir = gui_config["output_dir"]
            model = gui_config["model"]
            export_fmt = gui_config["export"]
            watermark_text = gui_config["watermark"]
            cookies = gui_config.get("cookies", "none")
        else:
            ffmpeg = self.ffmpeg_entry.get().strip() or "ffmpeg"
            lang_code = self._get_language_code()
            output_dir = self.out_entry.get().strip() or "./output"
            model = self.model_var.get()
            export_fmt = self.export_var.get()
            watermark_text = self.wm_entry.get().strip() or "Daisy"
            cookies = self.cookies_var.get()

        cookies_browser = cookies if cookies and cookies != "none" else ""

        config = AppConfig(
            output_dir=Path(output_dir),
            ffmpeg_path=ffmpeg,
            export_format=export_fmt,
            cookies_browser=cookies_browser,
        )
        config.whisper = WhisperConfig(
            model_size=model,
            language=lang_code,
        )
        config.watermark.text = watermark_text

        lang_display = SUPPORTED_LANGUAGES.get(lang_code, lang_code)
        self.after(0, item.set_status, "downloading",
                   f"W{worker_id}" if worker_id > 0 else "")
        self._set_status(f"{prefix} Downloading... [{lang_display}]")

        # Progress callback for real-time encoding updates
        def on_progress(current_sec: float, total_sec: float, speed: str) -> None:
            pct = min(current_sec / total_sec, 1.0) if total_sec > 0 else 0
            m, s = divmod(int(current_sec), 60)
            tm, ts = divmod(int(total_sec), 60)
            w_tag = f"W{worker_id} " if worker_id > 0 else ""
            status = (
                f"{prefix} Encoding {pct*100:.0f}%  "
                f"{m:02d}:{s:02d}/{tm:02d}:{ts:02d}  {speed}"
            )
            self._set_status(status)
            self.after(0, self.progress_bar.set, pct)
            self.after(0, item.set_status, "encoding",
                       f"{w_tag}{pct*100:.0f}%  {speed}")

        result = process_single_video(
            item.url, config,
            progress_callback=on_progress,
            worker_id=worker_id,
        )

        if result.video_info.title != "unknown":
            self.after(0, item.set_title, result.video_info.title)

        if result.success and result.output_path:
            size_mb = result.output_path.stat().st_size / (1024 * 1024)
            # Show quality score and grade
            quality_info = ""
            if result.quality_score is not None and result.quality_grade is not None:
                quality_info = f" · Q:{result.quality_score:.0f}({result.quality_grade})"

            # Show export formats
            export_info = ""
            if result.export_paths:
                formats = [f.upper() for f in result.export_paths.keys() if f != "srt"]
                if formats:
                    export_info = f" · +{','.join(formats)}"

            detail = f"{size_mb:.0f} MB{quality_info}{export_info}"
            self.after(0, item.set_status, "done", detail)
            self.after(0, self.progress_bar.set, 1.0)
        elif result.error:
            raise SubtitleToolError(result.error)
        else:
            # Result has no output_path and no explicit error
            raise SubtitleToolError("Processing failed (unknown error)")

    # ─── Helpers ─────────────────────────────────────────────

    def _set_status(self, text: str, color: str = "white") -> None:
        self.after(0, self.status_label.configure, {"text": text, "text_color": color})

    def _reset_buttons(self) -> None:
        self._is_processing = False
        self._stop_requested = False
        self.start_btn.configure(state="normal", text="▶  Start All")
        self.stop_btn.configure(state="disabled")


def main() -> None:
    app = SubtitleApp()
    app.mainloop()


if __name__ == "__main__":
    main()
