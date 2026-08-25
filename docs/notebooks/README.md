# 📚 Reachy Mini 教學筆記本 (Notebooks)

歡迎使用 Reachy Mini 教學筆記本！這些互動式 Jupyter Notebook 專為**循序漸進的學習**而設計，從第一次連線到建立互動行為。你將透過實用、動手做的範例，學習如何使用 Reachy 的 SDK 並了解其功能。

每個筆記本均包含：

* ✅ **可執行的程式碼** — 適用於模擬器或實體機器人硬體
* 🎯 **明確的學習目標** — 清楚知道將會達成什麼
* 🛠️ **動手實作練習** — 實際練習所學內容
* 💡 **自包含的詳細說明** — 無需在多份文件之間頻繁切換
* ⚠️ **安全提醒事項** — 正確的使用規範指南
---
## 環境需求
要執行這些筆記本，請確保你的 Python 環境已安裝 Reachy Mini SDK 與 Jupyter。
- **Reachy Mini SDK** — 依照 [安裝指南](https://huggingface.co/docs/reachy_mini/SDK/installation) 安裝 SDK。
- **Jupyter** — 執行筆記本所需的 Jupyter 環境。請使用以下指令安裝：
```bash
pip install notebook
```

此外，你還需要透過 Reachy Mini Control 啟動並執行 Reachy Mini 的 Daemon。請參考 [安裝指南](https://huggingface.co/docs/reachy_mini/platforms/reachy_mini/usage#2-installation)。


<details>
<summary><strong>若系統提示需要安裝 <code>ipykernel</code></strong></summary>

如果在啟動筆記本時看到有關安裝 `ipykernel` 的錯誤或提示，表示你的 Python 環境缺少 Jupyter 核心套件。你可以使用以下指令安裝：

```bash
pip install ipykernel
python -m ipykernel install --user --name mini --display-name "Python (mini)"
```

安裝完成後，請重啟 Jupyter 伺服器並再次嘗試打開筆記本。

如果你使用多個 Python 環境，請確保 Jupyter 與 Reachy Mini SDK 運行在同一個環境中。

</details>

## 📘 可用的教學筆記本

### **Notebook 0 — 第一次連線與移動**
**預計時間：** ~20 分鐘 | **難易度：** 初學者

學習連線到 Reachy Mini 以及控制其動作的基礎知識。

**你將學到：**
* 🔌 連線到 Reachy Mini（兩種連線模式）
* 🤖 了解 Reachy 的部件（頭部、天線）
* 🎯 使用 `goto_target()` 做出第一個動作
* 📐 設定頭部姿勢並控制天線
* ⏱️ 使用 duration（持續時間）實現平滑動作

**涵蓋主題：** 連線模式、頭部姿勢、天線、`goto_target()`、`set_target()`

---

### **Notebook 1 — 基礎多媒體：相機與音訊**
**預計時間：** ~20 分鐘 | **難易度：** 初學者

讓 Reachy 看得見、聽得到！學習擷取影像、錄製音訊與播放聲音。

**你將學到：**
* 📸 從相機擷取影像
* 🎬 顯示視訊影格
* 🎤 從麥克風陣列錄製音訊
* 🔊 透過揚聲器播放聲音
* 💾 儲存與載入多媒體檔案
* 🤖 結合多媒體與動作以建立互動行為

**涵蓋主題：** 相機存取、影像擷取、音訊錄製/播放、即時音訊處理、多媒體 + 動作

---

### ❓ 疑難排解

如果在探索筆記本時遇到任何問題，請參閱 **[疑難排解與常見問題指南](https://github.com/pollen-robotics/reachy_mini/blob/main/docs/troubleshooting.md)**