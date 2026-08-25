# Reachy Mini 馬達參考手冊

> 本文件的數值全部取自 repo 實際設定檔與 XL330-M288 規格書，並在實機（COM7）上讀取驗證。
> 驗證日期：2026-08-25

---

## 一、馬達配置總覽

匯流排：**TTL 序列 @ 1,000,000 baud**（[hardware_config.yaml:4](src/reachy_mini/assets/config/hardware_config.yaml#L4)）

| ID | 名稱 | 角色 | 型號 |
| :--- | :--- | :--- | :--- |
| 10 | `body_rotation` | 底座旋轉 | Feetech（見下方註） |
| 11 | `stewart_1` | Stewart 平台連桿 1 | XL330-M288 |
| 12 | `stewart_2` | Stewart 平台連桿 2 | XL330-M288 |
| 13 | `stewart_3` | Stewart 平台連桿 3 | XL330-M288 |
| 14 | `stewart_4` | Stewart 平台連桿 4 | XL330-M288 |
| 15 | `stewart_5` | Stewart 平台連桿 5 | XL330-M288 |
| 16 | `stewart_6` | Stewart 平台連桿 6 | XL330-M288 |
| 17 | `right_antenna` | 右天線 | Feetech（見下方註） |
| 18 | `left_antenna` | 左天線 | Feetech（見下方註） |

> **註**：程式碼註解明確指出 body rotation 與天線目前是 **Feetech 馬達，不支援扭矩控制**
> （[backend.py:221](src/reachy_mini/daemon/backend/robot/backend.py#L221)、
> [backend.py:229](src/reachy_mini/daemon/backend/robot/backend.py#L229)、
> [backend.py:397](src/reachy_mini/daemon/backend/robot/backend.py#L397)）。
> 硬體錯誤解碼則參照 XL330-M288 手冊，對應 Stewart 平台的六顆馬達。

---

## 二、XL330-M288 規格

| 項目 | 值 |
| :--- | :--- |
| 解析度 | **4,096 pulse/rev**（0.088°/tick） |
| 位置控制模式範圍 | 0 ~ 4,095 ticks = **0 ~ 360°** |
| 多圈位置模式範圍 | −1,048,575 ~ 1,048,575（±256 圈） |
| Min/Max Position Limit 暫存器 | 0 ~ 4,095 |
| 工作電壓 | 3.7 ~ 6.0 V（**建議 5.0 V**） |
| 堵轉扭力 | 0.52 Nm @ 5.0 V, 1.47 A |

---

## 三、各馬達角度限位（重要）

以下 `lower_limit` / `upper_limit` 寫入馬達 EEPROM 的 Min/Max Position Limit，**由馬達韌體強制執行** ——
超出範圍的 Goal Position 會被馬達拒絕或截斷，與上位機是否檢查無關。

| 名稱 | ID | ticks | 角度範圍 | 總行程 | homing offset |
| :--- | ---: | :--- | :--- | ---: | ---: |
| `body_rotation` | 10 | 0 ~ 4095 | −180.0° ~ +179.9° | 359.9° | 0 |
| `stewart_1` | 11 | 1502 ~ 2958 | −48.0° ~ +80.0° | 128.0° | +1024 |
| `stewart_2` | 12 | 1138 ~ 2844 | −80.0° ~ +70.0° | 149.9° | −1024 |
| `stewart_3` | 13 | 1502 ~ 2958 | −48.0° ~ +80.0° | 128.0° | +1024 |
| `stewart_4` | 14 | 1138 ~ 2594 | −80.0° ~ +48.0° | 128.0° | −1024 |
| `stewart_5` | 15 | 1252 ~ 2958 | −70.0° ~ +80.0° | 149.9° | +1024 |
| `stewart_6` | 16 | 1138 ~ 2594 | −80.0° ~ +48.0° | 128.0° | −1024 |
| `right_antenna` | 17 | 0 ~ 4095 | −180.0° ~ +179.9° | 359.9° | 0 |
| `left_antenna` | 18 | 0 ~ 4095 | −180.0° ~ +179.9° | 359.9° | 0 |

**規律**：奇數編號（11/13/15，offset **+1024**）與偶數編號（12/14/16，offset **−1024**）是鏡像配置，
所以角度範圍左右相反。

### 這些限位是怎麼來的？

config 檔開頭的註解寫著 `# Limits measured on the robot (in degrees)` ——
**這些是「裝著連桿、在實機上量出來的機構極限」，再燒進 EEPROM**，不是馬達的能力極限。

因此：

- 拆掉連桿後，馬達物理上仍能轉滿 360°，但 **EEPROM 限位依然生效**
- 若要突破，必須改寫 `write_raw_min_position_limit` / `write_raw_max_position_limit`
- ⚠️ **不建議這麼做** —— 那是出廠校正資料，改壞了會讓 Stewart 平台在正常運動中撞到機構

---

## 四、三種「角度」不要搞混（最容易踩的坑）

Dynamixel 的位置關係是：

```
Present Position = 實際編碼器位置 + Homing Offset
```

於是同一顆馬達有三個不同的「中間」：

| 名稱 | 定義 | ID 16 的值 |
| :--- | :--- | :--- |
| **馬達自身機械中心** | 編碼器的 2048 tick | 2048 ticks |
| **校正中立位** | `Present Position = 0°` 的位置 | 編碼器 3072 ticks |
| **Present Position** | 「離校正中立位多遠」 | 讀數即為答案 |

### 換算公式

```
角度(°)  = (Present tick − 2048) × 360 / 4096
Present tick = 實際編碼器 tick + Homing Offset
校正中立位對應的編碼器 tick = 2048 − Homing Offset
```

### 實例：ID 16 讀到 −87° 但外觀看起來置中

```
Present Position  = −87°  →  1059 ticks
Homing Offset     = −1024 ticks
實際編碼器位置     = 1059 − (−1024) = 2083 ticks   ← 幾乎正好是機械中心 2048
校正中立位對應     = 2048 − (−1024) = 3072 ticks
```

**結論**：曲柄停在「馬達自身的機械中心」，而校正中立位在 90° 之外，兩者差約 87°。

> ⚠️ **連桿拆除後，曲柄外觀「看起來置中」完全不代表 Present Position 為 0。**
> 曲柄一旦脫離連桿約束、在扭力關閉時被手轉過，或從花鍵上拆下重裝，就會落到任意角度。
> 要回到校正中立位，送出目標角度 `0` 即可（**務必先確認連桿已拆除、行程無阻擋**）。

---

## 五、硬體錯誤碼

Hardware Error Status（暫存器 70）：

| 位元 | 值 | 意義 |
| :--- | :--- | :--- |
| 0 | 0x01 | 輸入電壓異常 (Input Voltage Error) |
| 2 | 0x04 | 馬達過熱 (Overheating Error) |
| 3 | 0x08 | 編碼器異常 (Motor Encoder Error) |
| 4 | 0x10 | 電氣/短路異常 (Electrical Shock Error) |
| 5 | 0x20 | 機構過載 (Overload Error — 卡死或受力過大) |

### ⚠️ `0x01` 輸入電壓異常是預期行為，不需處理

實測匯流排電壓為 **7.6 V**，而規格書標稱上限是 6.0 V。這是 Pollen 的刻意設計，有兩個證據：

1. **daemon 主動濾掉這個旗標** —— `voltage_ok()` 把上限訂在 **7.8 V**，低於此值就把
   Input Voltage Error 從錯誤清單移除（[backend.py:701-717](src/reachy_mini/daemon/backend/robot/backend.py#L701-L717)）
2. **關機遮罩刻意排除它** —— config 的 `shutdown_error: 52` = `0b110100`
   = bit 2(過熱) + bit 4(電擊) + bit 5(過載)，**bit 0（輸入電壓）不在其中**，
   所以馬達不會因電壓旗標而停機

### 清除錯誤旗標

**扭力 off/on 清不掉硬體錯誤。** 過載等錯誤必須送 **Reboot 指令**（`controller.reboot(id)`）。
Reboot 後扭力回到 OFF，Profile Velocity 等 RAM 設定也會回復預設。

---

## 六、其他 EEPROM 設定

### config 檔的值

| 欄位 | Stewart (11–16) | body_rotation (10) | 天線 (17/18) |
| :--- | :--- | :--- | :--- |
| `operating_mode` | 3（位置控制） | 3 | 3 |
| `return_delay_time` | 0 | 0 | 0 |
| `shutdown_error` | 52 | 52 | 52 |
| `pid` (P, I, D) | 300, 0, 0 | 200, 0, 0 | 200, 0, 0 |

### 實機 EEPROM 實測值（ID 11–16，六顆完全一致）

| 暫存器 | 值 |
| :--- | :--- |
| Position P / I / D Gain | **400** / 0 / 0 |
| Velocity Limit | 445 |
| Profile Velocity | **0**（原廠預設＝不套用速度曲線） |
| Profile Acceleration | 0 |
| PWM Limit | 885 |
| Current Limit | 1750 |
| Drive Mode | 0（正轉 Normal） |
| Moving Threshold | 10 |
| Operating Mode / Shutdown / Return Delay | 3 / 52 / 0 |

> ⚠️ **實機 P Gain 為 400，config 檔寫的是 300** —— 兩者不一致。
> config 是 `setup_motor.py` 佈建時使用的來源，實機 EEPROM 可能由較新版本寫入。
> 六顆數值一致，所以不會造成馬達間的行為差異。

> 注意 **I 與 D 皆為 0**，是純 P 控制。在靜摩擦較大時會有明顯的穩態誤差，
> 這是「送出目標後停在差幾度的位置」的正常現象，不一定代表故障。

> **Profile Velocity 原廠預設為 0**，代表 daemon 平常運作時**不套用速度曲線**。
> 它是 RAM 暫存器，寫入後會保留到斷電或 reboot，所以測試工具寫過之後
> 會一直留著 —— 用 `--speed 0` 明確寫回 0 才能還原。

---

## 七、單顆馬達測試流程

使用 [test_single_motor.py](test_single_motor.py)。

### 前置條件

1. **必須先關閉 Reachy Mini daemon / desktop app** —— daemon 以 50 Hz 獨佔序列埠，
   Windows 上 COM 埠不可共享
2. 若要大角度移動，**先拆除該馬達的連桿**

### 執行

```bash
# 基本用法
python test_single_motor.py --id 16

# 指定埠、放慢速度、調整步進
python test_single_motor.py --id 16 --port COM7 --speed 30 --step 2
```

### 參數

| 參數 | 預設 | 說明 |
| :--- | :--- | :--- |
| `--id` | 15 | 目標馬達 ID |
| `--port` | 自動偵測 | 序列埠；自動偵測用 VID:PID = `1A86:55D3` 嚴格比對 |
| `--baudrate` | 1000000 | 波特率 |
| `--speed` | 40 | Profile Velocity 原始值；0 = 不設限。**實際走速在每次移動後由軌跡量測回報** |
| `--trace` | 關 | 印出每次移動的完整位置軌跡（時間 / 角度 / 電流） |
| `--step` | 5.0 | `+` / `-` 的相對步進角度 |
| `--no-limit-check` | 關 | 不截斷超出 EEPROM 限位的指令 |

### 互動指令

| 指令 | 動作 |
| :--- | :--- |
| `e` / `d` | 開啟 / 釋放扭力 |
| `+` / `-` | 相對移動 ±`--step` 度 |
| 數字 | 絕對目標角度（含負數，如 `-30`） |
| `c` | Reboot 馬達以清除硬體錯誤 |
| `r` | 重新讀取完整狀態 |
| `q` | 安全退出（自動關扭力） |

---

## 八、常見狀況排查

| 症狀 | 判讀 |
| :--- | :--- |
| `無法開啟序列埠: 存取被拒` | daemon / desktop app 仍佔用該埠，先關閉 |
| 錯誤碼 `0x01` | **預期行為**，見第五節，不需處理 |
| 讀數落在限位之外 | 曲柄脫離校正中立位，見第四節；送出 `0` 可復位 |
| 目標被截斷 | 超出 EEPROM 限位，工具會印出實際送出的值 |
| 停止但未到位、電流不為 0 | 撞到機械止點，或純 P 控制的穩態誤差（見第六節） |
| 停止但未到位、電流為 0 | 扭力未生效，或馬達在錯誤狀態 → 試 `c` |
| 逾時仍在移動 | Profile Velocity 過低，或負載過大導致爬行 |
| 單筆讀數異常（方向反了、速度超過物理上限） | 序列封包錯位，用 `--trace` 看整段軌跡再判斷，**不要單憑一個取樣點下結論** |

> **實測參考值**（ID 15，`--speed 40`，無負載）：走速約 **5.2°/s**，
> 10° 行程耗時約 1.9 秒，移動中電流 raw 約 −11，到位後歸 0，誤差 0.0°。
> 這組數字可當作判斷其他馬達是否正常的基準。
| `ping` 失敗 | 線路、電源，或 ID / 波特率不對 → 用 `scan_motors.py` 掃描 |

---

## 相關文件

- [CONTROL_CHAIN.md](CONTROL_CHAIN.md) — 從 UI 到馬達的完整控制鏈架構
- [src/reachy_mini/tools/scan_motors.py](src/reachy_mini/tools/scan_motors.py) — 掃描匯流排上的馬達 ID 與波特率
- [src/reachy_mini/tools/setup_motor.py](src/reachy_mini/tools/setup_motor.py) — 官方的馬達 ID / 波特率 / 限位設定工具
- [src/reachy_mini/assets/config/hardware_config.yaml](src/reachy_mini/assets/config/hardware_config.yaml) — 本文件所有限位數值的來源
