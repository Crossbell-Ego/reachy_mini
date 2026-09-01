r"""cloud_voice_chat 的瀏覽器介面：捏一個自己的 agent，然後跟它講話.

為什麼要一個本機伺服器
----------------------
單一 HTML 檔在瀏覽器裡做不到這條 pipeline 要的事：TTS 靠 edge-tts、動作要
透過 daemon 推馬達，都在本機這一側。所以這支程式把 cloud_voice_chat.py 的
東西原封不動接起來，外面包一層很薄的 FastAPI：

    瀏覽器 ──HTTP──> web_ui.py ──> cloud_voice_chat.run_turn() ──> daemon ──> 馬達
                                          └─> tools.py / edge-tts / 雲端 LLM

對話邏輯一行都沒有複製：run_turn() 就是 CLI 跑的那一個，回傳的 TurnResult
再轉成 JSON 給前端。CLI 與網頁因此不可能各講各的話。

一個 agent 由四樣東西組成
-------------------------
- **角色**：人設。跟輸出格式的規則分開存（SYSTEM_PROMPT 的 {role}），
  所以怎麼改都不會把 JSON 格式那段弄丟。
- **工具**：可以逐個關掉。關掉的不進 {tool_menu}，模型硬點也會被
  LLM.allowed_tools 擋下來。
- **長期記憶**：使用者自己填的私人事實，代入 {memory}；每條都能單獨停用。
- **Prompt 樣板**：上面三樣被塞進去的那張骨架，進階使用者才需要動。

這四樣加上語音與動作設定會存成一份 profile（profiles/<sid 的 hash>.json），
重開瀏覽器、重開伺服器都還在。**金鑰不在裡面**，只活在記憶體與使用者自己的
瀏覽器 localStorage。profiles/ 已進 .gitignore：裡頭是私人記憶，不該進版控。

Session
-------
只監聽 127.0.0.1，是給本機自己用的工具，不對外開放（這是 HTTP 不是 HTTPS，
金鑰明文經過網路，也就沒有分享出去的道理）。

每個瀏覽器仍自己帶一組 X-Session-Id（前端存在 localStorage），對應到這裡的一個
Session：金鑰、角色、記憶、對話歷史都是各自的——同一台機器上開兩個瀏覽器設定
不會互相蓋掉。機器人反過來只有一台，所有 Session 共用，用一把全域鎖擋住
併發——輪到別人講話時第二個請求拿 409。

金鑰預設讀 .env，使用者也可以在介面上蓋掉（只進記憶體，不寫檔）。

執行方式
--------
    python src/agent_system/web_ui.py                       # http://127.0.0.1:8800
    python src/agent_system/web_ui.py --no-robot            # 沒接機器人時乾跑
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import threading
import time
import uuid
import webbrowser
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, Optional

import httpx
import uvicorn

# 跟 cloud_voice_chat.py 一樣走「腳本模式」的同目錄 import。
from cloud_voice_chat import (
    DEFAULT_ROLE,
    EMOTION_MENU,
    LLM,
    SYSTEM_PROMPT,
    Config,
    Robot,
    _looks_set,
    move_label,
    run_turn,
)
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel
from tools import (
    TOOL_NEEDS_QUERY,
    TOOL_QUERY_HINT,
    TOOL_SPECS,
    VISION_TOOL,
    run_tool,
    tool_menu,
)

HERE = Path(__file__).resolve().parent
PROFILE_DIR = HERE / "profiles"

# 只綁 127.0.0.1，刻意不做成參數：這是 HTTP，金鑰與對話明文經過網路，
# 對外監聽沒有安全的做法。真的要分享，請自己在前面套一層 TLS 反向代理。
HOST = "127.0.0.1"

# SYSTEM_PROMPT 是給 str.format 吃的，JSON 範例裡的大括號都被跳脫成 {{ }}。
# 那是實作細節，不該漏到編輯框裡，所以先還原成人看的樣子；代入時也就不能再用
# format（會被 JSON 的括號絆倒），改用單純的字串取代。
DEFAULT_TEMPLATE = SYSTEM_PROMPT.replace("{{", "{").replace("}}", "}")

# 現成人設。選一個之後還是能自己改——這裡只是省得從空白開始寫。每一段都只寫
# 「這個角色是誰」，講話長度、繁體中文、JSON 格式那些由樣板的規則段負責，
# 所以換角色弄不壞 pipeline。
ROLE_PRESETS: list[dict[str, str]] = [
    {
        "name": "桌上小夥伴（預設）",
        "role": DEFAULT_ROLE,
    },
    {
        "name": "毒舌吐槽",
        "role": (
            "你是 Reachy Mini，一台放在桌上的小型桌面機器人，"
            "專門負責吐槽面前這個人。\n\n"
            "個性：嘴上不饒人，愛用反話跟誇張的比喻，但吐槽完還是會把正確答案講出來。"
            "分寸抓好：損的是事情，不是人。"
        ),
    },
    {
        "name": "傑尼龜",
        "role": (
            "你是傑尼龜，一隻住在桌上的水系寶可夢，正在跟面前的人講話。\n\n"
            "個性：像個五、六歲的小孩——好奇、容易興奮、想到什麼講什麼，"
            "只用最簡單的字，不會用成語或艱深的詞。\n"
            "口頭禪：每一句話的結尾都要加上「傑尼~傑尼~」，一句都不能漏。"
        ),
    },
]

# 內建的長期記憶範例：**預設關閉**。「長期記憶」講起來很抽象，給幾條具體的、
# 一勾起來回答就會變的例子，比解釋半天有用。刻意挑「查得到答案」的事實
# （庫存數量與單價、課表時段與教室），示範時開關一次就聽得出差別。
# 使用者可以照樣編輯或刪掉；刪掉的記在 Session.removed_examples，不會再冒出來。
EXAMPLE_MEMORIES: list[dict[str, Any]] = [
    {
        "id": "example-cat",
        "text": "我養了一隻叫麻糬的橘貓，牠很怕吵。",
        "enabled": False,
        "example": True,
    },
    {
        "id": "example-store",
        "text": (
            "我開的雜貨店目前的現貨與售價："
            "礦泉水 24 瓶、一瓶 20 元；泡麵 12 包、一包 35 元；"
            "雞蛋 8 盒、一盒 95 元；衛生紙 5 串、一串 120 元；"
            "醬油 3 瓶、一瓶 65 元。"
        ),
        "enabled": False,
        "example": True,
    },
    {
        "id": "example-timetable",
        "text": (
            "我星期三的課表："
            "8:10-10:00 微積分（理學院 A201）；"
            "10:10-12:00 英文會話（語言中心 302）；"
            "13:10-15:00 程式設計（資工系電腦教室）；"
            "15:10-16:00 體育（操場）；晚上沒課。"
        ),
        "enabled": False,
        "example": True,
    },
]

# 下拉選單的建議值。模型會下架也會出新的（gemini-2.0-flash 已於 2026 停用），
# 所以介面上一律允許自己打字，這裡只是省得每次手動輸入。
MODEL_SUGGESTIONS: dict[str, list[str]] = {
    "openai": ["gpt-5.4-mini", "gpt-4o-mini"],
    "gemini": ["gemini-3.1-flash-lite", "gemini-3.6-flash"],
}

# 沒有動靜超過這麼久的 Session 會被回收（連同它的 httpx 連線）。
# profile 已經存檔，回收掉不會弄丟設定，人回來時原樣載入。
SESSION_TTL = 2 * 60 * 60.0

# 「關掉網頁就收工」用的三個時間。開著的分頁會登記在 State.pages 裡：
# 關分頁時瀏覽器用 sendBeacon 打 /api/bye 除名，心跳只是備援。
#
# 這裡刻意不靠「心跳停了就關」——瀏覽器會把背景分頁的 setInterval 節流到
# 一分鐘一次，那樣開著卻沒在看的分頁會被誤判成關掉。分頁在不在以登記簿為準。
HEARTBEAT_INTERVAL = 5.0  # 前端多久打一次（web_ui.html 那邊要一致）
PAGE_STALE = 90.0  # 這麼久沒心跳就當那個分頁死了（瀏覽器當掉、直接關機）
CLOSE_GRACE = 6.0  # 登記簿空了之後再等這麼久，讓「重新整理」的新分頁接回來

# 記憶的長度上限。這是每一輪都要塞進 system prompt 的東西，
# 放任它長下去等於每輪都在多付 token 跟延遲。
MEMORY_MAX_CHARS = 400
MEMORY_MAX_ITEMS = 60

app = FastAPI(title="Reachy Mini Agent 控制台", docs_url=None, redoc_url=None)


# 串流幾張才夠看：太高的話區網頻寬跟這台筆電的 CPU 都會被榨乾，
# 8 fps 對「看一眼機器人現在朝哪看」這種用途已經夠用。
CAMERA_STREAM_FPS = 8.0


def _camera_available(st: "State") -> bool:
    """機器人有沒有鏡頭畫面可看（沒連機器人、或 daemon 沒開媒體都算沒有）."""
    mini = st.robot.mini
    return mini is not None and mini.media.camera is not None


def _daemon_url(st: "State") -> Optional[str]:
    """機器人背後那個 daemon 的 REST 位址，沒連機器人就是 None.

    喇叭音量是 daemon 既有的 /volume 端點在管，這裡直接借 ReachyMini SDK
    連線時記下的位址轉送過去，不用自己重新做一份音量控制。
    """
    mini = st.robot.mini
    if mini is None:
        return None
    return getattr(mini, "_daemon_http_url", None)


def mask(key: str) -> str:
    """把金鑰遮成「…尾四碼」，只是給人確認自己貼對了哪一把，不算洩漏."""
    key = key.strip()
    return f"…{key[-4:]}" if len(key) >= 8 else ("已設定" if key else "")


def memory_block(memories: list[dict[str, Any]]) -> str:
    """把啟用中的記憶組成要代入 {memory} 的那一段（沒有就回空字串）.

    刻意加上「不要主動複誦」：不講的話模型很愛一開口就把使用者的資料唸一遍。
    """
    lines = [
        f"- {str(m['text']).strip()}"
        for m in memories
        if m.get("enabled", True) and str(m.get("text", "")).strip()
    ]
    if not lines:
        return ""
    return (
        "\n關於這位使用者的長期記憶（他之前告訴過你的事，講話時自然地用上，"
        "不要主動複誦、也不要說「根據我的記憶」）：\n" + "\n".join(lines) + "\n"
    )


class Session:
    """一個瀏覽器背後的 agent：設定、金鑰、角色、工具、記憶、對話歷史.

    機器人不在這裡：那個是全機共用的，見 State.robot。
    """

    def __init__(self, sid: str, cfg: Config):
        """從基準設定複製一份，載入這個人存過的 profile（沒有就用預設）."""
        self.sid = sid
        self.cfg = replace(cfg)
        self.llm = LLM(self.cfg)
        self.history: list[dict[str, str]] = []
        self.template = DEFAULT_TEMPLATE
        self.role = DEFAULT_ROLE
        self.enabled_tools: set[str] = set(TOOL_SPECS)
        # 沒存過檔的人先看到兩條範例（關著的）。load_profile 會整份覆蓋掉，
        # 所以老使用者刪過的範例不會又長回來。
        self.memories: list[dict[str, Any]] = [dict(m) for m in EXAMPLE_MEMORIES]
        # 被刪掉的內建範例。記著才不會在下次載入時又補回來。
        self.removed_examples: set[str] = set()
        self.last_seen = time.time()
        self.load_profile()
        self.rebuild_prompt()

    # -- prompt ------------------------------------------------------------

    def rebuild_prompt(self) -> str:
        """把角色、情緒、工具、記憶代進樣板，得到真正送出去的 system prompt.

        任何一塊變動都重組一次。LLM 每輪都現讀 system_prompt，所以下一句話
        就生效，不必重建連線，對話歷史也留著。
        """
        menu = "\n".join(f"- {name}: {desc}" for name, desc in EMOTION_MENU.items())
        self.llm.system_prompt = (
            self.template.replace("{role}", self.role)
            .replace("{emotion_menu}", menu)
            .replace("{tool_menu}", tool_menu(self.enabled_tools))
            .replace("{memory}", memory_block(self.memories))
        )
        # 從選單裡消失還不夠：模型硬點一個關掉的工具也不能執行。
        self.llm.allowed_tools = set(self.enabled_tools)
        return self.llm.system_prompt

    # -- profile（存檔） ---------------------------------------------------

    def profile_path(self) -> Path:
        """這個 session 的設定檔位置（用 hash 當檔名，sid 不會變成路徑）."""
        name = hashlib.sha256(self.sid.encode("utf-8")).hexdigest()[:16]
        return PROFILE_DIR / f"{name}.json"

    def load_profile(self) -> None:
        """讀回存過的設定；檔案壞掉就當作沒有，別害使用者連介面都開不起來."""
        path = self.profile_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.role = str(data.get("role", self.role))
        # 樣板只有在使用者真的改過時才會被存起來（save_profile 那邊決定）。
        # 沒有這個旗標就用程式裡的當前預設——否則第一次存檔會把當下的預設凍在
        # 檔案裡，之後預設修好了也永遠到不了這個人身上（踩過：舊樣板少了 query
        # 欄位的說明，模型不會用行事曆，只會假裝已經幫你排好行程）。
        if data.get("template_custom") and data.get("template"):
            self.template = str(data["template"])
        self.memories = [
            {
                "id": str(m.get("id") or uuid.uuid4().hex[:8]),
                "text": str(m.get("text", ""))[:MEMORY_MAX_CHARS],
                "enabled": bool(m.get("enabled", True)),
                "example": bool(m.get("example", False)),
            }
            for m in data.get("memories", [])
            if isinstance(m, dict)
        ][:MEMORY_MAX_ITEMS]
        # 上面是整份覆蓋，所以存過檔的人本來看不到後來才加的範例。把少的補上，
        # 只跳過使用者自己刪掉的那些（記在 removed_examples，跟工具開關同一招）。
        self.removed_examples = {str(n) for n in data.get("removed_examples", [])}
        have = {m["id"] for m in self.memories}
        self.memories += [
            dict(m)
            for m in EXAMPLE_MEMORIES
            if m["id"] not in have and m["id"] not in self.removed_examples
        ]
        del self.memories[MEMORY_MAX_ITEMS:]
        # 存「關掉了哪些」而不是「開著哪些」：之後 tools.py 新增工具時，
        # 舊的 profile 會讓新工具預設是開的，而不是被沉默地擋在外面。
        disabled = {str(n) for n in data.get("disabled_tools", [])}
        self.enabled_tools = {n for n in TOOL_SPECS if n not in disabled}
        self.cfg.voice = str(data.get("voice", self.cfg.voice))
        self.cfg.use_emotion = bool(data.get("use_emotion", self.cfg.use_emotion))
        self.cfg.history_turns = int(data.get("history_turns", self.cfg.history_turns))
        self.cfg.provider = str(data.get("provider", self.cfg.provider))
        self.cfg.model = str(data.get("model", self.cfg.model))
        self.cfg.openai_base_url = str(data.get("base_url", self.cfg.openai_base_url))

    def save_profile(self) -> None:
        """存檔。**金鑰絕對不寫進去**——那個只活在記憶體與使用者的瀏覽器裡."""
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "memories": self.memories,
            "removed_examples": sorted(self.removed_examples),
            "disabled_tools": sorted(set(TOOL_SPECS) - self.enabled_tools),
            "voice": self.cfg.voice,
            "use_emotion": self.cfg.use_emotion,
            "history_turns": self.cfg.history_turns,
            "provider": self.cfg.provider,
            "model": self.cfg.model,
            "base_url": self.cfg.openai_base_url,
        }
        # 角色與樣板跟預設一樣就不要存：存了等於把「這一版的預設」凍住，日後
        # 改良的預設（例如新工具的用法說明）就再也進不來。真的改過才寫進檔案。
        if self.role != DEFAULT_ROLE:
            data["role"] = self.role
        if self.template != DEFAULT_TEMPLATE:
            data["template"] = self.template
            data["template_custom"] = True
        try:
            self.profile_path().write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError as e:
            print(f"！ profile 存檔失敗：{e}")

    # -- 金鑰 --------------------------------------------------------------

    def api_key(self) -> str:
        """回傳目前 provider 要用的那把金鑰."""
        return (
            self.cfg.openai_key
            if self.cfg.provider == "openai"
            else self.cfg.gemini_key
        )

    def require_key(self) -> None:
        """金鑰沒填好就擋下來，別讓人等到雲端回 401 才知道."""
        if not _looks_set(self.api_key()):
            name = (
                "OPENAI_API_KEY" if self.cfg.provider == "openai" else "GEMINI_API_KEY"
            )
            raise HTTPException(400, f"請先在「連線與模型」填入你的 {name}。")

    def close(self) -> None:
        """關掉這個人的 HTTP 連線."""
        self.llm.close()


class State:
    """整個行程共用的東西：基準設定、機器人、所有 Session."""

    def __init__(self, cfg: Config):
        """建立機器人連線（--no-robot 時退化成印字）."""
        self.base_cfg = cfg
        self.robot = Robot(cfg)
        self.robot_lock = threading.Lock()
        self.sessions: dict[str, Session] = {}
        self.sessions_lock = threading.Lock()
        # 目前開著的網頁：page_id → 最後一次心跳的時間。看門狗靠它決定要不要收工。
        # 沒有人開過網頁之前不武裝（pages_armed），--no-browser 乾跑才不會自己關掉。
        self.pages: dict[str, float] = {}
        self.pages_armed = False
        self.pages_lock = threading.Lock()

    def page_seen(self, page_id: str) -> None:
        """登記某個分頁還開著（心跳）."""
        with self.pages_lock:
            self.pages[page_id] = time.time()
            self.pages_armed = True

    def page_gone(self, page_id: str) -> None:
        """某個分頁關掉了。重新整理也會走這裡，所以收工前還有 CLOSE_GRACE 可以反悔."""
        with self.pages_lock:
            self.pages.pop(page_id, None)

    def pages_open(self) -> bool:
        """還有沒有開著的網頁；順手清掉久無心跳的（瀏覽器當掉沒送 bye 的情況）."""
        now = time.time()
        with self.pages_lock:
            for pid, seen in list(self.pages.items()):
                if now - seen > PAGE_STALE:
                    self.pages.pop(pid)
            return bool(self.pages)

    def get(self, sid: str) -> Session:
        """取出（必要時建立）某個瀏覽器的 Session，順手回收放太久的."""
        now = time.time()
        with self.sessions_lock:
            stale = [
                k for k, s in self.sessions.items() if now - s.last_seen > SESSION_TTL
            ]
            for k in stale:
                self.sessions.pop(k).close()
            session = self.sessions.get(sid)
            if session is None:
                session = self.sessions[sid] = Session(sid, self.base_cfg)
                # 讓這個人的 LLM 能用 VISION_TOOL：機器人只有一台、是 State
                # 共用的，Session 建立時接上去就好，之後不會再換。
                session.llm.capture_image = self.robot.get_frame_jpeg
            session.last_seen = now
            return session

    def close(self) -> None:
        """收線：關掉所有 HTTP client，機器人回原點."""
        for session in self.sessions.values():
            session.close()
        self.robot.close()


STATE: Optional[State] = None

# 跑起來之後才會有值（main() 設定）。存著是為了讓 MJPEG 串流那種「客戶端不斷線
# 就不會結束」的長連線知道伺服器正在關閉——不然按 Ctrl+C 之後 uvicorn 會一直
# 等這條串流收尾，看起來就像 Ctrl+C 沒有反應。
SERVER: Optional[uvicorn.Server] = None


def _shutting_down() -> bool:
    """伺服器是不是正在關閉（Ctrl+C 或看門狗觸發）."""
    return SERVER is not None and SERVER.should_exit


def current_session(
    x_session_id: Annotated[Optional[str], Header()] = None,
) -> Session:
    """從 X-Session-Id 標頭找出這個瀏覽器的 Session.

    沒帶標頭的（curl 之類）統一落到 "default"，行為就跟單人使用一樣。
    """
    if STATE is None:
        raise HTTPException(503, "尚未初始化")
    return STATE.get(x_session_id or "default")


def state() -> State:
    """取得共用狀態，還沒建好就回 503 而不是 AttributeError."""
    if STATE is None:
        raise HTTPException(503, "尚未初始化")
    return STATE


SessionDep = Annotated[Session, Depends(current_session)]


class PromptBody(BaseModel):
    """POST /api/prompt 的內容."""

    template: str


class RoleBody(BaseModel):
    """POST /api/role 的內容."""

    role: str


class ToolToggleBody(BaseModel):
    """POST /api/tools/{name}/toggle 的內容."""

    enabled: bool


class ToolRunBody(BaseModel):
    """POST /api/tools/{name}/run 的內容：試跑需要關鍵字的工具時才用得到."""

    query: Optional[str] = None


class MemoryBody(BaseModel):
    """新增或修改一條記憶；欄位都是選填，只改有帶的那些."""

    text: Optional[str] = None
    enabled: Optional[bool] = None


class CredentialsBody(BaseModel):
    """POST /api/credentials 的內容：每個人自己的金鑰與模型.

    api_key 留空 = 不動現有的那把（前端重新整理後不會把已設定的金鑰洗掉）；
    要清掉請送 clear=true。
    """

    provider: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    clear: bool = False


class SettingsBody(BaseModel):
    """POST /api/settings 的內容，欄位都是選填，只改有帶的那些."""

    voice: Optional[str] = None
    use_emotion: Optional[bool] = None
    use_face_tracking: Optional[bool] = None
    history_turns: Optional[int] = None


class ChatBody(BaseModel):
    """POST /api/chat 的內容."""

    text: str


class PageBody(BaseModel):
    """POST /api/heartbeat 的內容：哪一個分頁在報平安.

    是「分頁」不是「瀏覽器」：同一個 Session 開兩個分頁要分開算，
    不然關掉其中一個就會把還開著的那個也一起收掉。
    """

    page: str


class VolumeBody(BaseModel):
    """POST /api/audio/volume 的內容."""

    volume: int


def _server_key(provider: str) -> str:
    """伺服器端（.env）那把金鑰；.env 裡沒填就是空字串."""
    st = state()
    return st.base_cfg.openai_key if provider == "openai" else st.base_cfg.gemini_key


def _connection_info(s: Session) -> dict[str, Any]:
    """這個 Session 目前用哪家、哪把金鑰、哪個模型（金鑰只回遮罩後的尾碼）."""
    key = s.api_key()
    return {
        "provider": s.cfg.provider,
        "model": s.cfg.model,
        "base_url": s.cfg.openai_base_url,
        "key_masked": mask(key),
        "key_ok": _looks_set(key),
        "key_from_server": key == _server_key(s.cfg.provider),
        "model_suggestions": MODEL_SUGGESTIONS,
    }


def _agent_info(s: Session) -> dict[str, Any]:
    """角色、記憶、工具、樣板——重畫介面要的那一整包."""
    # 角色框裡是一大段人設，介面上方只放得下一個名字：跟現成人設逐字比對，
    # 對得上就顯示那個名字，改過一個字就是「自訂角色」。
    preset = next((p["name"] for p in ROLE_PRESETS if p["role"] == s.role), None)
    return {
        "role": s.role,
        "role_name": preset or "自訂角色",
        "is_default_role": s.role == DEFAULT_ROLE,
        "role_presets": ROLE_PRESETS,
        "memories": s.memories,
        "memory_preview": memory_block(s.memories).strip(),
        "tools": [
            {
                "name": n,
                "description": d,
                "enabled": n in s.enabled_tools,
                "needs_query": n in TOOL_NEEDS_QUERY,
                "query_hint": TOOL_QUERY_HINT.get(n, ""),
            }
            for n, d in TOOL_SPECS.items()
        ],
        "template": s.template,
        "rendered": s.llm.system_prompt,
        "is_default_template": s.template == DEFAULT_TEMPLATE,
    }


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """回傳介面本體."""
    return HTMLResponse((HERE / "web_ui.html").read_text(encoding="utf-8"))


@app.get("/api/state")
def get_state(s: SessionDep) -> dict[str, Any]:
    """介面開起來要知道的一切：連線、agent、設定、情緒清單."""
    st = state()
    return {
        "connection": _connection_info(s),
        "agent": _agent_info(s),
        "voice": s.cfg.voice,
        "use_emotion": s.cfg.use_emotion,
        "use_face_tracking": st.robot.face_tracking,
        "history_turns": s.cfg.history_turns,
        "robot": {
            "connected": st.robot.mini is not None,
            "emotions_loaded": st.robot.emotions is not None,
            "camera_available": _camera_available(st),
        },
        "emotions": [{"name": n, "label": d} for n, d in EMOTION_MENU.items()],
        "memory_limits": {"max_chars": MEMORY_MAX_CHARS, "max_items": MEMORY_MAX_ITEMS},
        "turns": len(s.history) // 2,
    }


# ---------------------------------------------------------------------------
# 角色
# ---------------------------------------------------------------------------


@app.post("/api/role")
def set_role(s: SessionDep, body: RoleBody) -> dict[str, Any]:
    """換一個角色（人設）。格式規則不在這裡，所以怎麼改都弄不壞 pipeline."""
    if not body.role.strip():
        raise HTTPException(400, "角色設定不能是空的")
    s.role = body.role.strip()
    s.rebuild_prompt()
    s.save_profile()
    return _agent_info(s)


@app.post("/api/role/reset")
def reset_role(s: SessionDep) -> dict[str, Any]:
    """還原成程式碼裡的預設角色."""
    s.role = DEFAULT_ROLE
    s.rebuild_prompt()
    s.save_profile()
    return _agent_info(s)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


@app.post("/api/tools/{name}/toggle")
def toggle_tool(s: SessionDep, name: str, body: ToolToggleBody) -> dict[str, Any]:
    """開關一個工具：關掉的不會出現在 prompt 裡，模型硬點也不會被執行."""
    if name not in TOOL_SPECS:
        raise HTTPException(404, f"沒有叫做 {name} 的工具")
    if body.enabled:
        s.enabled_tools.add(name)
    else:
        s.enabled_tools.discard(name)
    s.rebuild_prompt()
    s.save_profile()
    return _agent_info(s)


@app.post("/api/tools/{name}/run")
def try_tool(name: str, body: Optional[ToolRunBody] = None) -> dict[str, Any]:
    """不透過 LLM，直接試跑一個工具，看它到底回什麼（關掉的也能試）."""
    if name not in TOOL_SPECS:
        raise HTTPException(404, f"沒有叫做 {name} 的工具")
    query = (body.query or "") if body else ""
    if name in TOOL_NEEDS_QUERY and not query.strip():
        raise HTTPException(400, f"{name} 要有關鍵字才能跑")
    t0 = time.perf_counter()
    if name == VISION_TOOL:
        # tools.py 拿不到機器人（見 TOOL_FUNCS 裡的安全網說明），這裡直接
        # 借用共用的 Robot 真的拍一張，才測得出對話時 LLM 實際會看到什麼。
        frame = state().robot.get_frame_jpeg()
        result = (
            f"（成功拍到一張畫面，{len(frame)} bytes——對話時 LLM 看到的就是這張，"
            "跟下面「即時鏡頭」是同一支鏡頭。）"
            if frame is not None
            else "（沒有鏡頭畫面：機器人沒連線，或 daemon 沒有偵測到鏡頭。）"
        )
    else:
        result = run_tool(name, query)
    return {"name": name, "result": result, "seconds": time.perf_counter() - t0}


# ---------------------------------------------------------------------------
# 長期記憶
# ---------------------------------------------------------------------------


def _find_memory(s: Session, mid: str) -> dict[str, Any]:
    """依 id 找一條記憶，找不到就 404."""
    for m in s.memories:
        if m["id"] == mid:
            return m
    raise HTTPException(404, "找不到這條記憶（可能已經被刪掉）")


@app.post("/api/memories")
def add_memory(s: SessionDep, body: MemoryBody) -> dict[str, Any]:
    """記住一件事。內容會被代入 system prompt 的 {memory} 區段."""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "記憶內容不能是空的")
    if len(s.memories) >= MEMORY_MAX_ITEMS:
        raise HTTPException(400, f"記憶最多 {MEMORY_MAX_ITEMS} 條，請先刪掉一些。")
    s.memories.append(
        {
            "id": uuid.uuid4().hex[:8],
            "text": text[:MEMORY_MAX_CHARS],
            "enabled": True if body.enabled is None else body.enabled,
            "example": False,
        }
    )
    s.rebuild_prompt()
    s.save_profile()
    return _agent_info(s)


@app.patch("/api/memories/{mid}")
def edit_memory(s: SessionDep, mid: str, body: MemoryBody) -> dict[str, Any]:
    """改一條記憶的內容，或把它暫時停用（停用的不會進 prompt）."""
    memory = _find_memory(s, mid)
    if body.text is not None:
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "記憶內容不能是空的")
        memory["text"] = text[:MEMORY_MAX_CHARS]
    if body.enabled is not None:
        memory["enabled"] = body.enabled
    s.rebuild_prompt()
    s.save_profile()
    return _agent_info(s)


@app.delete("/api/memories/{mid}")
def delete_memory(s: SessionDep, mid: str) -> dict[str, Any]:
    """永久刪掉一條記憶."""
    memory = _find_memory(s, mid)
    if memory.get("example"):
        s.removed_examples.add(str(memory["id"]))  # 別讓它下次載入又冒出來
    s.memories.remove(memory)
    s.rebuild_prompt()
    s.save_profile()
    return _agent_info(s)


# ---------------------------------------------------------------------------
# Prompt 樣板（進階）
# ---------------------------------------------------------------------------


@app.post("/api/prompt")
def set_prompt(s: SessionDep, body: PromptBody) -> dict[str, Any]:
    """套用新的 system prompt 樣板（角色與記憶會照樣代進去）."""
    if not body.template.strip():
        raise HTTPException(400, "system prompt 不能是空的")
    s.template = body.template
    s.rebuild_prompt()
    s.save_profile()
    return _agent_info(s)


@app.post("/api/prompt/reset")
def reset_prompt(s: SessionDep) -> dict[str, Any]:
    """還原成程式碼裡的預設樣板."""
    s.template = DEFAULT_TEMPLATE
    s.rebuild_prompt()
    s.save_profile()
    return _agent_info(s)


# ---------------------------------------------------------------------------
# 連線與設定
# ---------------------------------------------------------------------------


@app.post("/api/credentials")
def set_credentials(s: SessionDep, body: CredentialsBody) -> dict[str, Any]:
    """設定這個瀏覽器自己的供應商、金鑰、base URL 與模型.

    金鑰只寫進記憶體裡的 Session，不會存檔；行程一關就沒了。
    """
    if body.provider is not None:
        if body.provider not in ("openai", "gemini"):
            raise HTTPException(400, "provider 只能是 openai 或 gemini")
        if body.provider != s.cfg.provider:
            s.cfg.provider = body.provider
            # 換家等於換模型：沿用上一家的模型名一定會 404。
            s.cfg.model = body.model or s.cfg.default_model()
    if body.clear:
        # 清掉自己填的那把，退回 .env 那把（.env 沒填就是清成空的）。
        s.cfg.openai_key = _server_key("openai")
        s.cfg.gemini_key = _server_key("gemini")
    elif body.api_key:
        if not _looks_set(body.api_key):
            raise HTTPException(
                400, "這串不像金鑰（太短，或還是 .env.example 的佔位符）"
            )
        if s.cfg.provider == "openai":
            s.cfg.openai_key = body.api_key.strip()
        else:
            s.cfg.gemini_key = body.api_key.strip()
    if body.base_url is not None:
        s.cfg.openai_base_url = (
            body.base_url.strip().rstrip("/") or "https://api.openai.com/v1"
        )
    if body.model is not None and body.model.strip():
        s.cfg.model = body.model.strip()
    s.save_profile()  # 存的是供應商與模型，金鑰不在裡面
    return _connection_info(s)


@app.post("/api/credentials/test")
def test_credentials(s: SessionDep) -> dict[str, Any]:
    """拿現在這組設定真的打一次雲端，確認金鑰與模型都是通的.

    直接借用 LLM 內部那條送出路徑（而不是自己再寫一份 HTTP 呼叫），
    這樣「測試通過」就真的等於「等一下對話也會通」。
    """
    s.require_key()
    t0 = time.perf_counter()
    try:
        raw = s.llm._call([{"role": "user", "content": "回一句「哈囉」就好。"}])
    except Exception as e:
        detail = getattr(getattr(e, "response", None), "text", "")
        raise HTTPException(
            502, f"{type(e).__name__}: {e} {detail[:200]}".strip()
        ) from e
    return {
        "ok": True,
        "seconds": time.perf_counter() - t0,
        "provider": s.cfg.provider,
        "model": s.cfg.model,
        "raw": raw[:200],
    }


@app.post("/api/settings")
def set_settings(s: SessionDep, body: SettingsBody) -> dict[str, Any]:
    """改語音與動作開關（下一輪生效，不用重開伺服器）."""
    st = state()
    cfg = s.cfg
    if body.voice is not None and body.voice.strip():
        cfg.voice = body.voice.strip()
    if body.use_emotion is not None:
        cfg.use_emotion = body.use_emotion
    if body.use_face_tracking is not None:
        # 追臉是全機共用的狀態（機器人只有一台，偵測跑在 daemon 端），所以不進
        # 個人 profile，也不會各瀏覽器一份——誰最後按的就算誰的。
        st.robot.set_face_tracking(body.use_face_tracking)
    if body.history_turns is not None:
        cfg.history_turns = max(1, min(50, body.history_turns))
    s.save_profile()
    return {
        "voice": cfg.voice,
        "use_emotion": cfg.use_emotion,
        "use_face_tracking": st.robot.face_tracking,
        "history_turns": cfg.history_turns,
    }


@app.post("/api/history/reset")
def reset_history(s: SessionDep) -> dict[str, Any]:
    """清空這個人的對話歷史（角色與長期記憶不動）."""
    s.history.clear()
    return {"turns": 0}


# ---------------------------------------------------------------------------
# 網頁還開著嗎（決定伺服器什麼時候自己收工，見 _watchdog）
# ---------------------------------------------------------------------------


@app.post("/api/heartbeat")
def heartbeat(s: SessionDep, body: PageBody) -> dict[str, Any]:
    """前端每 HEARTBEAT_INTERVAL 秒打一次，說「這個分頁還開著」."""
    state().page_seen(body.page)
    return {"ok": True}


@app.post("/api/bye")
async def bye(request: Request) -> dict[str, Any]:
    """分頁關掉時 sendBeacon 打過來的除名通知.

    刻意不吃 X-Session-Id、也不用 pydantic 驗證：sendBeacon 沒辦法帶自訂
    標頭，而且這是離開前最後一搏，寧可寬鬆也不要因為格式不合就漏掉。
    """
    try:
        data = await request.json()
        page = str(data.get("page", ""))
    except Exception:
        page = ""
    if page:
        state().page_gone(page)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 對話
# ---------------------------------------------------------------------------


@app.post("/api/chat")
def chat(s: SessionDep, body: ChatBody) -> dict[str, Any]:
    """跑一輪對話：LLM →（可能的工具）→ TTS → 動作 + 語音.

    刻意寫成同步函式：FastAPI 會把它丟到 threadpool，整輪（含語音播完）
    大約 4~8 秒，卡在 event loop 裡會讓其他請求一起停住。
    """
    st = state()
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "訊息不能是空的")
    s.require_key()
    # 機器人只有一台：正在講話時再送一句會讓兩段語音疊在一起。
    if not st.robot_lock.acquire(blocking=False):
        raise HTTPException(409, "機器人正在講上一句，等它講完再送。")
    try:
        t0 = time.perf_counter()
        result = run_turn(s.cfg, s.llm, st.robot, s.history, text)
        return {
            "say": result.say,
            "emotion": result.emotion,
            "emotion_label": move_label(result.emotion),
            "tools": [
                {"name": n, "result": r, "seconds": dt} for n, r, dt in result.tools
            ],
            "timings": {
                "llm": result.t_llm,
                "tts": result.t_tts,
                "total": time.perf_counter() - t0,
            },
            "audio": f"/api/audio/{result.mp3.name}",
            "turns": len(s.history) // 2,
        }
    except HTTPException:
        raise
    except Exception as e:
        # 雲端出錯、動作播不出來都不該讓介面白掉，把原因原樣送回前端。
        detail = getattr(getattr(e, "response", None), "text", "")
        raise HTTPException(
            502, f"{type(e).__name__}: {e} {detail[:200]}".strip()
        ) from e
    finally:
        st.robot_lock.release()


# 喇叭音量：轉發給 daemon 既有的音量端點，這裡不重做一份控制邏輯。
# 路徑是 /api/volume/*，不是 /volume/*：daemon 把 volume router 掛在一個
# prefix="/api" 的父 router 底下（見 daemon/app/main.py 的 include_router），
# 少掉那層 /api 就會 404。
#
# 必須宣告在 /api/audio/{name} 之前——Starlette 依宣告順序比對路由，
# 晚宣告的話 "volume" 會先被 {name} 這個萬用路徑吃掉，變成去找一個叫
# volume 的音檔（然後 404）。
DAEMON_VOLUME_PATH = "/api/volume"


@app.get("/api/audio/volume")
def get_audio_volume() -> dict[str, Any]:
    """讀目前的喇叭音量."""
    base = _daemon_url(state())
    if base is None:
        raise HTTPException(503, "沒有連機器人，無法讀取喇叭音量。")
    try:
        r = httpx.get(f"{base}{DAEMON_VOLUME_PATH}/current", timeout=5.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"讀取音量失敗：{e}") from e
    return r.json()


@app.post("/api/audio/volume")
def set_audio_volume(body: VolumeBody) -> dict[str, Any]:
    """設定喇叭音量（0-100）；daemon 那端設完會自己播一聲測試音."""
    base = _daemon_url(state())
    if base is None:
        raise HTTPException(503, "沒有連機器人，無法設定喇叭音量。")
    volume = max(0, min(100, body.volume))
    try:
        r = httpx.post(
            f"{base}{DAEMON_VOLUME_PATH}/set", json={"volume": volume}, timeout=5.0
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"設定音量失敗：{e}") from e
    return r.json()


@app.get("/api/audio/{name}")
def audio(name: str) -> FileResponse:
    """把合成好的 mp3 交給瀏覽器播（沒接機器人時這是唯一聽得到的路徑）."""
    # 只認 workdir 底下的檔名，路徑分隔字元一律擋掉。
    if Path(name).name != name:
        raise HTTPException(400, "檔名不合法")
    path = state().base_cfg.workdir / name
    if not path.is_file():
        raise HTTPException(404, "音檔不存在（可能已被清掉）")
    return FileResponse(path, media_type="audio/mpeg")


# ---------------------------------------------------------------------------
# 鏡頭
# ---------------------------------------------------------------------------


@app.get("/api/camera/stream")
async def camera_stream(request: Request) -> StreamingResponse:
    """即時鏡頭畫面：MJPEG multipart 串流，前端 <img> 直接吃這個位址就會動.

    抓畫面是 GStreamer 的同步呼叫，丟到 asyncio.to_thread 裡跑，才不會卡住
    event loop（跟其他人的對話請求搶時間）。用 request.is_disconnected()
    主動偵測斷線：分頁一關掉或重新整理就停止抓畫面，不會留著背景工作白跑。

    也要看 _shutting_down()：這條串流只要瀏覽器不關就不會自己結束，而 uvicorn
    關閉前會等所有進行中的請求收尾——少了這個條件，按 Ctrl+C 會像沒反應一樣。
    """
    st = state()
    if not _camera_available(st):
        raise HTTPException(503, "沒有連機器人，或機器人沒有可用的鏡頭畫面。")

    async def frames() -> Any:
        interval = 1.0 / CAMERA_STREAM_FPS
        while not _shutting_down() and not await request.is_disconnected():
            frame = await asyncio.to_thread(st.robot.get_frame_jpeg)
            if frame is not None:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
            await asyncio.sleep(interval)

    return StreamingResponse(
        frames(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


# ---------------------------------------------------------------------------
# 啟動
# ---------------------------------------------------------------------------


def _watchdog(server: uvicorn.Server) -> None:
    """網頁全部關掉之後讓伺服器自己收工.

    在背景執行緒裡跑，每秒看一次登記簿：
      * 還沒有人開過網頁 → 不動作（--no-browser 乾跑不該被自己關掉）
      * 還有分頁開著 → 不動作
      * 空了 → 再等 CLOSE_GRACE，因為「重新整理」也會讓登記簿短暫變空
      * 正在講話 → 再等一下，別把話切一半
    """
    empty_since: Optional[float] = None
    while not server.should_exit:
        time.sleep(1.0)
        if STATE is None or not STATE.pages_armed:
            continue
        if STATE.pages_open():
            empty_since = None
            continue
        now = time.time()
        if empty_since is None:
            empty_since = now
            continue
        if now - empty_since < CLOSE_GRACE:
            continue
        if STATE.robot_lock.locked():
            # 還在講上一句，等它講完再關；下一圈就會過。
            continue
        print("\n網頁已關閉，伺服器自動結束。")
        server.should_exit = True
        return


def base_config(args: argparse.Namespace) -> Config:
    """組出基準設定.

    不用 cloud_voice_chat.load_config()：那支專門給 CLI，沒金鑰就直接 SystemExit。
    這裡少一把金鑰是正常狀態——等使用者在介面上填。
    """
    base_url = "https://api.openai.com/v1"
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except ImportError:
        pass  # 沒裝 python-dotenv 就只吃系統環境變數
    openai_key = os.getenv("OPENAI_API_KEY", "")
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", base_url).rstrip("/") or base_url

    provider = args.provider
    if provider == "auto":
        # 哪把金鑰真的填了就用哪家；都沒有就先擺 openai，等使用者自己選。
        provider = (
            "gemini"
            if not _looks_set(openai_key) and _looks_set(gemini_key)
            else "openai"
        )

    cfg = Config(
        provider=provider,
        voice=args.voice,
        use_robot=not args.no_robot,
        use_emotion=not args.no_emotion,
        use_face_tracking=not args.no_face_tracking,
        openai_key=openai_key,
        openai_base_url=base_url,
        gemini_key=gemini_key,
    )
    cfg.model = args.model or cfg.default_model()
    cfg.workdir.mkdir(parents=True, exist_ok=True)
    return cfg


def main() -> None:
    """解析參數、連上機器人，然後把伺服器跑起來."""
    parser = argparse.ArgumentParser(
        description="Reachy Mini Agent 的瀏覽器控制台（角色、工具、長期記憶、對話）"
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "openai", "gemini"],
        default="auto",
        help="預設的雲端 LLM 供應商（每個人都能在介面上自己改）",
    )
    parser.add_argument("--model", default="", help="預設模型（介面上也能改）")
    parser.add_argument(
        "--voice", default="zh-TW-HsiaoChenNeural", help="edge-tts 語音"
    )
    parser.add_argument(
        "--no-robot", action="store_true", help="不連機器人，語音只在瀏覽器播"
    )
    parser.add_argument(
        "--no-emotion", action="store_true", help="不播預錄情緒動作（跳過動作庫下載）"
    )
    parser.add_argument(
        "--no-face-tracking", action="store_true", help="不要看到人臉就轉頭看著他"
    )
    parser.add_argument("--port", type=int, default=8800, help="監聽埠")
    parser.add_argument(
        "--no-browser", action="store_true", help="啟動後不自動開瀏覽器"
    )
    args = parser.parse_args()

    cfg = base_config(args)

    global STATE
    STATE = State(cfg)

    url = f"http://{HOST}:{args.port}"
    print(f"雲端 pipeline：{cfg.provider}/{cfg.model} → edge-tts/{cfg.voice} → 馬達")
    print(f"agent 設定存放：{PROFILE_DIR}")
    if not (_looks_set(cfg.openai_key) or _looks_set(cfg.gemini_key)):
        print("⚠️  .env 裡沒有可用金鑰，請在介面的「連線與模型」填入。")
    print(f"控制台：{url}")
    print("要結束時，請關閉網頁（或在這裡按 Ctrl+C）。")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # uvicorn.run() 沒有辦法從外面喊停，所以自己組 Server，看門狗才有東西可以關。
    # timeout_graceful_shutdown 是保險：萬一還有哪條連線不肯收尾，時間到就強制關，
    # 不會讓 Ctrl+C 變成無限等待。
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=HOST,
            port=args.port,
            log_level="warning",
            timeout_graceful_shutdown=3,
        )
    )
    global SERVER
    SERVER = server
    threading.Thread(target=_watchdog, args=(server,), daemon=True).start()

    try:
        server.run()
    finally:
        STATE.close()
        print("收工。")


if __name__ == "__main__":
    main()
