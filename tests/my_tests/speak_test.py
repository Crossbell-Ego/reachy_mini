"""Reachy Mini 喇叭發聲 + 頭部擺動測試（edge-tts 繁中語音）.

為什麼不用 time.sleep(duration) 等播放結束
------------------------------------------
`mini.media.play_sound()` 內部最後一行只是 `playbin.set_state(PLAYING)`，
這是 **非同步** 的：函式返回時聲音根本還沒開始播。實測（本機、LOCAL backend）：

    play_sound() 返回     :  801 ms
    真正進入 PLAYING      :  890 ms   ← 開 WASAPI 端點 + decodebin 探測格式
    EOS（實際播完）       : 6386 ms   （mp3 本身 5.50 秒）

所以 `time.sleep(duration + 0.5)` = 6.00 秒，會在真正播完前 0.39 秒就往下走，
接著 `with` 區塊結束觸發 `__exit__`，playbin 被 set 成 NULL——尾巴直接被砍掉。
起步延遲越大（裝置冷、檔案短），被砍掉的比例越高，短句就整段沒聲音。

同時頭卻照樣會動，是因為 head wobbler 在 `set_state(PLAYING)` **之前**就啟動了，
而且它的資料來自 tee 分支，只要 decodebin 出得來就有——不必等 WASAPI 端點開好。
「頭會動但沒聲音」就是這麼來的，不是喇叭壞掉。

正確做法：等 playbin 真的進 PLAYING，再等 bus 上的 EOS。見 play_and_wait()。

執行方式
--------
    python tests/my_tests/speak_test.py
    python tests/my_tests/speak_test.py --text "要說的話" --voice zh-TW-YunJheNeural

在 Jupyter notebook 裡跑的話，把 main() 裡的 asyncio.run(_tts()) 換成
`await edge_tts.Communicate(text, voice).save(path)`，其餘不變。
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

import edge_tts  # noqa: E402

from reachy_mini import ReachyMini  # noqa: E402
from reachy_mini.media.device_detection import get_audio_device  # noqa: E402
from reachy_mini.media.gstreamer_utils import audio_duration_seconds  # noqa: E402

# 輸出被導向檔案或管線時 Windows 會退回 cp950，中文會炸 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

# 可選繁中語音：
#   zh-TW-HsiaoChenNeural（女聲·曉臻）
#   zh-TW-HsiaoYuNeural  （女聲·曉雨）
#   zh-TW-YunJheNeural   （男聲·雲哲）
DEFAULT_TEXT = "你好！我是 Reachy Mini，現在正在測試喇叭發聲與頭部擺動功能。"
DEFAULT_VOICE = "zh-TW-HsiaoChenNeural"


def check_audio_sink() -> None:
    """確認 GStreamer 找得到 Reachy 的喇叭端點.

    找不到時 SDK 會靜默 fallback 到 autoaudiosink，聲音會跑去 Windows 的預設
    輸出裝置（藍牙耳機之類），Reachy 這邊自然沒聲音——但頭照樣會動。

    注意 Gst.init() 一定要先呼叫：device monitor 在未初始化時會拋例外，
    而 get_audio_device() 會把例外吞掉回傳 None，看起來就像「找不到裝置」。
    """
    Gst.init([])  # 冪等
    sink_id = get_audio_device("Sink")
    if sink_id is None:
        print("⚠️  找不到 Reachy Mini 的喇叭端點，SDK 會退回系統預設輸出裝置。")
        print("   聲音可能跑到耳機/螢幕喇叭去了。檢查 USB-C 連線後重試。")
    else:
        print(f"✓ 喇叭端點: {sink_id}")


def play_and_wait(mini: ReachyMini, path: Path, timeout_margin: float = 15.0) -> None:
    """播放音檔並確實等到播完.

    先等 playbin 完成 async 狀態轉換（真正開始出聲），再等 bus 上的 EOS。
    拿不到內部 playbin 時退回保守的 sleep，至少不會比原本更糟。
    """
    abs_path = str(path.resolve())
    duration = audio_duration_seconds(abs_path)
    print(f"🔊 播放中（{duration:.1f} 秒）…")

    t0 = time.perf_counter()
    mini.media.play_sound(abs_path)

    playbin = getattr(mini.media.audio, "_playbin", None)
    if playbin is None:
        # WEBRTC / NO_MEDIA backend 沒有本地 playbin，只能盲等。
        print("（拿不到 playbin，退回盲等；起步延遲無法補償）")
        time.sleep(duration + 1.5)
        return

    # 阻塞到 NULL→PLAYING 的 async 轉換結束，這才是真正開始發聲的時刻。
    playbin.get_state(10 * Gst.SECOND)
    t_playing = time.perf_counter() - t0

    msg = playbin.get_bus().timed_pop_filtered(
        int((duration + timeout_margin) * Gst.SECOND),
        Gst.MessageType.EOS | Gst.MessageType.ERROR,
    )
    t_done = time.perf_counter() - t0

    if msg is None:
        print(f"⚠️  等不到 EOS（已等 {t_done:.1f}s），播放可能卡住了。")
    elif msg.type == Gst.MessageType.ERROR:
        err, dbg = msg.parse_error()
        print(f"✗ 播放錯誤：{err.message}")
        print(f"  debug: {dbg}")
    else:
        print(
            f"✓ 播完（起步 {t_playing:.2f}s + 發聲 {t_done - t_playing:.2f}s "
            f"= {t_done:.2f}s）"
        )


def main() -> None:
    """合成語音並在 Reachy Mini 上播放，同時擺頭."""
    parser = argparse.ArgumentParser(description="Reachy Mini 喇叭 + 擺頭測試")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="要說的話")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="edge-tts 語音")
    parser.add_argument(
        "--keep", action="store_true", help="保留產生的 mp3（預設用完就刪）"
    )
    args = parser.parse_args()

    mp3_path = Path(__file__).with_name("reachy_tts_test.mp3")

    print("🔊 透過 edge-tts 生成語音…")

    async def _tts() -> None:
        await edge_tts.Communicate(args.text, args.voice).save(str(mp3_path))

    asyncio.run(_tts())
    print(f"✓ 語音已生成: {mp3_path.name}")

    check_audio_sink()

    with ReachyMini(log_level="WARNING") as mini:
        print(f"✓ 已連上 Reachy Mini（media backend = {mini.media.backend}）")
        mini.enable_wobbling()
        try:
            play_and_wait(mini, mp3_path)
        finally:
            mini.disable_wobbling()

    if not args.keep:
        mp3_path.unlink(missing_ok=True)

    print("✓ 喇叭測試完成！")


if __name__ == "__main__":
    main()
