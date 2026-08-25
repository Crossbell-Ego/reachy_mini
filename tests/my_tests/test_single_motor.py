"""Reachy Mini 單獨馬達控制與測試工具.

用途：
    單獨對指定 ID (預設 15) 的馬達進行絕對/相對角度控制、開關扭力與即時監控。
    適合在「已拆除連桿、馬達無負載」的情況下量測單顆馬達的實際行程與健康狀態。

⚠️ 執行前務必先關閉 Reachy Mini daemon / desktop app，
   否則序列埠會被佔用（Windows 上 COM 埠不可共享）。

執行方式：
    python test_single_motor.py --id 15
    python test_single_motor.py --id 15 --port COM5 --speed 30
"""

import argparse
import math
import statistics
import sys
import time
from typing import Any, Callable

import serial.tools.list_ports
from rustypot import Xl330PyController

# 輸出被導向檔案或管線時，Windows 會退回 cp950，emoji 會觸發 UnicodeEncodeError。
# 直接在主控台執行不受影響，這裡只是讓 `> log.txt` 也不會中斷。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

TICKS_PER_TURN = 4096
DEFAULT_ZERO_TICK = 2048  # 換算基準推導失敗時的保底中心值

# 與 daemon 的 voltage_ok() 一致：低於此電壓的 Input Voltage Error 視為誤報
ALLOWED_MAX_VOLTAGE = 7.8

# 送出目標後，最多等多久讓馬達走完；以及取樣間隔
SETTLE_TIMEOUT = 2.5
SETTLE_INTERVAL = 0.05

# 硬體錯誤代碼解析 (Dynamixel XL330 Hardware Error Status, 暫存器 70)
ERROR_BITS = {
    0x01: "輸入電壓異常 (Input Voltage Error)",
    0x04: "馬達過熱 (Overheating Error)",
    0x08: "編碼器異常 (Motor Encoder Error)",
    0x10: "電氣/短路異常 (Electrical Shock Error)",
    0x20: "⚠️ 機構過載 (Overload Error - 卡死或受力過大)",
}

OPERATING_MODES = {
    0: "電流控制 (Current)",
    1: "速度控制 (Velocity)",
    3: "位置控制 (Position)",
    4: "多圈位置控制 (Extended Position)",
    5: "電流基礎位置控制 (Current-based Position)",
    16: "PWM 控制",
}


def decode_hardware_errors(error_byte: int, voltage: float | None = None) -> str:
    """將硬體錯誤位元組轉為中文說明.

    Reachy Mini 在正常工作電壓下就會掛起 Input Voltage Error 旗標，
    daemon 自己也把「電壓 <= ALLOWED_MAX_VOLTAGE」的這個位元當誤報濾掉
    (見 daemon/backend/robot/backend.py 的 voltage_ok)，這裡比照處理，
    避免把已知的假警報當成故障追。
    """
    if error_byte == 0:
        return "正常"

    hits = []
    for bit, desc in ERROR_BITS.items():
        if not error_byte & bit:
            continue
        if (
            bit == 0x01
            and voltage is not None
            and voltage <= ALLOWED_MAX_VOLTAGE
        ):
            hits.append(f"{desc}〔{voltage:.1f}V ≤ {ALLOWED_MAX_VOLTAGE}V，daemon 視為誤報〕")
        else:
            hits.append(desc)
    return ", ".join(hits) if hits else f"未知錯誤代碼 (0x{error_byte:02X})"


def decode_moving_status(value: int) -> str:
    """解析 Moving Status (暫存器 123)."""
    profiles = {0: "Step(無 profile)", 1: "Rectangular", 2: "Trapezoidal", 3: "Triangular"}
    parts = ["已到位" if value & 0x01 else "未到位"]
    if value & 0x02:
        parts.append("⚠️ Profile 仍進行中")
    parts.append(f"速度曲線={profiles.get((value >> 4) & 0x03, '?')}")
    return ", ".join(parts)


