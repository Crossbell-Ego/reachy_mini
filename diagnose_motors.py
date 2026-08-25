"""Reachy Mini 馬達即時診斷與錯誤檢測工具 (不需拆機).

執行方式：
    python diagnose_motors.py
"""

import time

import serial.tools.list_ports
from rustypot import Xl330PyController

# 馬達 ID 與名稱對照
MOTOR_NAMES = {
    10: "底座旋轉 (body_rotation)",
    11: "頭部馬達 1 (stewart_1)",
    12: "頭部馬達 2 (stewart_2)",
    13: "頭部馬達 3 (stewart_3)",
    14: "頭部馬達 4 (stewart_4)",
    15: "頭部馬達 5 (stewart_5)",
    16: "頭部馬達 6 (stewart_6)",
    17: "右天線 (right_antenna)",
    18: "左天線 (left_antenna)",
}

# 硬體錯誤代碼解析 (Dynamixel XL330 Hardware Error Status Register 70)
ERROR_BITS = {
    0x01: "輸入電壓異常 (Input Voltage Error)",
    0x04: "馬達過熱 (Overheating Error)",
    0x08: "編碼器異常 (Motor Encoder Error)",
    0x10: "電氣/短路異常 (Electrical Shock Error)",
    0x20: "⚠️ 機構過載 (Overload Error - 卡死或受力過大)",
}


def find_serial_port() -> str | None:
    """自動尋找 Reachy Mini 的 USB 序列埠 (VID:PID = 1a86:55d3)."""
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if "1A86:55D3" in p.hwid.upper() or "USB-SERIAL" in p.description.upper():
            return p.device
    if ports:
        return ports[0].device
    return None


def decode_hardware_errors(error_byte: int) -> list[str]:
    """將錯誤狀態位元組轉為中文說明."""
    if error_byte == 0:
        return ["無錯誤 (正常)"]
    errors = []
    for bit, desc in ERROR_BITS.items():
        if error_byte & bit:
            errors.append(desc)
    if not errors:
        errors.append(f"未知錯誤代碼 (0x{error_byte:02X})")
    return errors


def main():
    """逐一輪詢每顆馬達，印出溫度、電壓、位置與硬體錯誤旗標."""
    port = find_serial_port()
    if not port:
        print("❌ 找不到 Reachy Mini 的 USB COM 埠，請確認 USB 已連接！")
        return

    baudrate = 1000000
    print("=" * 65)
    print(f"🤖 Reachy Mini 馬達診斷程式 (序列埠: {port}, 波特率: {baudrate})")
    print("=" * 65)

    controller = Xl330PyController(port, baudrate=baudrate, timeout=0.03)

    print(
        f"\n{'ID':<4} {'名稱':<24} {'連線':<6} {'溫度(°C)':<8} {'電壓(V)':<8} {'當前位置':<10} {'硬體狀態/錯誤'}"
    )
    print("-" * 75)

    faulty_motors = []

    for motor_id, name in MOTOR_NAMES.items():
        try:
            if not controller.ping(motor_id):
                print(f"{motor_id:<4} {name:<24} ❌ 斷線")
                continue

            # 讀取溫度、電壓、位置
            temp = controller.read_present_temperature(motor_id)[0]
            voltage = controller.read_present_input_voltage(motor_id)[0] / 10.0
            pos = controller.read_present_position(motor_id)[0]

            # 嘗試讀取硬體錯誤碼 (暫存器 70)
            try:
                err_raw = controller.read_hardware_error_status(motor_id)[0]
            except Exception:
                err_raw = 0

            error_list = decode_hardware_errors(err_raw)
            status_str = ", ".join(error_list)

            # 判斷是否異常
            is_faulty = err_raw != 0
            flag = "⚠️" if is_faulty else "✅"

            print(
                f"{motor_id:<4} {name:<24} {flag} 正常   {temp:<8} {voltage:<8.1f} {pos:<10} {status_str}"
            )

            if is_faulty:
                faulty_motors.append((motor_id, name, status_str))

        except Exception as e:
            print(f"{motor_id:<4} {name:<24} ⚠️ 讀取失敗: {e}")

    print("\n" + "=" * 65)
    if faulty_motors:
        print("🚨 檢測到異常的馬達：")
        for m_id, m_name, err in faulty_motors:
            print(f"  👉 ID {m_id} [{m_name}]: {err}")
    else:
        print("✅ 所有馬達硬體狀態暫存器皆回報正常！")
    print("=" * 65)

    # 燈光識別測試：讓使用者確認哪顆馬達是哪顆
    print("\n💡 執行 LED 閃爍識別測試 (依序點亮馬達 LED 1 秒)...")
    for motor_id in MOTOR_NAMES.keys():
        try:
            controller.write_led(motor_id, 1)
            time.sleep(0.3)
            controller.write_led(motor_id, 0)
        except Exception:
            pass
    print("✨ 診斷完成。")


if __name__ == "__main__":
    main()
