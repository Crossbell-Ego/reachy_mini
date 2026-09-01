# 快速啟動指令

## 0. 換一台電腦的第一次安裝

需要的系統層前置只有三樣：**git**、**uv**、以及 uv 會自己抓的 **Python 3.12**
（`pyproject.toml` 寫 `>=3.11`，這台實測跑 3.12）。GStreamer 不用另外裝——
Windows / macOS 走 pip 上的 `gstreamer-bundle`，是 SDK 的相依，裝 SDK 就有；
Linux 則改吃系統的 GStreamer + `PyGObject`（同樣寫在 `pyproject.toml` 裡）。

```bash
git clone <這個 repo>            # 或直接複製整個資料夾（venv 不用複製）
cd reachy_mini

uv venv reachy_mini_env --python 3.12
reachy_mini_env\Scripts\activate

uv pip install -e .                                  # SDK 本體 + daemon（吃 repo 原始碼）
uv pip install -r src/agent_system/requirements.txt  # agent 控制台的額外相依

copy .env.example .env                               # 再把金鑰填進去
```

一個環境就夠，**兩件事都裝在同一個 venv 裡**：`-e .` 讓 daemon 直接跑 repo 的
`src/reachy_mini`，agent_system 也 import 到同一份程式碼，不會有版本各走各的。

選配（要用才裝）：

```bash
uv pip install -e ".[mujoco]"    # daemon --sim 的 MuJoCo 模擬
uv pip install -e ".[examples]"  # examples/ 的 pynput、soundfile、opencv
uv pip install --group dev       # pytest / ruff / mypy，只有要改 SDK 本體才需要
uv pip install sounddevice       # 只有 tests/my_tests/test_microphone.py 用得到
uv pip install ipykernel         # 只有要在 VS Code 跑 docs/ 的 notebook 才需要
```

**不會跟著版控過去、要在新機器自己生的東西：**

| 東西 | 怎麼來 |
| --- | --- |
| `.env` | 照 `.env.example` 複製一份填金鑰 |
| `src/agent_system/profiles/` | web_ui 第一次跑會自己建，是私人的角色與長期記憶 |
| `src/agent_system/calendar.json` | 私人行程；沒有這個檔 calendar 工具會當空的行事曆跑 |
| `reachy_mini_env/` `.venv/` | 用上面的指令重建，不要複製舊機器的 |

## 1. 每次開新終端機一定要先做

```bash
reachy_mini_env\Scripts\activate
```

所有安裝套件都要在這個虛擬環境裡（`uv pip install <package>`），不要裝到全域 Python。

金鑰放在 repo 根目錄的 `.env`：

```
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
OPENAI_BASE_URL=...   # 選填
```

> ⚠️ 這個 repo 同時是個 uv 專案，所以 `uv run <指令>` **會忽略你 activate 的
> `reachy_mini_env`、改用 `.venv`**（uv 自己會印一行 warning）。要跑在 activate
> 的環境就直接下指令，或加 `uv run --active`。

## 2. 啟動 daemon（機器人的控制核心，其他一切都要先連上它）

```bash
reachy-mini-daemon --mockup-sim     # 沒有硬體、沒裝 MuJoCo 時乾跑
reachy-mini-daemon --sim            # MuJoCo 模擬（要先裝 [mujoco] extra）
reachy-mini-daemon                  # 接真的機器人（自動找序列埠）
```

常用參數：`--fastapi-port`、`--log-level DEBUG`、
`--kinematics-engine {AnalyticalKinematics,Placo,NN}`、`--check-collision`。

## 3. 瀏覽器控制台（角色、工具、長期記憶、對話 — cloud_voice_chat 的網頁介面）

daemon 要先跑起來，再開這個：

```bash
python src/agent_system/web_ui.py                        # http://127.0.0.1:8800
python src/agent_system/web_ui.py --no-robot              # 沒接機器人時，只跑AI agent
```