def find_serial_ports(vid: str = "1A86", pid: str = "55D3") -> list[str]:
    """嚴格比對 VID:PID 尋找 Reachy Mini 的 USB 序列埠.

    刻意不做「找不到就回傳第一個埠」的 fallback —— 那可能開到藍牙或其他裝置。
    """
    return [
        p.device
        for p in serial.tools.list_ports.comports()
        if f"USB VID:PID={vid}:{pid}" in p.hwid.upper()
    ]


def safe_read(fn: Callable[..., Any], *args: Any, default: Any = None) -> Any:
    """呼叫 rustypot 的 read_* 並取出第一個元素；失敗時回傳 default 而不中斷程式."""
    try:
        return fn(*args)[0]
    except Exception as e:
        print(f"⚠️ 讀取失敗 ({fn.__name__}): {e}")
        return default


def main() -> None:
    """解析參數、開啟序列埠，並確保結束時一定關閉扭力與釋放埠."""
    parser = argparse.ArgumentParser(description="Reachy Mini 單獨馬達控制與測試工具")
    parser.add_argument("--id", type=int, default=15, help="要測試的馬達 ID (預設: 15)")
    parser.add_argument(
        "--port", type=str, default=None, help="序列埠 (例: COM5)，不指定則自動偵測"
    )
    parser.add_argument(
        "--baudrate", type=int, default=1000000, help="波特率 (預設: 1000000)"
    )
    parser.add_argument(
        "--speed",
        type=int,
        default=40,
        help="Profile Velocity 原始值 (單位 0.229 rpm，0 = 全速)。預設 40 約 55 度/秒",
    )
    parser.add_argument(
        "--step", type=float, default=5.0, help="[+]/[-] 每次的相對步進角度 (預設: 5.0 度)"
    )
    parser.add_argument(
        "--no-limit-check",
        action="store_true",
        help="不擋下超出 EEPROM 限位的指令（仍會由馬達自行拒絕）",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="每次移動印出完整位置軌跡（診斷「走錯方向 / 爬行 / 卡住」用）",
    )
    args = parser.parse_args()

    target_id = args.id

    # ---------- 序列埠 ----------
    if args.port:
        port = args.port
    else:
        found = find_serial_ports()
        if not found:
            print("❌ 找不到 Reachy Mini 的 USB COM 埠 (VID:PID=1A86:55D3)。")
            print("   → 請確認 USB 已連接，或用 --port COM5 手動指定。")
            return
        if len(found) > 1:
            print(f"⚠️ 偵測到多個符合的埠: {found}，使用第一個。可用 --port 指定。")
        port = found[0]

    print("=" * 62)
    print(f"🔧 單獨馬達測試工具 - 目標馬達 ID: {target_id}")
    print(f"連接埠: {port} | 波特率: {args.baudrate}")
    print("=" * 62)

    try:
        controller = Xl330PyController(port, baudrate=args.baudrate, timeout=0.05)
    except Exception as e:
        print(f"❌ 無法開啟序列埠 {port}: {e}")
        print("   → 最常見原因：Reachy Mini daemon 或 desktop app 正佔用此埠，請先關閉後重試。")
        return

    try:
        run_session(controller, target_id, args)
    finally:
        try:
            controller.write_torque_enable(target_id, False)
            controller.write_led(target_id, 0)
            print("🔒 已安全關閉扭力並熄滅 LED。")
        except Exception:
            pass
        # Windows 上必須釋放 controller，否則短時間內重跑會開不了同一個 COM 埠
        try:
            del controller
        except Exception:
            pass
        time.sleep(0.05)


