from __future__ import annotations

import argparse
from datetime import datetime
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


ESC = "\x1b"
CTRL_C = "\x03"
DEFAULT_OUTPUT_DIR = "voices"
MAX_FILENAME_TEXT_LENGTH = 24
WINDOWS_FORBIDDEN_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ExitRequested(Exception):
    pass


class TerminalKeys:
    def __init__(self) -> None:
        self.is_windows = platform.system() == "Windows"
        self._old_termios: list[int | bytes] | None = None

    def __enter__(self) -> "TerminalKeys":
        if not self.is_windows:
            import termios
            import tty

            self._old_termios = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())

        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if not self.is_windows and self._old_termios is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_termios)

    def read_key(self) -> str:
        if self.is_windows:
            import msvcrt

            return msvcrt.getwch()

        return sys.stdin.read(1)

    def read_key_if_available(self) -> str | None:
        if self.is_windows:
            import msvcrt

            if not msvcrt.kbhit():
                return None

            return msvcrt.getwch()

        import select

        readable, _, _ = select.select([sys.stdin], [], [], 0)
        if not readable:
            return None

        return sys.stdin.read(1)


def clear_line(prompt: str) -> None:
    sys.stdout.write("\r\033[2K" + prompt)
    sys.stdout.flush()


def read_text(prompt: str, keys: TerminalKeys) -> str:
    buffer: list[str] = []
    sys.stdout.write(prompt)
    sys.stdout.flush()

    while True:
        try:
            ch = keys.read_key()
        except KeyboardInterrupt:
            if buffer:
                buffer.clear()
                clear_line(prompt)
                continue

            raise ExitRequested

        if ch in ("\r", "\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "".join(buffer).strip()

        if ch == ESC:
            if buffer:
                buffer.clear()
                clear_line(prompt)
            continue

        if ch == CTRL_C:
            if buffer:
                buffer.clear()
                clear_line(prompt)
                continue

            raise ExitRequested

        if ch in ("\b", "\x7f"):
            if buffer:
                buffer.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue

        if ch in ("\x00", "\xe0"):
            # Windows extended key prefix. Ignore the following key code.
            keys.read_key()
            continue

        if ch.isprintable():
            buffer.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()


def read_play_answer(prompt: str, keys: TerminalKeys) -> bool:
    sys.stdout.write(prompt)
    sys.stdout.flush()

    while True:
        try:
            ch = keys.read_key()
        except KeyboardInterrupt as exc:
            sys.stdout.write("\n")
            sys.stdout.flush()
            raise ExitRequested from exc

        lower_ch = ch.lower()
        if ch in ("\r", "\n") or lower_ch == "y":
            sys.stdout.write("\n")
            sys.stdout.flush()
            return True

        if ch == ESC or lower_ch == "n":
            sys.stdout.write("\n")
            sys.stdout.flush()
            return False


def make_piper_command(model: str, output: Path, text: str) -> list[str]:
    return [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "piper",
        "-m",
        model,
        "-f",
        str(output),
        "--",
        text,
    ]


def sanitize_filename_text(text: str) -> str:
    compact_text = re.sub(r"\s+", "", text)
    safe_text = WINDOWS_FORBIDDEN_FILENAME_CHARS.sub("_", compact_text)
    safe_text = safe_text.strip(" ._")

    if not safe_text:
        return "语音"

    return safe_text[:MAX_FILENAME_TEXT_LENGTH]


def build_output_path(output_dir: Path, text: str, now: datetime | None = None) -> Path:
    current_time = now or datetime.now()
    date_dir = output_dir / current_time.strftime("%Y-%m-%d")
    time_prefix = current_time.strftime("%H%M%S")
    text_part = sanitize_filename_text(text)
    candidate = date_dir / f"{time_prefix}_{text_part}.wav"

    index = 1
    while candidate.exists():
        candidate = date_dir / f"{time_prefix}_{text_part}_{index:02d}.wav"
        index += 1

    return candidate


def drain_output(stream, output_queue: queue.Queue[str]) -> None:  # type: ignore[no-untyped-def]
    try:
        for line in stream:
            output_queue.put(line.rstrip())
    finally:
        stream.close()


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def generate_voice(model: str, output: Path, text: str, keys: TerminalKeys) -> tuple[bool, list[str]]:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(
        f".{output.stem}.{os.getpid()}.{int(time.time() * 1000)}.tmp{output.suffix or '.wav'}"
    )
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    command = make_piper_command(model, temp_output, text)
    creationflags = 0
    start_new_session = False

    if platform.system() == "Windows":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        start_new_session = True

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    output_queue: queue.Queue[str] = queue.Queue()
    assert process.stdout is not None
    reader = threading.Thread(target=drain_output, args=(process.stdout, output_queue), daemon=True)
    reader.start()

    try:
        while process.poll() is None:
            ch = keys.read_key_if_available()
            if ch in (ESC, CTRL_C):
                terminate_process(process)
                if temp_output.exists():
                    temp_output.unlink()
                return False, ["已取消生成。"]

            time.sleep(0.05)
    except KeyboardInterrupt:
        terminate_process(process)
        if temp_output.exists():
            temp_output.unlink()
        return False, ["已取消生成。"]

    reader.join(timeout=1)
    logs: list[str] = []
    while not output_queue.empty():
        logs.append(output_queue.get())

    if process.returncode != 0:
        if temp_output.exists():
            temp_output.unlink()
        return False, logs

    if not temp_output.exists():
        return False, logs + ["生成失败：未找到临时输出文件。"]

    temp_output.replace(output)
    return True, logs


def play_wav(path: Path) -> None:
    if platform.system() == "Windows":
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        return

    players = [
        ["afplay", str(path)],
        ["aplay", str(path)],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
    ]
    for command in players:
        if shutil.which(command[0]):
            subprocess.run(command, check=False)
            return

    print("未找到可用的音频播放器，请手动打开生成的 WAV 文件。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="交互式 Piper 中文语音生成命令行程序。")
    parser.add_argument(
        "-m",
        "--model",
        default="zh_CN-xiao_ya-medium",
        help="Piper 模型名称或模型文件路径，默认：zh_CN-xiao_ya-medium。",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录，默认：{DEFAULT_OUTPUT_DIR}。程序会按日期创建子目录。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)

    print("交互式中文语音生成")
    print(f"语音文件会保存到 {output_dir}/日期/时间_文本开头.wav。")
    print("输入文本后回车生成语音。输入中按 Esc 或 Ctrl+C 清空；空输入时按 Ctrl+C 退出。")
    print("生成中按 Esc 或 Ctrl+C 取消。")

    with TerminalKeys() as keys:
        while True:
            try:
                text = read_text("\n请输入文本> ", keys)
            except ExitRequested:
                print("\n已退出。")
                return 0

            if not text:
                continue

            output = build_output_path(output_dir, text)
            print("正在生成语音...")
            success, logs = generate_voice(args.model, output, text, keys)
            if not success:
                print(logs[-1] if logs else "生成失败。")
                if logs and logs[-1] != "已取消生成。":
                    print("最近输出：")
                    for line in logs[-8:]:
                        print(line)
                continue

            print(f"已保存：{output}")
            try:
                should_play = read_play_answer("是否播放？[Y/n] ", keys)
            except ExitRequested:
                print("已退出。")
                return 0

            if should_play:
                try:
                    play_wav(output)
                except KeyboardInterrupt:
                    print("\n播放已中断。")
                except Exception as exc:  # noqa: BLE001
                    print(f"播放失败：{exc}")


if __name__ == "__main__":
    raise SystemExit(main())
