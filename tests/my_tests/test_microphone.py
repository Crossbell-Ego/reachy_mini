"""Reachy Mini 麥克風雙層診斷工具.

用途：
    當「聲音 → 錄製」的音量條不跳、錄音檔全靜音、語音功能沒反應時，
    用這支釐清問題出在哪一層，而不是憑感覺猜。

    診斷分兩層，兩層的結果合起來才能定位病灶：

    第一層 — 主機音訊層 (PortAudio)
        用 MME / DirectSound / WASAPI 三種 host API 分別開啟
        「Reachy Mini Audio」錄音，逐通道量測 RMS / PEAK / dBFS。
        回答的是：「Windows 到底有沒有收到聲音資料？」

    第二層 — 音訊板韌體層 (XVF3800 USB control)
        直接讀 XMOS XVF3800 的內部狀態：增益、AEC/AGC 開關、麥克風數量，
        並連續取樣聲源定位 (DOA)、波束方位角、AGC 動態增益。
        回答的是：「音訊板自己的 DSP 有沒有聽到麥克風？」

判讀方式：
    第一層有訊號                   → 麥克風正常，問題在上層應用 (daemon / 應用程式)
    第一層全零 + 第二層 DOA 有反應 → USB 音訊串流或路由設定問題
    第一層全零 + 第二層 DOA 凍結   → DSP 收不到麥克風陣列訊號 (排線鬆脫或硬體故障)
    找不到裝置                     → USB 未列舉，先查線材與供電

⚠️ 執行前務必先關閉 Reachy Mini daemon / desktop app。
   daemon 會持有音訊裝置與 USB handle，會讓量測結果失真。
   （daemon 執行中時 WASAPI 通常會直接回傳零，看起來就像麥克風壞了。）

前置套件（裝在 reachy_mini_env 內）：
    uv pip install sounddevice numpy

執行方式：
    python test_microphone.py                 # 完整雙層診斷
    python test_microphone.py --duration 5    # 每項量測 5 秒
    python test_microphone.py --skip-usb      # 只測主機音訊層
    python test_microphone.py --skip-audio    # 只讀韌體狀態

歷史基準 (2026-08-25，麥克風故障時的實測值，供日後修復後比對)：
    音訊層  WASAPI ch0/ch1 = -190.0 dBFS (位元全零)
            MME / DirectSound = -96.7 dBFS (僅 1 LSB 抖動)
    韌體層  MIC_GAIN=15.0 / AGC=ON / AEC=ON / NUM_MICS=4 / 韌體 2.1.2  ← 設定全正常
            DOA 凍結 (0.0, 0.0)、SELECTED_AZIMUTHS 出現 nan、AGCGAIN 死鎖在 2.0
            同日稍後 MIC_GAIN 讀到 90.0（接近上限）仍然全靜音，連底噪都沒有 ——
            增益不是變因，這條路徑上根本沒有訊號可放大。
    當時已排除：Windows 麥克風隱私權限、daemon 佔用、韌體增益設定、USB 列舉。
    結論    設定與韌體皆正常，但 DSP 完全收不到麥克風訊號 → 判定硬體故障。

    修好之後重跑這支，應該要看到：對著說話時 peak 明顯高於 -30 dBFS，
    且 DOA 會隨聲源方向變動、AZIMUTHS 不再是 nan。
"""

import argparse
import math
import sys
import time

# 輸出被導向檔案或管線時，Windows 會退回 cp950，中文與符號會觸發 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

# 裝置名稱關鍵字。Windows 上端點會被在地化成「迴音消除式麥克風 (Reachy Mini Audio)」，
# 但括號內的產品名不會被翻譯，所以比對這段最穩。
DEVICE_KEYWORD = "Reachy Mini"

# 判定門檻 (dBFS)
SILENCE_DBFS = -80.0  # 低於此值視為數位靜音；安靜房間的底噪也該高於這條線
SPEECH_DBFS = -30.0  # 高於此值代表確實收到說話音量

DBFS_FLOOR = -190.0  # log(0) 的替代值，純零時顯示用

DYNAMIC_INTERVAL = 0.5  # 韌體層動態取樣間隔（秒）


def to_dbfs(value: float) -> float:
    """把 0.0~1.0 的線性振幅換算成 dBFS，全零時回傳地板值."""
    return 20 * math.log10(value) if value > 0 else DBFS_FLOOR