def run_session(
    controller: Xl330PyController, target_id: int, args: argparse.Namespace
) -> None:
    """連線後的互動主流程."""
    if not controller.ping(target_id):
        print(f"❌ 無法連線到馬達 ID {target_id}！請確認線路、電源與 ID 是否正確。")
        return

    # ---------- 初始狀態 ----------
    cur_rad = safe_read(controller.read_present_position, target_id, default=0.0)
    cur_deg = math.degrees(cur_rad)
    raw_pos = safe_read(controller.read_raw_present_position, target_id)
    temp = safe_read(controller.read_present_temperature, target_id, default=-1)
    volt_raw = safe_read(controller.read_present_input_voltage, target_id, default=0)
    current = safe_read(controller.read_present_current, target_id)
    err_raw = safe_read(controller.read_hardware_error_status, target_id, default=0)
    mode = safe_read(controller.read_operating_mode, target_id)
    homing = safe_read(controller.read_homing_offset, target_id)
    lo_tick = safe_read(controller.read_raw_min_position_limit, target_id)
    hi_tick = safe_read(controller.read_raw_max_position_limit, target_id)

    # 由「同一時刻讀到的 raw ticks 與 radians」反推換算基準，
    # 不去假設 rustypot 的零點落在哪個 tick 上。
    if raw_pos is not None:
        zero_tick = raw_pos - cur_deg * TICKS_PER_TURN / 360.0
    else:
        zero_tick = float(DEFAULT_ZERO_TICK)

    def tick_to_deg(t: int) -> float:
        return (t - zero_tick) * 360.0 / TICKS_PER_TURN

    lo_deg = tick_to_deg(lo_tick) if lo_tick is not None else None
    hi_deg = tick_to_deg(hi_tick) if hi_tick is not None else None

    print("\n📊 馬達初始狀態：")
    print(
        f"  • 當前角度  : {cur_deg:+.2f}° ({cur_rad:+.4f} rad)"
        + (f" | raw {raw_pos} ticks" if raw_pos is not None else "")
    )
    print(
        f"  • 工作電壓  : {volt_raw / 10.0:.1f} V | 溫度: {temp} °C"
        + (f" | 電流: {current}" if current is not None else "")
    )
    print(f"  • 運轉模式  : {mode} - {OPERATING_MODES.get(mode, '未知')}")
    print(f"  • Homing off: {homing} ticks")
    if raw_pos is not None and homing is not None:
        # Present Position = 實際編碼器位置 + Homing Offset
        # 拆掉連桿後這兩者差很多，分開列出可避免「看起來置中卻讀到大角度」的誤判
        actual_tick = raw_pos - homing
        neutral_tick = DEFAULT_ZERO_TICK - homing
        print(
            f"  • 實際編碼器: {actual_tick} ticks (馬達自身機械中心 = {DEFAULT_ZERO_TICK})"
            f" | 校正中立位對應 {neutral_tick} ticks"
        )
    if lo_tick is not None and hi_tick is not None:
        print(
            f"  • 位置限位  : {lo_tick} ~ {hi_tick} ticks"
            f"  →  {lo_deg:+.1f}° ~ {hi_deg:+.1f}°"
        )
    else:
        print("  • 位置限位  : 讀取失敗（將不做範圍檢查）")
    last_voltage = volt_raw / 10.0
    print(f"  • 硬體錯誤碼: 0x{err_raw:02X} ({decode_hardware_errors(err_raw, last_voltage)})")

    if mode != 3:
        print(f"\n⚠️ 此馬達不在位置控制模式 (mode={mode})，角度指令不會有預期效果。")

    if lo_deg is not None and hi_deg is not None and not (lo_deg <= cur_deg <= hi_deg):
        print(f"\n🚨 目前位置 {cur_deg:+.1f}° 落在 EEPROM 限位之外！")
        print("   Present Position 量的是「離校正中立位多遠」，不是「離馬達機械中心多遠」。")
        print("   → 連桿拆除後，曲柄外觀看起來置中，不代表編碼器讀數為 0。")
        print("   → 輸入 0 可讓馬達回到校正中立位；")
        print("     這會是一次大角度移動，務必先確認連桿已拆除且行程上沒有阻擋。")

    # ---------- 速度限制 ----------
    # 注意：Profile Velocity 是 RAM 暫存器，寫入後會一直留著直到斷電或 reboot。
    # 因此 --speed 0 必須「明確寫入 0」來取消速度曲線，不能只是跳過不寫，
    # 否則會沿用上一次執行留下的值。
    try:
        controller.write_profile_velocity(target_id, args.speed)
        if args.speed > 0:
            print(f"\n🐢 Profile Velocity 已設為 {args.speed}（實際走速在每次移動後量測回報）。")
        else:
            print("\n⚡ Profile Velocity 已設為 0（取消速度曲線，全速移動）。")
            print("   這是其他馬達的原廠預設值，也是 daemon 平常的運作條件。")
    except Exception as e:
        print(f"\n⚠️ 設定 Profile Velocity 失敗: {e}")

    # 回讀確認寫入真的生效。Profile Velocity 會被 Velocity Limit(44) 壓住，要一起看。
    pv = safe_read(controller.read_profile_velocity, target_id)
    pa = safe_read(controller.read_profile_acceleration, target_id)
    dm = safe_read(controller.read_drive_mode, target_id)
    mt = safe_read(controller.read_moving_threshold, target_id)
    vl = safe_read(controller.read_velocity_limit, target_id)
    print(f"   回讀 Profile Velocity={pv} / Acceleration={pa} / Velocity Limit={vl}")
    if dm is not None:
        print(
            f"   Drive Mode=0x{dm:02X}"
            f"（{'反轉 Reverse' if dm & 0x01 else '正轉 Normal'}）"
            f" | Moving Threshold={mt}"
        )
    else:
        print(f"   Moving Threshold={mt}")

    # ---------- LED 識別 ----------
    print("💡 閃爍馬達 LED 2 次確認實體位置...")
    for _ in range(2):
        try:
            controller.write_led(target_id, 1)
            time.sleep(0.15)
            controller.write_led(target_id, 0)
            time.sleep(0.15)
        except Exception:
            break

    # ---------- 操作說明 ----------
    print("\n" + "-" * 62)
    print("🎮 操作指令說明（單位：度數 °）：")
    print("  [e]    開啟扭力 (Torque ON)")
    print("  [d]    釋放扭力 (Torque OFF)")
    print(f"  [+]    相對旋轉 +{args.step:g}°  （步進值可用 --step 調整）")
    print(f"  [-]    相對旋轉 -{args.step:g}°")
    print("  [數字] 直接輸入「絕對目標角度」 (例: 65, -30, 0)")
    print("  [c]    Reboot 馬達以清除硬體錯誤標記（扭力會回到 OFF）")
    print("  [r]    重新讀取完整狀態")
    print("  [q]    安全退出")
    print("-" * 62)

    torque_enabled = False
    last_goal_deg: float | None = None

    def clamp_target(deg: float) -> float:
        """把目標截斷到 EEPROM 限位內.

        刻意採「截斷」而非「拒絕」：馬達若已停在限位之外（例如連桿拆除後被手動轉開），
        拒絕送出會讓使用者沒辦法把它開回有效範圍內。
        """
        if args.no_limit_check or lo_deg is None or hi_deg is None:
            return deg
        clamped = min(max(deg, lo_deg), hi_deg)
        if abs(clamped - deg) > 1e-6:
            print(
                f"⚠️ 目標 {deg:+.1f}° 超出 EEPROM 限位 "
                f"({lo_deg:+.1f}° ~ {hi_deg:+.1f}°)，已截斷為 {clamped:+.1f}°。"
            )
            print("   → 若要原樣送出請加 --no-limit-check（馬達仍可能自行拒絕）。")
        return clamped

    def send_target_deg(target_deg: float, base_deg: float) -> None:
        nonlocal torque_enabled, last_goal_deg

        target_deg = clamp_target(target_deg)

        if not torque_enabled:
            try:
                controller.write_torque_enable(target_id, True)
                torque_enabled = True
                print("（已自動開啟扭力）")
            except Exception as e:
                print(f"❌ 開啟扭力失敗: {e}")
                return

        target_rad = math.radians(target_deg)
        try:
            controller.write_goal_position(target_id, target_rad)
        except Exception as e:
            print(f"❌ 寫入目標位置失敗: {e}")
            print("   → 可能是指令超出馬達限位、通訊逾時，或馬達處於錯誤狀態（試試 [c]）。")
            return

        last_goal_deg = target_deg

        # 等到「位置不再變化」才回報。
        # 刻意不用 read_moving（暫存器 122）：它只在速度超過 Moving Threshold 時才為 1，
        # 門檻設得高時馬達仍在爬就已回報 0，會被誤判成「已停止」。
        t0 = time.time()
        now_deg = base_deg
        prev_deg = base_deg
        settled = False
        peak_current = 0
        stable = 0
        trace: list[tuple[float, float, int]] = []

        while time.time() - t0 < SETTLE_TIMEOUT:
            time.sleep(SETTLE_INTERVAL)
            now_rad = safe_read(controller.read_present_position, target_id)
            if now_rad is None:
                print(f"👉 目標: {target_deg:+.1f}°（已送出，但位置回讀失敗）")
                return
            now_deg = math.degrees(now_rad)
            cur_now = safe_read(controller.read_present_current, target_id, default=0) or 0
            peak_current = max(peak_current, abs(cur_now))
            t_now = time.time() - t0
            trace.append((t_now, now_deg, cur_now))

            stable = stable + 1 if abs(now_deg - prev_deg) < 0.1 else 0
            prev_deg = now_deg
            # 至少觀察 0.2 秒，避免在 profile 還沒啟動前就判定為停止
            if stable >= 3 and t_now > 0.2:
                settled = True
                break
        elapsed = time.time() - t0

        if args.trace:
            print("   ── 位置軌跡 ──")
            for t_s, d, c in trace:
                print(f"     t={t_s:5.2f}s  {d:+8.2f}°  電流(raw)={c}")

        # 從軌跡量測實際走速。理論值依賴 Profile Velocity 的單位假設，
        # 且可能被 Velocity Limit(44) 壓住，所以一律以實測為準。
        speeds = [
            abs(trace[i][1] - trace[i - 1][1]) / (trace[i][0] - trace[i - 1][0])
            for i in range(1, len(trace))
            if trace[i][0] > trace[i - 1][0]
            and abs(trace[i][1] - trace[i - 1][1]) > 0.05
        ]
        measured_speed = statistics.median(speeds) if speeds else None

        # 回讀馬達實際接受的目標值：可分辨「指令被限位截斷」與「推不到位」
        goal_rad = safe_read(controller.read_goal_position, target_id)
        goal_tick = safe_read(controller.read_raw_goal_position, target_id)
        err_now = safe_read(controller.read_hardware_error_status, target_id, default=0)
        mstat = safe_read(controller.read_moving_status, target_id)

        print(
            f"👉 目標: {target_deg:+.1f}° ({target_rad:+.3f} rad) | "
            f"實際: {now_deg:+.1f}° | 變化: {now_deg - base_deg:+.1f}° | "
            f"誤差: {now_deg - target_deg:+.1f}°"
        )
        print(
            f"   耗時 {elapsed:.2f}s {'(已停止)' if settled else '(⏱ 逾時仍在動)'}"
            + (f" | 馬達接受的目標: {math.degrees(goal_rad):+.1f}°" if goal_rad is not None else "")
            + (f" ({goal_tick} ticks)" if goal_tick is not None else "")
            + f" | 峰值電流(raw): {peak_current}"
            + (f" | 實測走速: {measured_speed:.1f}°/s" if measured_speed else "")
        )
        if mstat is not None:
            print(f"   MovingStatus: 0x{mstat:02X} → {decode_moving_status(mstat)}")

        if err_now:
            print(
                f"   ⚠️ 硬體錯誤碼 0x{err_now:02X}: "
                f"{decode_hardware_errors(err_now, last_voltage)}"
            )

        # 誤差偏大時，用回讀值指出最可能的原因
        if abs(now_deg - target_deg) > 3.0:
            if goal_rad is not None and abs(math.degrees(goal_rad) - target_deg) > 1.0:
                print(
                    f"   ⚠️ 指令被馬達修改：送出 {target_deg:+.1f}°，"
                    f"馬達實際採用 {math.degrees(goal_rad):+.1f}° → 撞到 EEPROM 位置限位。"
                )
            elif not settled:
                print("   ⚠️ 逾時仍未停止：Profile Velocity 可能過低，或負載過大導致爬行。")
            elif peak_current == 0:
                print("   ⚠️ 已停止但電流為 0 且未到位：扭力可能未真正生效，或馬達處於錯誤狀態（試試 [c]）。")
            else:
                print("   ⚠️ 已停止但未到位：撞到機械止點，或 P 增益不足以克服靜摩擦。")

    # ---------- 主迴圈 ----------
    while True:
        cur_rad = safe_read(controller.read_present_position, target_id)
        if cur_rad is None:
            print("⚠️ 位置讀取失敗，1 秒後重試（連續失敗請檢查線路，或是否有其他程式佔用序列埠）。")
            time.sleep(1.0)
            continue
        cur_deg = math.degrees(cur_rad)

        prompt = (
            f"\n[ID {target_id} | 目前 {cur_deg:+.1f}° | "
            f"扭力 {'開啟' if torque_enabled else '關閉'}] 請輸入指令 > "
        )
        try:
            cmd = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print("\n正在安全退出...")
            break

        if not cmd:
            continue

        cmd_lower = cmd.lower()

        if cmd_lower == "q":
            print("正在安全退出...")
            break

        elif cmd_lower == "e":
            try:
                controller.write_torque_enable(target_id, True)
                torque_enabled = True
                last_goal_deg = None
                print("✅ 扭力已開啟 (Torque ON - 馬達鎖定出力)")
            except Exception as e:
                print(f"❌ 開啟扭力失敗: {e}")

        elif cmd_lower == "d":
            try:
                controller.write_torque_enable(target_id, False)
                torque_enabled = False
                last_goal_deg = None
                print("🔓 扭力已釋放 (Torque OFF - 馬達放鬆，可用手轉動)")
            except Exception as e:
                print(f"❌ 釋放扭力失敗: {e}")

        elif cmd_lower == "c":
            # 注意：Dynamixel 的 Hardware Error Status 無法靠 torque off/on 清除，
            # 過載等錯誤必須送 Reboot 指令。
            print("正在 reboot 馬達以清除硬體錯誤標記...")
            try:
                controller.reboot(target_id)
            except Exception as e:
                print(f"❌ Reboot 失敗: {e}")
                continue
            torque_enabled = False
            last_goal_deg = None
            time.sleep(0.6)  # 等馬達重新開機
            err = safe_read(controller.read_hardware_error_status, target_id, default=0)
            print(
                f"重置完成，當前錯誤碼: 0x{err:02X} "
                f"({decode_hardware_errors(err, last_voltage)})"
            )
            print("（reboot 後扭力回到 OFF、Profile Velocity 回復預設；如需放慢請重新執行本程式）")

        elif cmd_lower == "r":
            err = safe_read(controller.read_hardware_error_status, target_id, default=0)
            t = safe_read(controller.read_present_temperature, target_id, default=-1)
            v = safe_read(controller.read_present_input_voltage, target_id, default=0)
            raw = safe_read(controller.read_raw_present_position, target_id)
            last_voltage = v / 10.0
            print(
                f"📊 角度: {cur_deg:+.2f}° ({cur_rad:+.4f} rad)"
                + (f" | raw {raw} ticks" if raw is not None else "")
            )
            print(
                f"   溫度: {t} °C | 電壓: {last_voltage:.1f} V | "
                f"錯誤碼: 0x{err:02X} ({decode_hardware_errors(err, last_voltage)})"
            )

        elif cmd in ("+", "-"):
            # 相對移動。以「上一次的目標角」為基準累加，
            # 避免位置穩態誤差在連按時逐次累積造成漂移。
            delta = args.step if cmd == "+" else -args.step
            base = (
                last_goal_deg
                if (torque_enabled and last_goal_deg is not None)
                else cur_deg
            )
            send_target_deg(base + delta, cur_deg)

        else:
            # 任何可解析為數字的輸入一律視為「絕對角度」，包含 -30 這種負角度。
            try:
                target_deg = float(cmd)
            except ValueError:
                print("❌ 無效輸入！可用: e, d, c, r, q, +, - 或絕對角度 (如 60, -30, 0)")
                continue
            send_target_deg(target_deg, cur_deg)


if __name__ == "__main__":
    main()
