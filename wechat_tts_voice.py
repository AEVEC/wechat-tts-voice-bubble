from __future__ import annotations

import argparse
import ctypes
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import psutil
import pyautogui
import requests
import sounddevice as sd
import soundfile as sf


DEFAULT_TTS_URL = "http://ws:8000/v1/tts"
DEFAULT_DEVICE_NAME = "CABLE Input"
FALLBACK_DEVICE_NAMES = ("CABLE In 16ch", "VB-Audio Point")
WECHAT_PROCESS_NAMES = {"weixin.exe", "wechat.exe"}


def list_output_devices() -> None:
    print("可用的音频输出设备：")
    for index, device in enumerate(sd.query_devices()):
        if int(device["max_output_channels"]) > 0:
            print(
                f"  {index:>2}: {device['name']} "
                f"(channels={device['max_output_channels']}, "
                f"rate={device['default_samplerate']:.0f})"
            )


def find_output_devices(name_part: str) -> list[tuple[int, dict]]:
    search_names = (name_part, *FALLBACK_DEVICE_NAMES)
    matches: list[tuple[int, dict]] = []
    matched_name = name_part
    for candidate in search_names:
        matches = [
            (index, dict(device))
            for index, device in enumerate(sd.query_devices())
            if int(device["max_output_channels"]) > 0
            and candidate.casefold() in str(device["name"]).casefold()
        ]
        if matches:
            matched_name = candidate
            break

    if not matches:
        list_output_devices()
        raise RuntimeError(
            f"没有找到名称包含 {name_part!r} 或 {FALLBACK_DEVICE_NAMES!r} 的输出设备。"
            "请确认 VB-CABLE 已安装并已重启 Windows。"
        )

    if matched_name != name_part:
        print(f"未找到 {name_part!r}，已兼容匹配新版设备名 {matched_name!r}。")
    host_priority = {
        "Windows WASAPI": 0,
        "Windows DirectSound": 1,
        "MME": 2,
        "Windows WDM-KS": 3,
    }
    matches.sort(
        key=lambda item: (
            host_priority.get(
                str(sd.query_hostapis(item[1]["hostapi"])["name"]), 9
            ),
            0 if int(item[1]["max_output_channels"]) == 2 else 1,
        )
    )
    if len(matches) > 1:
        print(f"找到多个匹配设备，将使用：{matches[0][1]['name']}")
    return matches


def find_output_device(name_part: str) -> tuple[int, dict]:
    return find_output_devices(name_part)[0]


def request_tts(text: str, output_path: Path, url: str, token: str) -> None:
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={"text": text, "text_lang": "zh"},
        timeout=(10, 180),
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if "json" in content_type.casefold():
        raise RuntimeError(f"TTS 服务返回了 JSON，而不是音频：{response.text[:500]}")

    output_path.write_bytes(response.content)


def resample_linear(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return audio

    source_length = len(audio)
    target_length = max(1, round(source_length * target_rate / source_rate))
    source_points = np.linspace(0.0, 1.0, source_length, endpoint=False)
    target_points = np.linspace(0.0, 1.0, target_length, endpoint=False)
    channels = [
        np.interp(target_points, source_points, audio[:, channel])
        for channel in range(audio.shape[1])
    ]
    return np.stack(channels, axis=1).astype(np.float32, copy=False)


def foreground_process_name() -> str:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    process_id = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    try:
        return psutil.Process(process_id.value).name().casefold()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ""


def require_wechat_focused() -> None:
    process_name = foreground_process_name()
    if process_name not in WECHAT_PROCESS_NAMES:
        raise RuntimeError(
            "当前前台窗口不是微信，已取消发送以避免发错窗口。"
            "请打开目标聊天并保持微信在最前面后重试。"
        )


def current_session_name() -> str:
    session_id = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(
        os.getpid(), ctypes.byref(session_id)
    ):
        return os.getenv("SESSIONNAME", "")

    buffer = ctypes.c_void_p()
    byte_count = ctypes.c_ulong()
    # WTSWinStationName = 6
    if not ctypes.windll.wtsapi32.WTSQuerySessionInformationW(
        0, session_id.value, 6, ctypes.byref(buffer), ctypes.byref(byte_count)
    ):
        return os.getenv("SESSIONNAME", "")
    try:
        return ctypes.wstring_at(buffer)
    finally:
        ctypes.windll.wtsapi32.WTSFreeMemory(buffer)


def current_session_protocol_type() -> int | None:
    session_id = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(
        os.getpid(), ctypes.byref(session_id)
    ):
        return None

    buffer = ctypes.c_void_p()
    byte_count = ctypes.c_ulong()
    # WTSClientProtocolType = 16; 0 is console and 2 is RDP.
    if not ctypes.windll.wtsapi32.WTSQuerySessionInformationW(
        0, session_id.value, 16, ctypes.byref(buffer), ctypes.byref(byte_count)
    ):
        return None
    try:
        return ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort)).contents.value
    finally:
        ctypes.windll.wtsapi32.WTSFreeMemory(buffer)


def require_local_audio_session() -> None:
    protocol_type = current_session_protocol_type()
    # WTSClientProtocolType is authoritative. SESSIONNAME can remain RDP-Tcp
    # after Windows has returned the user to the physical console, so consult
    # the name only when the live WTS protocol query itself is unavailable.
    is_remote = protocol_type == 2
    if protocol_type is None:
        is_remote = "rdp" in current_session_name().casefold()
    if is_remote:
        raise RuntimeError(
            "当前程序运行在 Windows 远程桌面（RDP）会话中。RDP 会隔离远程电脑"
            "本机的 VB-CABLE 录音端点，微信会显示“未找到麦克风”。请在本地控制台"
            "会话或不会隔离主机音频设备的远控会话中运行。"
        )


