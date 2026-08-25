# Reachy Mini 座標控制鏈架構筆記

> 本文件根據 repo 實際原始碼逐行驗證撰寫，所有行號皆對應本 commit 的程式碼。
> 驗證日期：2026-08-25

---

## 一、Controller UI 在哪裡？

6-DOF 座標控制器介面共有兩個入口，**兩者走的傳輸路徑不同**：

| 入口 | 傳輸方式 | 說明 |
| :--- | :--- | :--- |
| **Reachy Mini Desktop App** | HTTP / WebSocket（localhost） | 前端專案**不在本 repo**，是獨立的 desktop app 儲存庫 |
| **Web App（HF Space / Testbench）** | WebRTC DataChannel → JSON-RPC | 經由中央 signaling server 連到機器人 |

本 repo 的 `ts/` 目錄是 **JS SDK**（`ts/reachy-mini-sdk.ts`），走 WebRTC 路徑，不是 desktop app 的前端原始碼。

---

## 二、完整控制鏈（修正版）

```
[Controller UI]
   │
   ├─ 本機：HTTP POST /move/set_target   或   WS /move/ws/set_target
   └─ 遠端：WebRTC DataChannel → jsonrpc_relay.py → /ws/sdk
   ▼
[FastAPI Router · move.py]
   │  只做 is_move_running 檢查後轉發，**不做任何 clamp**
   │  backend.set_target(head=4x4 齊次矩陣, antennas, body_yaw)
   ▼
[Backend 共享狀態]
   │  target_head_pose 更新 + ik_required = True
   ╎
   ╎  ⚠️ 非同步交界：Router 只「寫入狀態」，不直接驅動硬體
   ╎
   ▼
[50 Hz 控制迴圈 · RobotBackend._update()]
   │  1. 把「上一輪」算好的 target_head_joint_positions 寫給馬達
   │  2. 讀取當前關節位置、更新運動學模型、head tracking
   │  3. if ik_required: 執行 IK
   │       → head tracking 姿態混合
   │       → speech wobbler offset 疊加
   │       → Rust IK 求解
   │       → 回傳 7 維向量；若含 NaN 則丟 ValueError
   ▼
[ReachyMiniPyControlLoop（Rust，內部使用 rustypot）]
   │  set_body_rotation(j[0])
   │  set_stewart_platform_position(j[1:])
   │  set_antennas_positions(...)   ← 不經過 IK
   ▼
[TTL 序列匯流排 @ 1,000,000 baud]
   ▼
[9 顆匯流排馬達]
```

---

## 三、各層核心程式與檔案位置

| 層級 | 核心模組 | 檔案位置 | 實際負責的工作 |
| :--- | :--- | :--- | :--- |
| **1. 前端 UI** | React / TS | 不在本 repo（desktop app 為獨立專案）；WebRTC SDK 在 `ts/` | 擷取滑桿數值，封裝為 `(x,y,z,roll,pitch,yaw)` + antennas + body_yaw |
| **2. 後端 Router** | FastAPI | [`src/reachy_mini/daemon/app/routers/move.py`](src/reachy_mini/daemon/app/routers/move.py) | 接收指令、檢查是否有 move 正在執行、轉發給 backend。**不做安全 clamp** |
| **3. 狀態層 + IK 觸發** | Backend 抽象層 | [`src/reachy_mini/daemon/backend/abstract.py`](src/reachy_mini/daemon/backend/abstract.py) | 保存目標姿態、`ik_required` 旗標、`update_target_head_joints_from_ik()` |
| **4. 運動學引擎** | `reachy_mini_rust_kinematics`（外部 pip 套件） | Python 包裝層在 [`src/reachy_mini/kinematics/`](src/reachy_mini/kinematics/) | 解析式 IK（Rust），FK 用 Newton 數值法 |
| **5. 控制迴圈 + 馬達通訊** | `reachy_mini_motor_controller` | [`src/reachy_mini/daemon/backend/robot/backend.py`](src/reachy_mini/daemon/backend/robot/backend.py) | 固定 50 Hz 迴圈，透過 Rust crate 寫入 TTL 匯流排 |

---

## 四、關鍵細節（容易寫錯的地方）

### 4.1 Router 沒有安全邊界防呆

[`move.py`](src/reachy_mini/daemon/app/routers/move.py) 的 `set_target` 端點（L215–231）只做 `is_move_running` 檢查後原封不動轉發，**沒有任何數值 clamp**。

實際的安全機制在另外兩處：

1. **IK 失敗攔截** — `abstract.py:581-583`
   ```python
   joints = self.head_kinematics.ik(pose, body_yaw=body_yaw)
   if joints is None or np.any(np.isnan(joints)):
       raise ValueError("WARNING: Collision detected or head pose not achievable!")
   ```
   在控制迴圈中此例外會被 catch 並以 0.5 秒節流的方式記錄警告（`backend.py:257-261`），機器人保持在上一個有效姿態。

2. **馬達硬體限位** — `src/reachy_mini/assets/config/hardware_config.yaml`
   每顆馬達有 `lower_limit` / `upper_limit`（以 raw tick 為單位，例：stewart_1 為 1502–2958）。

### 4.2 IK 不是由 Router 觸發，而是被 50 Hz 迴圈輪詢

Router → backend 只是設定狀態並打旗標：

- `abstract.py:604` — `set_target_head_pose()` 設 `ik_required = True`
- `abstract.py:621` — `set_target_body_yaw()` 設 `ik_required = True`
- `abstract.py:633` — `set_target_head_joint_positions()` 設 `ik_required = False`（直接給關節角，跳過 IK）

真正呼叫 IK 的是 `backend.py:253-259`：

