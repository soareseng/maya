from __future__ import annotations

import asyncio
import logging
import os
import re
import select
import shutil
import sys
import termios
import time
import tty
from collections import deque
from dataclasses import dataclass

from src.utils.logger import logger

ANSI_CLEAR_SCREEN = "\033[2J\033[3J\033[H"
ANSI_HIDE_CURSOR = "\033[?25l"
ANSI_SHOW_CURSOR = "\033[?25h"
ANSI_RESET = "\033[0m"
ANSI_ORANGE = "\033[38;5;208m"
ANSI_BRIGHT_ORANGE = "\033[38;5;214m"
ANSI_DARK = "\033[38;5;236m"
ANSI_WHITE = "\033[38;5;255m"
ANSI_GRAY = "\033[38;5;245m"
ANSI_GREEN = "\033[38;5;120m"
ANSI_YELLOW = "\033[38;5;221m"
ANSI_RED = "\033[38;5;203m"
ANSI_CYAN = "\033[38;5;117m"
ANSI_BLACK_BG = "\033[48;5;16m"
ANSI_ORANGE_BG = "\033[48;5;208m"
ANSI_DIM_BG = "\033[48;5;234m"

PROGRESS_RE = re.compile(r"\[PROGRESS\]\s*([0-9]+(?:\.[0-9]+)?)%")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
SPINNER_FRAMES = ["|", "/", "-", "\\"]


@dataclass
class TUIState:
    status: str = "Initializing"
    progress_percent: float = 0.0
    peers_connected: int = 0
    pieces_done: int = 0
    pieces_total: int = 0
    trackers_ok: int = 0
    trackers_total: int = 0
    new_peers_last_cycle: int = 0
    downloaded_bytes: int = 0


class UILogHandler(logging.Handler):
    def __init__(self, max_entries: int = 12):
        super().__init__(level=logging.INFO)
        self.entries: deque[tuple[str, str]] = deque(maxlen=max_entries)

    def emit(self, record: logging.LogRecord) -> None:
        self.entries.append((record.levelname, record.getMessage()))