def measure_host_audio(duration: float) -> list[dict]:
    """第一層：用各 host API 錄音並量測位準。回傳每個通道的量測結果."""
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError:
        print("！ 缺少套件，跳過主機音訊層。請先執行：")
        print("    reachy_mini_env\\Scripts\\activate")
        print("    uv pip install sounddevice numpy")
        return []

    print("=" * 68)
    print("第一層：主機音訊層 (PortAudio)")
    print("=" * 68)

    devices = sd.query_devices()
    targets = [
        (i, d)
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0 and DEVICE_KEYWORD in d["name"]
    ]

    if not targets:
        print(f"！ 找不到名稱含「{DEVICE_KEYWORD}」的錄音裝置。")
        print("  代表 USB 沒有正確列舉，先檢查 USB-C 線材與供電。")
        print("\n目前偵測到的錄音裝置：")
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                api = sd.query_hostapis(d["hostapi"])["name"]
                print(f"  [{i:2}] {d['name'][:45]:45} api={api}")
        return []

    results = []
    for idx, dev in targets:
        api = sd.query_hostapis(dev["hostapi"])["name"]
        channels = min(dev["max_input_channels"], 2)
        rate = int(dev["default_samplerate"])
        print(f"\n[{idx}] {dev['name']}")
        print(f"    api={api} ch={channels} sr={rate}")
        print(f"    錄 {duration:.0f} 秒 — 請對著機器人說話或拍手…")

        try:
            frames = int(duration * rate)
            recording = sd.rec(
                frames, samplerate=rate, channels=channels, device=idx, dtype="float32"
            )
            sd.wait()
        except Exception as e:
            # 裝置被 daemon 或其他程式佔用時最常走到這裡。
            print(f"    ✗ 錄音失敗：{type(e).__name__}: {e}")
            continue

        for ch in range(channels):
            samples = recording[:, ch]
            rms = float(np.sqrt((samples**2).mean()))
            peak = float(np.abs(samples).max())
            peak_dbfs = to_dbfs(peak)
            mark = "✓" if peak_dbfs > SILENCE_DBFS else "✗ 數位靜音"
            print(
                f"    ch{ch}: RMS={to_dbfs(rms):7.1f} dBFS  "
                f"PEAK={peak_dbfs:7.1f} dBFS  {mark}"
            )
            results.append({"api": api, "channel": ch, "peak_dbfs": peak_dbfs})

    return results


def measure_firmware(duration: float) -> list[tuple]:
    """第二層：讀 XVF3800 靜態設定並連續取樣動態狀態。回傳動態取樣序列."""
    try:
        from reachy_mini.media.audio_control_utils import init_respeaker_usb
    except ImportError as e:
        print(f"！ 無法載入 audio_control_utils，跳過韌體層：{e}")
        return []

    print("\n" + "=" * 68)
    print("第二層：音訊板韌體層 (XVF3800 USB control)")
    print("=" * 68)

    respeaker = init_respeaker_usb()
    if respeaker is None:
        print("！ 找不到 XVF3800 USB 裝置。")
        print("  若第一層看得到錄音裝置卻讀不到這裡，多半是 daemon 正持有 USB handle。")
        return []

    try:
        version = respeaker.read("VERSION")
        # 回傳的第一個位元組是 status，後面才是版號。
        if isinstance(version, list) and len(version) >= 4:
            print(f"\n韌體版本：{version[1]}.{version[2]}.{version[3]}")

        print("\n靜態設定（這些正常不代表麥克風正常，只代表設定沒被改壞）：")
        static_params = [
            ("AUDIO_MGR_MIC_GAIN", "麥克風增益"),
            ("AUDIO_MGR_REF_GAIN", "參考訊號增益"),
            ("AEC_NUM_MICS", "麥克風數量"),
            ("PP_AGCONOFF", "AGC 自動增益"),
            ("PP_AGCGAIN", "AGC 當前增益"),
            ("PP_ECHOONOFF", "AEC 回音消除"),
            ("AUDIO_MGR_SELECTED_CHANNELS", "選定通道"),
            ("AUDIO_MGR_OP_ALL", "輸出路由"),
        ]
        for name, label in static_params:
            try:
                print(f"  {label:14} {name:30} = {respeaker.read_values(name)}")
            except Exception as e:
                print(f"  {label:14} {name:30} ! {e}")

        print(f"\n動態取樣 {duration:.0f} 秒 — 請對著機器人說話，或繞著它走動：")
        print("  （DOA 應隨聲源方向變動；凍結不動或 nan 代表 DSP 沒收到麥克風訊號）")
        print(f"\n  {'t':>5} {'DOA (rad)':>24} {'AZIMUTHS':>26} {'AGCGAIN':>10}")

        samples = []
        for i in range(int(duration / DYNAMIC_INTERVAL)):
            elapsed = i * DYNAMIC_INTERVAL
            try:
                doa = respeaker.read_values("DOA_VALUE_RADIANS")
                azimuths = respeaker.read_values("AUDIO_MGR_SELECTED_AZIMUTHS")
                agc = respeaker.read_values("PP_AGCGAIN")
                print(
                    f"  {elapsed:5.1f} {str(doa):>24} {str(azimuths):>26} {str(agc):>10}"
                )
                samples.append((doa, azimuths, agc))
            except Exception as e:
                print(f"  {elapsed:5.1f} 讀取失敗：{e}")
            time.sleep(DYNAMIC_INTERVAL)

        return samples
    finally:
        respeaker.close()


