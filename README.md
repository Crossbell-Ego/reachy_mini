# Reachy Mini 🤖

> **關於這個 fork**
>
> 本專案衍生自 [pollen-robotics/reachy_mini](https://github.com/pollen-robotics/reachy_mini)，
> 原始著作權 © 2025 Pollen Robotics，以 Apache-2.0 授權釋出（見 [LICENSE](LICENSE)）。
>
> 相對於上游的修改：
>
> - 新增 `src/agent_system/` —— 雲端三段式對話 pipeline（STT → LLM → 工具 → TTS → 動作）
> - 新增 `tests/my_tests/` 與 `diagnose_motors.py` —— 針對實機的硬體診斷腳本
> - 新增 `CONTROL_CHAIN.md`、`EMOTIONS.md`、`MOTOR_REFERENCE.md` —— 控制鏈與硬體參考筆記
> - 將 `README.md`、`RELEASE.md`、`docs/notebooks/` 翻譯為繁體中文
>
> 逐檔的修改紀錄見 commit history。

[![Ask on HuggingChat](https://img.shields.io/badge/Read_the-Documentation-yellow?logo=huggingface&logoColor=yellow)](https://huggingface.co/docs/reachy_mini/)
[![Discord](https://img.shields.io/badge/Discord-Join_the_Community-7289DA?logo=discord&logoColor=white)](https://discord.gg/Y7FgMqHsub)

**Reachy Mini 是一款專為駭客與 AI 開發者打造的開源、富含表情的機器人。**

🛒 [**購買 Reachy Mini**](https://www.hf.co/reachy-mini/)

[![Reachy Mini Hello](/docs/assets/reachy_mini_hello.gif)](https://www.pollen-robotics.com/reachy-mini/)

## ⚡️ 組裝並啟動你自己的機器人

**選擇你的平台以閱讀專屬指南：**

| **🤖 Reachy Mini (無線版 Wireless)** | **🔌 Reachy Mini Lite** | **💻 模擬器 (Simulation)** |
| :---: | :---: | :---: |
| 完整的獨立自主體驗。<br>Raspberry Pi CM4 + 電池 + WiFi。 | 開發者版本。<br>透過 USB 連線到你的電腦。 | 無需硬體。<br>在 MuJoCo 中快速建立原型。 |
| 👉 [**前往無線版指南**](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/get_started) | 👉 [**前往 Lite 指南**](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini_lite/get_started) | 👉 [**前往模擬器指南**](https://huggingface.co/docs/reachy_mini/platforms/simulation/get_started) |



> ⚡ **實用技巧：** 安裝 [uv](https://docs.astral.sh/uv/getting-started/installation/) 可享有 10-100 倍更快的 App 安裝速度（系統會自動偵測，若無則回退至 `pip`）。

<br>

## 📱 應用程式與生態系統 (Apps & Ecosystem)

Reachy Mini 內建由 Hugging Face Spaces 支援的應用程式商店。你可以直接在機器人的儀表板上一鍵安裝這些 App！

* **🗣️ [對話 App (Conversation App)](https://huggingface.co/spaces/pollen-robotics/reachy_mini_conversation_app)：** 與 Reachy Mini 自然對話（由大型語言模型 LLM 驅動）。
* **📻 [廣播電台 (Radio)](https://huggingface.co/spaces/pollen-robotics/reachy_mini_radio)：** 和 Reachy Mini 一起收聽廣播！
* **👋 [手部追蹤 (Hand Tracker)](https://huggingface.co/spaces/pollen-robotics/hand_tracker_v2)：** 機器人即時跟隨你的手部動作。

👉 [**在 Hugging Face 上瀏覽所有應用程式**](https://hf.co/reachy-mini/#/apps)

<br>

## 🚀 開始使用 Reachy Mini SDK

### 使用者指南
* **[安裝指南 (Installation)](https://huggingface.co/docs/reachy_mini/SDK/installation)**：5 分鐘完成電腦開發環境設定
* **[快速上手指南 (Quickstart Guide)](https://huggingface.co/docs/reachy_mini/SDK/quickstart)**：在 Reachy Mini 上執行你的第一個行為
* **[JavaScript SDK 與網頁應用 (Web Apps)](https://huggingface.co/docs/reachy_mini/SDK/javascript-sdk)**：建立透過 WebRTC 控制機器人的瀏覽器應用 — **分享應用程式最簡單的方式**。
* **[AI 整合 (AI Integrations)](https://huggingface.co/docs/reachy_mini/SDK/integration)**：串接 LLM、開發 App 並發布至 Hugging Face。
* **[核心概念 (Core Concepts)](https://huggingface.co/docs/reachy_mini/SDK/core-concept)**：系統架構、座標系統與安全限制。
* **[Python SDK](https://huggingface.co/docs/reachy_mini/SDK/python-sdk)**：從 Python 完整控制機器人 — 支援腳本、控制迴圈與機載程式。
* 🤗[**與社群分享你的 App**](https://huggingface.co/blog/pollen-robotics/make-and-publish-your-reachy-mini-apps)
* 📂 [**瀏覽範例資料夾 (Examples)**](examples)
* 📓 [**教學筆記本 (Tutorial Notebooks)**](docs/notebooks)：循序漸進的 Jupyter 筆記本，涵蓋連線、動作、相機與音訊

### 🤖 AI 輔助開發

正在使用 AI 輔助編程工具（Claude Code、Codex、Copilot 等）嗎？你可以立即開始建立應用程式。將以下 Prompt 貼給你的 AI 代理：

> *I'd like to create a Reachy Mini app. Start by reading https://github.com/pollen-robotics/reachy_mini/blob/main/AGENTS.md*

這份 [**AGENTS.md**](AGENTS.md) 指南為 AI 代理提供了所需的一切資訊：SDK 使用模式、最佳實踐、範例應用與逐步技能指南。

### 啟動 Daemon 背景服務 (每次執行前必做)

在執行任何控制程式或 Jupyter Notebook 前，請先開啟獨立終端機啟動 Daemon：

1. **啟用虛擬環境**：
   ```powershell
   reachy_mini_env\Scripts\activate
   ```
2. **啟動 Daemon**：
   - **實體機器人**：
     ```powershell
     reachy-mini-daemon
     ```
   - **模擬器 (MuJoCo)**：
     ```powershell
     reachy-mini-daemon --sim
     ```

### 快速預覽
大多數 Reachy Mini 應用都是 **網頁 / JS App**：一個透過 WebRTC 從任何瀏覽器控制機器人的靜態網頁，使用者端完全零安裝 — 這是開發新應用的推薦途徑（請參閱 [JavaScript SDK 與網頁應用](https://huggingface.co/docs/reachy_mini/SDK/javascript-sdk) 指南）。

更傾向於使用 Python 開發機載即時控制迴圈？[安裝 SDK](https://huggingface.co/docs/reachy_mini/SDK/installation) 且喚醒機器人後，只需要**幾行程式碼**就能控制它：

```python
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

with ReachyMini() as mini:
    # 抬頭並傾斜
    mini.goto_target(
        head=create_head_pose(z=10, roll=15, degrees=True, mm=True),
        duration=1.0
    )
```

<br>

## 🛠 硬體總覽

Reachy Mini 機器人以套件形式販售，通常需要 **2 到 3 小時** 組裝。上方連結的各平台專屬資料夾中提供了詳細的逐步組裝指南。

* **Reachy Mini (無線版 Wireless)：** 運行於機載電腦 (RPi CM4)，自主運作，內建 IMU。[查看規格](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/hardware)。
* **Reachy Mini Lite：** 運行於你的個人電腦，透過插座供電。[查看規格](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini_lite/hardware)。

<br>

## ❓ 疑難排解

遇到問題了嗎？👉 **[查閱疑難排解與常見問題指南](https://huggingface.co/docs/reachy_mini/troubleshooting)**

<br>

## 🤝 社群與貢獻

* **加入社群：** 加入 [Discord](https://discord.gg/2bAhWfXme9) 分享你與 Reachy 的精彩瞬間、一起開發應用並獲得支援。
* **發現錯誤？** 歡迎在此儲存庫提交 Issue。
* **貢獻指南：** 請閱讀我們的 [貢獻指南 (contributing guidelines)](docs/contributing.md)，了解如何貢獻程式碼、回報問題或提出新功能建議。


## 授權條款 (License)

本專案採用 Apache 2.0 授權條款。詳情請參閱 [LICENSE](LICENSE) 檔案。
硬體設計檔案採用創用 CC 姓名標示-相同方式分享-非商業性 (Creative Commons BY-SA-NC) 授權條款。