class OrangeBlackTUI:
    def __init__(self, torrent, log_handler: UILogHandler):
        self.torrent = torrent
        self.log_handler = log_handler
        self.state = TUIState()
        self.started_at = time.time()
        self._progress_history: deque[tuple[float, float]] = deque(maxlen=180)
        self._bytes_history: deque[tuple[float, int]] = deque(maxlen=240)
        self._rate_samples: deque[float] = deque(maxlen=80)
        self._spinner_index = 0
        self._view = "overview"
        self._show_help = True
        self._refresh_interval = 0.12
        self._quit_requested = False
        self._tty_configured = False
        self._stdin_fd: int | None = None
        self._old_termios: list | None = None

    def _terminal_size(self) -> tuple[int, int]:
        size = shutil.get_terminal_size((110, 34))
        return max(96, size.columns), max(28, size.lines)

    def _setup_keyboard(self) -> None:
        if not sys.stdin.isatty() or os.name != "posix":
            return

        self._stdin_fd = sys.stdin.fileno()
        self._old_termios = termios.tcgetattr(self._stdin_fd)
        tty.setcbreak(self._stdin_fd)
        self._tty_configured = True

    def _restore_keyboard(self) -> None:
        if not self._tty_configured:
            return

        if self._stdin_fd is not None and self._old_termios is not None:
            termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._old_termios)

        self._tty_configured = False

    def _poll_key(self) -> None:
        if not self._tty_configured or self._stdin_fd is None:
            return

        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if not ready:
            return

        key = sys.stdin.read(1).lower()
        if key == "1":
            self._view = "overview"
        elif key == "2":
            self._view = "network"
        elif key == "3":
            self._view = "logs"
        elif key == "h":
            self._show_help = not self._show_help
        elif key == "+":
            self._refresh_interval = max(0.05, self._refresh_interval - 0.02)
        elif key == "-":
            self._refresh_interval = min(0.30, self._refresh_interval + 0.02)
        elif key == "q":
            self._quit_requested = True

    def _color_for_level(self, level: str) -> str:
        return {
            "DEBUG": ANSI_GRAY,
            "INFO": ANSI_WHITE,
            "WARNING": ANSI_YELLOW,
            "ERROR": ANSI_RED,
            "CRITICAL": ANSI_RED,
        }.get(level, ANSI_WHITE)

    def _format_bytes(self, size: int) -> str:
        units = ["B", "KiB", "MiB", "GiB", "TiB"]
        value = float(max(0, size))
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                return f"{value:6.2f} {unit}"
            value /= 1024.0
        return "0.00 B"

    def _parse_logs(self) -> None:
        for _, entry in list(self.log_handler.entries):
            progress_match = PROGRESS_RE.search(entry)
            if progress_match:
                self.state.progress_percent = float(progress_match.group(1))
                self.state.status = "Downloading"

            if "trackers OK:" in entry:
                marker = (
                    entry.rsplit("trackers OK:", maxsplit=1)[-1].strip().rstrip(")")
                )
                parts = marker.split("/")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    self.state.trackers_ok = int(parts[0])
                    self.state.trackers_total = int(parts[1])

            if "Connecting to peer:" in entry:
                self.state.status = "Connecting"

            if "Starting download:" in entry:
                self.state.status = "Starting"

        peers = self.torrent.peer_manager.get_peers()
        self.state.peers_connected = sum(
            1 for peer in peers if peer.tcp_protocol and peer.tcp_protocol.is_connected
        )

        self.state.trackers_ok = getattr(self.torrent, "last_announce_ok", 0)
        self.state.trackers_total = getattr(
            self.torrent,
            "last_announce_total",
            len(self.torrent.announce_list),
        )
        self.state.new_peers_last_cycle = getattr(
            self.torrent,
            "last_announce_new_peers",
            0,
        )

        piece_manager = self.torrent.piece_manager
        if piece_manager:
            self.state.pieces_done = len(piece_manager.downloaded)
            self.state.pieces_total = piece_manager.total_pieces
            self.state.downloaded_bytes = piece_manager.get_downloaded_bytes()
            if piece_manager.total_pieces > 0:
                computed_progress = (
                    len(piece_manager.downloaded) / piece_manager.total_pieces
                ) * 100
                self.state.progress_percent = max(
                    self.state.progress_percent,
                    computed_progress,
                )

    def _progress_bar(self, width: int, value: float) -> str:
        clamped = max(0.0, min(100.0, value))
        fill = int((clamped / 100.0) * width)
        empty = width - fill
        return f"{ANSI_ORANGE_BG}{' ' * fill}" f"{ANSI_DIM_BG}{' ' * empty}{ANSI_RESET}"

    def _horizontal_meter(self, width: int, ratio: float, color: str) -> str:
        clamped = max(0.0, min(1.0, ratio))
        fill = int(clamped * width)
        empty = width - fill
        return f"{color}{'█' * fill}{ANSI_DARK}{'░' * empty}{ANSI_RESET}"

    def _estimate_eta_seconds(self) -> int | None:
        now = time.time()
        progress = self.state.progress_percent

        if progress <= 0 or progress >= 100:
            return None

        self._progress_history.append((now, progress))

        while self._progress_history and now - self._progress_history[0][0] > 90:
            self._progress_history.popleft()

        if len(self._progress_history) < 2:
            return None

        first_time, first_progress = self._progress_history[0]
        last_time, last_progress = self._progress_history[-1]
        elapsed = last_time - first_time
        gained = last_progress - first_progress

        if elapsed <= 0 or gained <= 0:
            return None

        progress_per_second = gained / elapsed
        remaining = 100.0 - progress
        return int(remaining / progress_per_second)

    def _format_eta(self, eta_seconds: int | None) -> str:
        if eta_seconds is None:
            return "--:--:--"

        eta_seconds = max(0, eta_seconds)
        hours, rem = divmod(eta_seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _format_rate(self, bytes_per_second: float) -> str:
        units = ["B/s", "KiB/s", "MiB/s", "GiB/s"]
        rate = max(0.0, bytes_per_second)
        unit = units[0]
        for next_unit in units[1:]:
            if rate < 1024.0:
                break
            rate /= 1024.0
            unit = next_unit
        return f"{rate:6.2f} {unit}"

    def _download_speed_metrics(self) -> tuple[str, str]:
        now = time.time()
        downloaded = self.state.downloaded_bytes
        self._bytes_history.append((now, downloaded))

        while self._bytes_history and now - self._bytes_history[0][0] > 30:
            self._bytes_history.popleft()

        current_bps = 0.0
        if len(self._bytes_history) >= 2:
            first_t, first_b = self._bytes_history[0]
            last_t, last_b = self._bytes_history[-1]
            elapsed = last_t - first_t
            if elapsed > 0 and last_b >= first_b:
                current_bps = (last_b - first_b) / elapsed

        total_elapsed = max(1.0, now - self.started_at)
        average_bps = downloaded / total_elapsed
        self._rate_samples.append(current_bps)

        return self._format_rate(current_bps), self._format_rate(average_bps)

    def _sparkline(self, width: int) -> str:
        if not self._rate_samples:
            return " " * width

        levels = "▁▂▃▄▅▆▇█"
        sample = list(self._rate_samples)[-width:]
        max_rate = max(sample)
        if max_rate <= 0:
            return " " * width

        chars = []
        for value in sample:
            idx = int((value / max_rate) * (len(levels) - 1))
            chars.append(levels[idx])
        return "".join(chars).rjust(width)

    def _status_color(self) -> str:
        if self.state.status in {"Failed"}:
            return ANSI_RED
        if self.state.status in {"Complete"}:
            return ANSI_GREEN
        if self.state.status in {"Connecting", "Starting"}:
            return ANSI_YELLOW
        return ANSI_CYAN

    def _fit_visible_width(self, text: str, width: int) -> str:
        if width <= 0:
            return ""

        out: list[str] = []
        visible = 0
        index = 0

        while index < len(text) and visible < width:
            if text[index] == "\x1b":
                match = ANSI_ESCAPE_RE.match(text, index)
                if match:
                    out.append(match.group(0))
                    index = match.end()
                    continue

            out.append(text[index])
            visible += 1
            index += 1

        if visible < width:
            out.append(" " * (width - visible))

        # Always terminate styles before rendering the right border.
        out.append(ANSI_RESET)
        return "".join(out)

    def _visible_len(self, text: str) -> int:
        return len(ANSI_ESCAPE_RE.sub("", text))

    def _render_line(self, text: str, inner: int, color: str = ANSI_WHITE) -> str:
        content = self._fit_visible_width(text, inner)
        return (
            f"{ANSI_BLACK_BG}{ANSI_ORANGE}║{ANSI_RESET} "
            f"{color}{content}{ANSI_RESET} "
            f"{ANSI_BLACK_BG}{ANSI_ORANGE}║{ANSI_RESET}"
        )

    def _render_logs_panel(self, body: list[str], inner: int, lines: int) -> None:
        body.append(f"{ANSI_BLACK_BG}{ANSI_ORANGE}╠{'═' * (inner + 2)}╣{ANSI_RESET}")
        body.append(self._render_line("Recent Events", inner, ANSI_BRIGHT_ORANGE))

        logs = list(self.log_handler.entries)[-lines:]
        for level, message in logs:
            label = f"[{level[:4]}]"
            rendered = f"{label} {message}".replace("\n", " ")
            body.append(
                self._render_line(rendered, inner, self._color_for_level(level))
            )

        for _ in range(max(0, lines - len(logs))):
            body.append(self._render_line("", inner, ANSI_GRAY))

    def _frame(self) -> str:
        self._parse_logs()
        width, height = self._terminal_size()
        inner = width - 4
        elapsed = int(time.time() - self.started_at)
        minutes, seconds = divmod(elapsed, 60)
        eta_text = self._format_eta(self._estimate_eta_seconds())
        current_speed, average_speed = self._download_speed_metrics()
        self._spinner_index = (self._spinner_index + 1) % len(SPINNER_FRAMES)
        spinner = SPINNER_FRAMES[self._spinner_index]

        peer_count = max(1, len(self.torrent.peer_manager.get_peers()))
        peer_ratio = self.state.peers_connected / peer_count
        tracker_total = max(1, self.state.trackers_total)
        tracker_ratio = self.state.trackers_ok / tracker_total

        top_inner = width - 2
        header_title = f"{ANSI_BRIGHT_ORANGE} MAYA TORRENT CONTROL CENTER {ANSI_RESET}"
        header_status = (
            f"{self._status_color()}{spinner} {self.state.status:<11}{ANSI_RESET}"
        )
        spacer_count = max(
            1,
            top_inner
            - self._visible_len(header_title)
            - self._visible_len(header_status),
        )
        header_content = (
            f"{header_title}{ANSI_DARK}{' ' * spacer_count}{ANSI_RESET}{header_status}"
        )

        top = (
            f"{ANSI_BLACK_BG}{ANSI_ORANGE}╔{'═' * (width - 2)}╗{ANSI_RESET}\n"
            f"{ANSI_BLACK_BG}{ANSI_ORANGE}║{ANSI_RESET}"
            f"{self._fit_visible_width(header_content, top_inner)}"
            f"{ANSI_BLACK_BG}{ANSI_ORANGE}║{ANSI_RESET}\n"
            f"{ANSI_BLACK_BG}{ANSI_ORANGE}╠{'═' * (width - 2)}╣{ANSI_RESET}\n"
        )

        body = []
        tabs = (
            f"[{ '1:Overview' if self._view == 'overview' else '1 Overview' }]  "
            f"[{ '2:Network' if self._view == 'network' else '2 Network' }]  "
            f"[{ '3:Logs' if self._view == 'logs' else '3 Logs' }]"
        )
        body.append(self._render_line(tabs, inner, ANSI_CYAN))

        if self._view == "overview":
            body.append(self._render_line(f"File: {self.torrent.name or '-'}", inner))
            body.append(
                self._render_line(
                    f"Speed: {current_speed} (avg {average_speed})  |  Downloaded: {self._format_bytes(self.state.downloaded_bytes)}",
                    inner,
                )
            )
            body.append(
                self._render_line(
                    f"Pieces: {self.state.pieces_done}/{self.state.pieces_total}  |  ETA: {eta_text}  |  Elapsed: {minutes:02d}:{seconds:02d}",
                    inner,
                )
            )
            body.append(self._render_line("Speed Trend", inner, ANSI_BRIGHT_ORANGE))
            body.append(self._render_line(self._sparkline(inner), inner, ANSI_ORANGE))
        elif self._view == "network":
            body.append(
                self._render_line(
                    f"Peers connected: {self.state.peers_connected}/{peer_count}  |  New peers (last announce): {self.state.new_peers_last_cycle}",
                    inner,
                )
            )
            body.append(
                self._render_line(
                    f"Trackers healthy: {self.state.trackers_ok}/{tracker_total}",
                    inner,
                )
            )
            body.append(
                self._render_line("Peer Connectivity", inner, ANSI_BRIGHT_ORANGE)
            )
            body.append(
                self._render_line(
                    self._horizontal_meter(inner, peer_ratio, ANSI_GREEN), inner
                )
            )
            body.append(
                self._render_line("Tracker Availability", inner, ANSI_BRIGHT_ORANGE)
            )
            body.append(
                self._render_line(
                    self._horizontal_meter(inner, tracker_ratio, ANSI_CYAN), inner
                )
            )
        else:
            body.append(
                self._render_line("Live Logs Stream", inner, ANSI_BRIGHT_ORANGE)
            )

        progress_label = f"Progress: {self.state.progress_percent:6.2f}%"
        body.append(self._render_line(progress_label, inner, ANSI_BRIGHT_ORANGE))

        bar = self._progress_bar(inner - 2, self.state.progress_percent)
        body.append(self._render_line(bar, inner))

        if self._view == "logs":
            log_lines = max(6, height - 18)
        else:
            log_lines = max(4, height - 24)
        self._render_logs_panel(body, inner, log_lines)

        if self._show_help:
            help_text = (
                "Keys: [1]Overview [2]Network [3]Logs [h]Help [+/ -]Refresh [q]Quit"
            )
            body.append(self._render_line(help_text, inner, ANSI_GRAY))

        bottom = f"{ANSI_BLACK_BG}{ANSI_ORANGE}╚{'═' * (width - 2)}╝{ANSI_RESET}"
        return top + "\n".join(body) + "\n" + bottom

    async def run(self, torrent_coro: asyncio.Future) -> None:
        task = asyncio.create_task(torrent_coro)
        self._setup_keyboard()
        print(ANSI_HIDE_CURSOR, end="")
        try:
            while not task.done():
                self._poll_key()
                if self._quit_requested:
                    task.cancel()
                    self.state.status = "Stopped"
                    break

                print(ANSI_CLEAR_SCREEN + self._frame(), end="", flush=True)
                await asyncio.sleep(self._refresh_interval)

            if not task.cancelled():
                await task
                self.state.status = "Complete"
                self.state.progress_percent = 100.0
        except asyncio.CancelledError:
            self.state.status = "Stopped"
            raise
        except Exception:
            self.state.status = "Failed"
            raise
        finally:
            print(ANSI_CLEAR_SCREEN + self._frame(), end="\n", flush=True)
            print(ANSI_SHOW_CURSOR, end="", flush=True)
            self._restore_keyboard()


async def run_torrent_with_tui(torrent) -> None:
    handler = UILogHandler(max_entries=14)
    suspended_handlers = [h for h in logger.handlers if h is not handler]

    for log_handler in suspended_handlers:
        logger.removeHandler(log_handler)

    logger.addHandler(handler)
    ui = OrangeBlackTUI(torrent=torrent, log_handler=handler)

    try:
        await ui.run(torrent.run())
    finally:
        logger.removeHandler(handler)
        for log_handler in suspended_handlers:
            logger.addHandler(log_handler)
