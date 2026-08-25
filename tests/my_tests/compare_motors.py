"""比對多顆馬達的暫存器設定，自動標出與多數不同的值.

純唯讀工具：只讀暫存器，不寫入、不移動馬達、不開扭力。

用途：
    當某顆馬達行為和其他顆不同（走速、力道、精度），先用這支確認
    是「設定不同」還是「硬體問題」——設定不同會直接被標記出來。

⚠️ 執行前務必先關閉 Reachy Mini daemon / desktop app，否則序列埠會被佔用。

執行方式：
    python compare_motors.py                  # 比對六顆 Stewart 馬達 (11-16)
    python compare_motors.py --ids 10,11,17   # 指定要比對的 ID
    python compare_motors.py --port COM7
"""

import argparse
import sys
from collections import Counter

import serial.tools.list_ports
from rustypot import Xl330PyController

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

MOTOR_NAMES = {
    10: "body_rotation",
    11: "stewart_1",
    12: "stewart_2",
    13: "stewart_3",
    14: "stewart_4",
    15: "stewart_5",
    16: "stewart_6",
    17: "right_antenna",
    18: "left_antenna",
}

# (欄位標題, rustypot 暫存器名, 是否納入「與多數不同」的比對)
# 位置限位與 homing offset 每顆本來就不同（鏡像配置），不納入比對。
REGISTERS = [
    ("ProfVel", "profile_velocity", True),
    ("ProfAcc", "profile_acceleration", True),
    ("VelLimit", "velocity_limit", True),
    ("Drive", "drive_mode", True),
    ("MovThr", "moving_threshold", True),
    ("P", "position_p_gain", True),
    ("I", "position_i_gain", True),
    ("D", "position_d_gain", True),
    ("PWMlim", "pwm_limit", True),
    ("CurLim", "current_limit", True),
    ("OpMode", "operating_mode", True),
    ("Shutdown", "shutdown", True),
    ("RetDelay", "return_delay_time", True),
    ("Homing", "homing_offset", False),
    ("MinPos", "raw_min_position_limit", False),
    ("MaxPos", "raw_max_position_limit", False),
    ("Temp", "present_temperature", False),
    ("HwErr", "hardware_error_status", False),
]


def find_serial_ports(vid: str = "1A86", pid: str = "55D3") -> list[str]:
    """嚴格比對 VID:PID 尋找 Reachy Mini 的 USB 序列埠."""
    return [
        p.device
        for p in serial.tools.list_ports.comports()
        if f"USB VID:PID={vid}:{pid}" in p.hwid.upper()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="比對多顆馬達的暫存器設定（唯讀）")
    parser.add_argument(
        "--ids", type=str, default="11,12,13,14,15,16", help="要比對的馬達 ID，逗號分隔"
    )
    parser.add_argument("--port", type=str, default=None, help="序列埠 (例: COM7)")
    parser.add_argument("--baudrate", type=int, default=1000000, help="波特率")
    args = parser.parse_args()

    try:
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
    except ValueError:
        print("❌ --ids 格式錯誤，請用如 11,12,13")
        return

    if args.port:
        port = args.port
    else:
        found = find_serial_ports()
        if not found:
            print("❌ 找不到 Reachy Mini 的 USB COM 埠，請用 --port 指定。")
            return
        port = found[0]

    print(f"🔍 讀取 {port} @ {args.baudrate}，比對 ID: {ids}")
    print("（唯讀：不寫入、不移動馬達）\n")

    try:
        controller = Xl330PyController(port, baudrate=args.baudrate, timeout=0.05)
    except Exception as e:
        print(f"❌ 無法開啟序列埠 {port}: {e}")
        print("   → 最常見原因：daemon / desktop app 或另一個測試程式正佔用此埠。")
        return

    # ---------- 讀取 ----------
    table: dict[int, dict[str, str]] = {}
    for mid in ids:
        try:
            if not controller.ping(mid):
                print(f"⚠️ ID {mid} ping 失敗，跳過。")
                continue
        except Exception as e:
            print(f"⚠️ ID {mid} ping 例外: {e}，跳過。")
            continue

        row: dict[str, str] = {}
        for title, reg, _ in REGISTERS:
            try:
                row[title] = str(getattr(controller, "read_" + reg)(mid)[0])
            except Exception:
                row[title] = "ERR"
        table[mid] = row

    if not table:
        print("❌ 沒有讀到任何馬達。")
        return

    # ---------- 找出與多數不同的值 ----------
    majority: dict[str, str] = {}
    for title, _, compare in REGISTERS:
        if not compare:
            continue
        vals = [row[title] for row in table.values() if row[title] != "ERR"]
        if not vals:
            continue
        common, count = Counter(vals).most_common(1)[0]
        # 至少要有過半數才算得上「多數」
        if count > len(vals) / 2:
            majority[title] = common

    # ---------- 輸出 ----------
    titles = [t for t, _, _ in REGISTERS]
    widths = {
        t: max(len(t), 8, *(len(table[m][t]) for m in table)) + 1 for t in titles
    }

    header = f"{'ID':>4} {'名稱':<14}" + "".join(f"{t:>{widths[t]}}" for t in titles)
    print(header)
    print("-" * len(header))

    outliers: list[tuple[int, str, str, str]] = []
    for mid, row in table.items():
        cells = ""
        for t in titles:
            v = row[t]
            if t in majority and v != "ERR" and v != majority[t]:
                cells += f"{'*' + v:>{widths[t]}}"
                outliers.append((mid, t, v, majority[t]))
            else:
                cells += f"{v:>{widths[t]}}"
        print(f"{mid:>4} {MOTOR_NAMES.get(mid, '?'):<14}{cells}")

    # ---------- 結論 ----------
    print("\n" + "=" * 70)
    if outliers:
        print("🚨 以下設定與多數馬達不同（上表以 * 標記）：\n")
        for mid, title, val, maj in outliers:
            print(
                f"  ID {mid} [{MOTOR_NAMES.get(mid, '?')}] "
                f"{title}: {val}  ← 其他馬達為 {maj}"
            )
        print("\n→ 行為差異很可能來自這些設定，而非硬體損壞。")
    else:
        print("✅ 所有納入比對的設定完全一致。")
        print("   若行為仍有差異，才需要往機械或馬達本身的問題排查。")
    print("=" * 70)
    print("\n註：Homing / MinPos / MaxPos 每顆本來就不同（鏡像配置），不納入比對。")


if __name__ == "__main__":
    main()