def report(audio_results: list[dict], firmware_samples: list[tuple]) -> None:
    """綜合兩層結果給出判讀."""
    print("\n" + "=" * 68)
    print("判讀")
    print("=" * 68)

    has_audio = any(r["peak_dbfs"] > SILENCE_DBFS for r in audio_results)
    has_speech = any(r["peak_dbfs"] > SPEECH_DBFS for r in audio_results)

    # DOA 在整段取樣中完全沒變動，代表 DSP 端沒有有效輸入。
    doa_values = {str(s[0]) for s in firmware_samples}
    doa_frozen = len(firmware_samples) > 1 and len(doa_values) == 1
    has_nan = any("nan" in str(s[1]) for s in firmware_samples)

    if has_speech:
        print("✓ 麥克風正常運作，收到明確的說話音量。")
    elif has_audio:
        print("△ 有訊號但音量偏低。可能是離太遠、增益偏低，或只有底噪。")
        print("  試著靠近再測一次；若仍偏低，可調高 AUDIO_MGR_MIC_GAIN。")
    elif audio_results:
        print("✗ 主機音訊層收到的是數位靜音（位元全零）。")
        if firmware_samples and doa_frozen:
            print("✗ 韌體層 DOA 全程凍結，DSP 完全沒有偵測到聲源。")
            if has_nan:
                print("✗ 波束方位角為 nan，波束成形器沒有有效輸入。")
            print("\n  → 判定：XVF3800 收不到麥克風陣列訊號。")
            print("     設定與韌體都正常，問題在 PDM 麥克風 → DSP 這段實體路徑。")
            print("     處置順序：")
            print("       1. 完全拔除 USB-C 等 10 秒再插回（排除韌體卡死）")
            print("       2. 仍無訊號則開殼檢查麥克風板排線是否鬆脫")
            print("       3. 排線正常仍無訊號 → 硬體故障，向 Pollen Robotics 報修")
        elif firmware_samples:
            print("△ 但韌體層 DOA 有變動，代表 DSP 聽得到麥克風。")
            print("  → 問題在 USB 音訊串流或輸出路由，不是麥克風本身。")
        else:
            print("  韌體層沒有資料，無法進一步定位。請確認 daemon 已關閉後重跑。")
    else:
        print("！ 沒有取得任何音訊層資料，無法判讀。")
        print("  確認 daemon / desktop app 已關閉，且已安裝 sounddevice。")


def main() -> None:
    """解析參數並執行診斷."""
    parser = argparse.ArgumentParser(
        description="Reachy Mini 麥克風雙層診斷工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--duration", type=float, default=3.0, help="每項量測的秒數 (預設 3)"
    )
    parser.add_argument("--skip-audio", action="store_true", help="跳過主機音訊層")
    parser.add_argument("--skip-usb", action="store_true", help="跳過韌體層")
    args = parser.parse_args()

    print("Reachy Mini 麥克風診斷")
    print("⚠️  請先確認 Reachy Mini daemon / desktop app 已關閉，否則結果會失真。\n")

    audio_results = [] if args.skip_audio else measure_host_audio(args.duration)
    firmware_samples = [] if args.skip_usb else measure_firmware(args.duration)
    report(audio_results, firmware_samples)


if __name__ == "__main__":
    main()