def prepare_audio(
    wav_path: Path,
    device: dict,
    max_seconds: float,
) -> tuple[np.ndarray, int, float]:
    audio, source_rate = sf.read(wav_path, dtype="float32", always_2d=True)
    if len(audio) == 0:
        raise RuntimeError("TTS 返回了空音频。")

    duration = len(audio) / source_rate
    if duration > max_seconds:
        raise RuntimeError(
            f"音频长 {duration:.1f} 秒，超过 {max_seconds:.1f} 秒限制，请拆分文本。"
        )

    max_channels = int(device["max_output_channels"])
    if audio.shape[1] > max_channels:
        audio = np.mean(audio, axis=1, keepdims=True)

    target_rate = int(round(float(device["default_samplerate"])))
    audio = resample_linear(audio, int(source_rate), target_rate)
    return audio, target_rate, duration


def play_audio_with_fallback(
    wav_path: Path,
    device_name: str,
    max_seconds: float,
) -> tuple[float, int, dict, int]:
    """Use the original blocking playback path, with host-API fallback."""
    errors: list[str] = []
    for device_index, device in find_output_devices(device_name):
        host_name = str(sd.query_hostapis(device["hostapi"])["name"])
        try:
            audio, sample_rate, duration = prepare_audio(
                wav_path, device, max_seconds
            )
            sd.play(
                audio,
                sample_rate,
                device=device_index,
                blocking=True,
            )
            return duration, device_index, device, sample_rate
        except Exception as error:
            errors.append(f"{host_name}: {error}")
            sd.stop()
            time.sleep(0.30)

    detail = "；".join(errors[-3:])
    raise RuntimeError(
        f"无法播放到 VB-CABLE。已尝试多个 Windows 音频后端：{detail}"
    )


def send_voice(
    text: str,
    *,
    url: str,
    token: str,
    device_name: str,
    countdown: int,
    max_seconds: float,
    save_wav: Path | None,
    source_wav: Path | None,
) -> None:
    require_local_audio_session()
    temp_path: Path | None = None
    try:
        if source_wav is not None:
            wav_path = source_wav.resolve()
            if not wav_path.is_file():
                raise RuntimeError(f"找不到 WAV 文件：{wav_path}")
        elif save_wav is None:
            handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            handle.close()
            temp_path = Path(handle.name)
            wav_path = temp_path
        else:
            wav_path = save_wav.resolve()
            wav_path.parent.mkdir(parents=True, exist_ok=True)

        if source_wav is None:
            print("正在生成 TTS 音频……")
            request_tts(text, wav_path, url, token)

        if save_wav is not None:
            print(f"音频已保存：{wav_path}")
            return

        device_index, device = find_output_device(device_name)
        audio, sample_rate, duration = prepare_audio(wav_path, device, max_seconds)
        print(
            f"音频 {duration:.1f} 秒；将播放到：{device['name']}。\n"
            "请现在打开正确的微信聊天窗口。"
        )

        for remaining in range(countdown, 0, -1):
            print(f"{remaining}…", flush=True)
            time.sleep(1)

        require_wechat_focused()

        pyautogui.FAILSAFE = True
        try:
            pyautogui.keyDown("alt")
            time.sleep(0.45)
            sd.play(audio, sample_rate, device=device_index, blocking=True)
            time.sleep(0.30)
        finally:
            sd.stop()
            pyautogui.keyUp("alt")

        print("已完成微信录音操作。")
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="调用 TTS，并通过 VB-CABLE 作为微信语音消息发送。"
    )
    parser.add_argument("text", nargs="?", help="要转换并发送的文字")
    parser.add_argument("--list-devices", action="store_true", help="列出音频输出设备")
    parser.add_argument(
        "--device",
        default=os.getenv("WECHAT_TTS_DEVICE", DEFAULT_DEVICE_NAME),
        help=f"虚拟播放设备名称的一部分（默认：{DEFAULT_DEVICE_NAME}）",
    )
    parser.add_argument(
        "--tts-url",
        default=os.getenv("TTS_URL", DEFAULT_TTS_URL),
        help=f"TTS 接口地址（默认：{DEFAULT_TTS_URL}）",
    )
    parser.add_argument("--countdown", type=int, default=5, help="发送前倒计时秒数")
    parser.add_argument("--max-seconds", type=float, default=58.0)
    parser.add_argument(
        "--save-wav",
        type=Path,
        help="只生成并保存 WAV，不操作微信",
    )
    parser.add_argument(
        "--wav",
        type=Path,
        help="发送已有 WAV（不再调用 TTS）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list_devices:
        list_output_devices()
        return 0

    if not args.text and args.wav is None:
        print("错误：请提供要发送的文字。", file=sys.stderr)
        return 2

    token = os.getenv("TTS_TOKEN")
    if not token and args.wav is None:
        print("错误：请先设置环境变量 TTS_TOKEN。", file=sys.stderr)
        return 2

    try:
        send_voice(
            args.text or "",
            url=args.tts_url,
            token=token or "",
            device_name=args.device,
            countdown=max(0, args.countdown),
            max_seconds=args.max_seconds,
            save_wav=args.save_wav,
            source_wav=args.wav,
        )
    except Exception as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
