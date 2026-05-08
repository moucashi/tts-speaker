from __future__ import annotations

import argparse
from datetime import datetime
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any


ESC = "\x1b"
CTRL_C = "\x03"
DEFAULT_MODEL = "zh_CN-xiao_ya-medium"
DEFAULT_OUTPUT_DIR = "voices"
MAX_FILENAME_TEXT_LENGTH = 24
WINDOWS_FORBIDDEN_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_VOICE_CACHE: dict[Path, Any] = {}


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


def get_voice_files(voice_name: str) -> tuple[Path, Path]:
    model_path = Path(voice_name)
    if model_path.suffix == ".onnx":
        return model_path, Path(f"{model_path}.json")

    return Path(f"{voice_name}.onnx"), Path(f"{voice_name}.onnx.json")


def is_downloadable_voice_name(voice_name: str) -> bool:
    model_path = Path(voice_name)
    return not model_path.is_absolute() and model_path.parent == Path(".") and model_path.suffix == ""


def has_voice_files(voice_name: str) -> bool:
    model_file, config_file = get_voice_files(voice_name)
    return model_file.exists() and config_file.exists()


def resolve_model_path(model: str) -> Path:
    model_file, _ = get_voice_files(model)
    return model_file


def download_voice(voice_name: str) -> bool:
    from piper.download_voices import download_voice as piper_download_voice

    print(f"当前目录未找到语音：{voice_name}")
    print("开始下载语音...")

    try:
        piper_download_voice(voice_name, Path.cwd())
    except Exception as exc:  # noqa: BLE001
        print(f"语音下载失败：{exc}")
        return False

    print(f"语音下载完成：{voice_name}")
    return True


def ensure_voice_available(voice_name: str) -> bool:
    if has_voice_files(voice_name):
        return True

    model_file, config_file = get_voice_files(voice_name)
    if not is_downloadable_voice_name(voice_name):
        print("未找到语音模型文件：")
        print(f"- {model_file}")
        print(f"- {config_file}")
        print("当前模型参数不是语音名，无法自动下载。请放置模型文件后重试。")
        return False

    if not download_voice(voice_name):
        return False

    if has_voice_files(voice_name):
        return True

    print("语音下载命令已结束，但当前目录仍未找到完整语音文件：")
    print(f"- {model_file}")
    print(f"- {config_file}")
    return False


def open_empty_window() -> None:
    def run_window() -> None:
        try:
            import tkinter as tk

            window = tk.Tk()
            window.title("tts-speaker")
            window.geometry("320x200")
            window.mainloop()
        except Exception as exc:  # noqa: BLE001
            print(f"打开空窗口失败：{exc}")

    threading.Thread(target=run_window, daemon=True).start()


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


def load_voice(model: str):
    from piper import PiperVoice

    model_path = resolve_model_path(model).resolve()
    voice = _VOICE_CACHE.get(model_path)
    if voice is None:
        print("正在加载语音模型...")
        voice = PiperVoice.load(model_path, download_dir=Path.cwd())
        _VOICE_CACHE[model_path] = voice

    return voice


def synthesize_voice_to_wav(
    model: str,
    output: Path,
    text: str,
    cancel_requested: threading.Event,
) -> None:
    voice = load_voice(model)
    wav_file: wave.Wave_write | None = None

    try:
        for audio_chunk in voice.synthesize(text):
            if cancel_requested.is_set():
                return

            if wav_file is None:
                wav_file = wave.open(str(output), "wb")
                wav_file.setframerate(audio_chunk.sample_rate)
                wav_file.setsampwidth(audio_chunk.sample_width)
                wav_file.setnchannels(audio_chunk.sample_channels)

            wav_file.writeframes(audio_chunk.audio_int16_bytes)
    finally:
        if wav_file is not None:
            wav_file.close()


def start_cancel_monitor(keys: TerminalKeys, cancel_requested: threading.Event) -> threading.Event:
    done = threading.Event()

    def monitor() -> None:
        while not done.is_set() and not cancel_requested.is_set():
            try:
                ch = keys.read_key_if_available()
            except KeyboardInterrupt:
                cancel_requested.set()
                break

            if ch in (ESC, CTRL_C):
                cancel_requested.set()
                break

            time.sleep(0.05)

    threading.Thread(target=monitor, daemon=True).start()
    return done


def generate_voice(model: str, output: Path, text: str, keys: TerminalKeys) -> tuple[bool, list[str]]:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(
        f".{output.stem}.{os.getpid()}.{int(time.time() * 1000)}.tmp{output.suffix or '.wav'}"
    )
    cancel_requested = threading.Event()
    monitor_done = start_cancel_monitor(keys, cancel_requested)

    try:
        synthesize_voice_to_wav(model, temp_output, text, cancel_requested)
    except KeyboardInterrupt:
        cancel_requested.set()
        if temp_output.exists():
            temp_output.unlink()
        return False, ["已取消生成。"]
    except Exception as exc:  # noqa: BLE001
        if temp_output.exists():
            temp_output.unlink()
        return False, [f"生成失败：{exc}"]
    finally:
        monitor_done.set()

    if cancel_requested.is_set():
        if temp_output.exists():
            temp_output.unlink()
        return False, ["已取消生成。"]

    if not temp_output.exists():
        return False, ["生成失败：未找到临时输出文件。"]

    temp_output.replace(output)
    return True, []


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
        default=DEFAULT_MODEL,
        help=f"Piper 模型名称或模型文件路径，默认：{DEFAULT_MODEL}。",
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

    open_empty_window()

    print("交互式中文语音生成")
    print(f"语音文件会保存到 {output_dir}/日期/时间_文本开头.wav。")
    print("输入文本后回车生成语音。输入中按 Esc 或 Ctrl+C 清空；空输入时按 Ctrl+C 退出。")
    print("生成中按 Esc 或 Ctrl+C 取消。")

    if not ensure_voice_available(args.model):
        return 1

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