```python
if self.ik_required:
    try:
        self.update_target_head_joints_from_ik(
            self.target_head_pose, self.target_body_yaw
        )
    except ValueError as e:
        log_throttling.by_time(self.logger, interval=0.5).warning(f"IK error: {e}")
```

**時序注意**：在 `_update()` 中，馬達寫入（L200–231）發生在 IK 計算（L253–259）**之前**。因此新算出的關節角要到**下一個 tick** 才會送到馬達，存在約 20 ms 的固有延遲。

### 4.3 控制迴圈頻率是固定 50 Hz

`backend.py:71`：
```python
self.control_loop_frequency = 50.0  # Hz
```
寫死，不可設定，不是 50–100 Hz 的範圍。

### 4.4 IK 輸出是 7 維，不是 6 維

回傳向量為 `[body_yaw, stewart_1 … stewart_6]`，backend 分兩路送出（`backend.py:206-209`）：

```python
self.c.set_stewart_platform_position(self.target_head_joint_positions[1:].tolist())
self.c.set_body_rotation(self.target_head_joint_positions[0])
```

**兩顆天線完全不經過 IK**，由 `set_antennas_positions()` 獨立設定（`backend.py:226`）。

### 4.5 有三種運動學引擎，Rust 只是其中之一

[`src/reachy_mini/kinematics/`](src/reachy_mini/kinematics/) 是 Python 包裝層，Rust crate 本身是外部 pip 依賴（`reachy-mini-rust-kinematics>=1.0.3`）：

| 引擎 | 檔案 | 說明 |
| :--- | :--- | :--- |
| `AnalyticalKinematics` | `analytical_kinematics.py` | **預設**。Rust bindings，IK 為解析法、FK 為 Newton 數值法 |
| `NNKinematics` | `nn_kinematics.py` | ONNX 神經網路，由 Placo 資料訓練而來 |
| `PlacoKinematics` | `placo_kinematics.py` | 需 `pip install reachy_mini[placo_kinematics]`；**Windows 不支援**。重力補償只在此引擎可用 |

引擎於 `abstract.py:195-211` 依 `kinematics_engine` 參數選擇。

### 4.6 rustypot 在 daemon 路徑上不是直接被 Python 呼叫

`backend.py:18` 只 import 了 `ReachyMiniPyControlLoop`。rustypot 是在 Rust crate 內部使用。

Python 端直接使用 rustypot 的 `Xl330PyController` 只出現在工具腳本：
- `src/reachy_mini/tools/scan_motors.py`
- `src/reachy_mini/tools/setup_motor.py`
- `diagnose_motors.py`（本機自訂腳本）

### 4.7 9 顆馬達不全是 Dynamixel XL330

來自 `hardware_config.yaml`（L56 起）：

| 名稱 | ID | operating_mode | 備註 |
| :--- | :--- | :--- | :--- |
| `body_rotation` | 10 | 3 | 程式碼註解指出目前為 **Feetech** 馬達，不支援扭矩控制 |
| `stewart_1` ~ `stewart_6` | 11–16 | 3 | XL330（硬體錯誤解碼參照 XL330-M288 手冊） |
| `right_antenna` | 17 | 3 | 註解指出天線扭矩控制同樣不支援 |
| `left_antenna` | 18 | 3 | 同上 |

相關註解位置：`backend.py:221`、`backend.py:229`、`backend.py:397`、`backend.py:447`。

匯流排波特率：`hardware_config.yaml:4` → `baudrate: 1000000`。

---

## 五、可用的 REST / WS 端點速查

| 端點 | 方法 | 用途 |
| :--- | :--- | :--- |
| `/move/set_target` | POST | 單次設定完整姿態（不阻塞、不插值） |
| `/move/ws/set_target` | WS | 串流式連續設定姿態（UI 滑桿即用此路徑） |
| `/move/goto` | POST | 帶時長與插值（預設 min-jerk）的移動，回傳 move UUID |
| `/move/play/wake_up` | POST | 播放喚醒動作 |
| `/move/play/goto_sleep` | POST | 播放睡眠動作 |
| `/move/stop` | POST | 依 UUID 取消進行中的 move |
| `/move/running` | GET | 列出進行中的 move UUID |
| `/move/ws/updates` | WS | 訂閱 move 狀態事件（started / completed / failed / cancelled） |
| `/move/ws/raw/write` | WS | 直接對序列埠寫入 raw packet 並取回回應 |
| `/kinematics/info` | GET | 查詢目前使用的引擎與碰撞檢查設定 |
| `/kinematics/urdf` | GET | 取得 URDF |

> 注意：`set_target` 與 `goto` 互斥 —— 有 move 正在執行時，`set_target` 會被忽略並回傳 `{"status": "ignored", "reason": "move_running"}`。

---

## 六、常見誤解對照表

| 常見寫法 | 實際情況 |
| :--- | :--- |
| 路由檔是 `moves.py` | 是 `move.py`（單數） |
| Router 做安全 clamp | Router 不做；限位在 IK 失敗攔截 + 馬達 config 限位 |
| Router 直接呼叫 IK | Router 只設旗標，IK 在 50 Hz 迴圈中執行 |
| 頻率 50~100 Hz | 固定 50 Hz（寫死） |
| IK 輸出 6 顆馬達角度 | 輸出 7 維：body_yaw + 6 連桿；天線另計 |
| `kinematics/` 是 Rust 核心 | 是 Python 包裝層，含 3 種引擎；Rust 是外部套件 |
| Python 用 rustypot 控馬達 | daemon 用 Rust 的 `ReachyMiniPyControlLoop`；rustypot 只在工具腳本 |
| 9 顆全是 XL330 | body_rotation 與天線為 Feetech（不支援扭矩控制） |
