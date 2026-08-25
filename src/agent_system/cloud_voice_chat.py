r"""Reachy Mini 雲端三段式對話 Pipeline：STT → LLM → 工具 → TTS → 本地馬達動作.

設計目標
--------
完全不吃本地顯卡：辨識與生成都丟給雲端 API，本地只負責「輸入 → 播放 → 擺頭」。
代價是每輪約 1.5~2.5 秒延遲，用「思考動作」蓋掉大部分等待的體感。

    使用者輸入 ─┬─ 打字（預設，麥克風故障期間唯一可用路徑）
                └─ 麥克風錄音 → 雲端 Whisper API（--input mic，修好後才可用）
                    │
                    ▼
              雲端 LLM（OpenAI 或 Gemini，純 REST，不裝任何官方 SDK）
                    │  回傳 JSON: {"say": ..., "emotion": ..., "tool": ...}
                    │
                    ├─ tool 有值 → 執行 tools.py 的工具 → 把結果餵回去再問一次
                    ▼
              edge-tts（微軟，免費、免金鑰）→ mp3
                    │
                    ▼
              Reachy Mini：情緒動作 + 語音同時播（擺頭疊加在動作之上）

工具
----
工具定義在同目錄的 tools.py。目前只有一個：weather_taipei（台北市即時天氣，
走 Open-Meteo，免金鑰）。模型自己決定要不要用；用了會多一次雲端往返，約 +1 秒。

⚠️ 本機麥克風硬體故障（XVF3800 收不到麥克風陣列訊號，見 test_microphone.py 的
   歷史基準）。因此 --input 預設是 text；改用 mic 之前請先跑一次診斷確認修好了。

前置作業
--------
    reachy_mini_env\Scripts\activate
    uv pip install -r src/agent_system/requirements.txt

    金鑰放在 repo 根目錄的 .env（見 .env.example）：
        OPENAI_API_KEY=sk-...            # provider=openai 時必填
        GEMINI_API_KEY=...               # provider=gemini 時必填
        OPENAI_BASE_URL=...              # 選填，可指向 OpenAI 相容服務（Groq 等）

執行方式
--------
    python src/agent_system/cloud_voice_chat.py                 # 打字對話迴圈
    python src/agent_system/cloud_voice_chat.py --text "台北現在天氣如何"
    python src/agent_system/cloud_voice_chat.py --provider gemini
    python src/agent_system/cloud_voice_chat.py --no-robot      # 沒接機器人時乾跑
    python src/agent_system/cloud_voice_chat.py --input mic     # 麥克風修好後才有意義

    python src/agent_system/tools.py                            # 單獨測工具

執行前請確認 daemon 已啟動（desktop app 或 `uv run reachy-mini-daemon`）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# 輸出被導向檔案或管線時 Windows 會退回 cp950，中文與符號會炸 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

# 輸入從管線進來時同理：cp950 解不出來的位元組會變成 surrogate，
# 一路帶到 httpx 才炸「surrogates not allowed」。互動終端機不動它，
# 那條路徑走的是 console 的寬字元 API，本來就正確。
if not sys.stdin.isatty():
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

import httpx  # noqa: E402

# 同目錄的工具模組。以腳本方式執行時 sys.path[0] 就是這個資料夾，直接 import 得到。
from tools import TOOL_SPECS, run_tool, tool_menu  # noqa: E402

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

EMOTIONS_DATASET = "pollen-robotics/reachy-mini-emotions-library"

# _cloud_work() 的回傳：(台詞, 情緒代號, mp3, LLM 秒數, TTS 秒數, 工具紀錄)
# 工具紀錄的每一筆是 (工具名, 回傳字串, 耗時秒數)。
CloudResult = tuple[str, str, Path, float, float, list[tuple[str, str, float]]]

# 給 LLM 挑選的情緒白名單。刻意只收「短動作」（約 2~4 秒）：
# 太長的動作（curious1 11.8s、boredom1 15.7s）會讓對話節奏整個卡死。
# 完整 85 種清單見 repo 根目錄的 EMOTIONS.md。
EMOTION_MENU: dict[str, str] = {
    "cheerful1": "歡快高興",
    "enthusiastic1": "熱情熱烈",
    "laughing1": "大笑",
    "success1": "成功慶祝",
    "proud1": "驕傲自豪",
    "grateful1": "感激感謝",
    "welcoming1": "熱情歡迎",
    "yes1": "點頭答應",
    "no1": "搖頭說不",
    "understanding1": "理解贊同",
    "helpful2": "樂意幫忙",
    "inquiring1": "詢問打聽",
    "surprised1": "驚訝意外",
    "amazed1": "驚嘆讚嘆",
    "confused1": "疑惑困惑",
    "uncertain1": "不確定猶豫",
    "displeased1": "不悅不滿",
    "oops1": "糟糕哎呀",
    "resigned1": "無奈認命",
    "dance1": "跳舞律動",
    "none": "不做動作，只說話",
}

# 等 LLM 回覆時播的動作（不出聲，純馬達），用來蓋掉雲端往返的延遲。
THINKING_MOVE = "thoughtful1"

# 終端機顯示動作名稱時用的中文對照（思考動作不在 LLM 的選單裡，另外補上）。
MOVE_LABELS: dict[str, str] = {**EMOTION_MENU, THINKING_MOVE: "深思熟慮"}


def move_label(name: str) -> str:
    """把動作代號變成「代號（中文）」，查不到就只回代號."""
    desc = MOVE_LABELS.get(name)
    return f"{name}（{desc}）" if desc else name


SYSTEM_PROMPT = """你是 Reachy Mini，一台放在桌上的小型桌面機器人，正在和面前的人用繁體中文聊天。

個性：好奇、友善、講話簡短有活力，像個話不多但很捧場的夥伴。

規則：
1. 一律用繁體中文（台灣用語）回覆。
2. 回覆要短。1 到 2 句話，最多 60 個字，因為這段文字會被唸出來。
3. 不要用 markdown、條列、表情符號或任何唸不出來的符號。
4. 從情緒清單中挑一個最貼近這句話語氣的動作。
5. 需要即時資料才答得出來的問題，一定要用工具查，不可以憑印象亂講。

情緒清單（只能從中挑一個，填動作代號）：
{emotion_menu}

可用工具：
{tool_menu}

只輸出這個格式的 JSON，不要有其他文字：
{{"say": "要說出口的話", "emotion": "動作代號", "tool": null}}

不需要工具時 tool 填 null。要用工具時把 tool 填成工具代號，say 填空字串、
emotion 填 none——系統會執行工具並把結果交給你，你再產生真正要說出口的那一句。"""


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """一次執行的所有設定."""

    provider: str = "openai"
    model: str = ""
    voice: str = "zh-TW-HsiaoChenNeural"
    input_mode: str = "text"
    record_seconds: float = 5.0
    use_robot: bool = True
    use_emotion: bool = True
    use_thinking_move: bool = True
    history_turns: int = 8
    workdir: Path = field(
        default_factory=lambda: Path(tempfile.gettempdir()) / "reachy_chat"
    )

    # 由環境變數填入
    openai_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    gemini_key: str = ""

    def default_model(self) -> str:
        """回傳該供應商的預設模型（速度優先，因為延遲是這條 pipeline 的痛點）.

        模型會下架（gemini-2.0-flash 已於 2026 年停用），跑出 404 時用
        --model 指定新的即可，不必改程式。
        """
        return "gpt-4o-mini" if self.provider == "openai" else "gemini-3.1-flash-lite"


def _looks_set(value: str) -> bool:
    """判斷金鑰是不是真的填了（而不是 .env.example 留下的佔位符）.

    OpenAI 與 Gemini 的金鑰都遠長於 20 字元，`sk-...` 這種佔位符會被擋掉。
    """
    value = value.strip()
    return len(value) > 20 and "..." not in value


def load_config(args: argparse.Namespace) -> Config:
    """組出 Config，並檢查必要金鑰."""
    try:
        from dotenv import load_dotenv

        # 從這支腳本往上找 repo 根目錄的 .env
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except ImportError:
        pass  # 沒裝 python-dotenv 就只吃系統環境變數

    provider = args.provider
    if provider == "auto":
        # 哪把金鑰真的填了就用哪家；兩把都有時以 OpenAI 為先。
        if _looks_set(os.getenv("OPENAI_API_KEY", "")):
            provider = "openai"
        elif _looks_set(os.getenv("GEMINI_API_KEY", "")):
            provider = "gemini"
        else:
            raise SystemExit(
                "✗ .env 裡沒有可用的金鑰。\n"
                "  請在 repo 根目錄的 .env 填入 OPENAI_API_KEY 或 GEMINI_API_KEY，\n"
                "  Gemini 金鑰可在 https://aistudio.google.com/apikey 免費取得。"
            )

    cfg = Config(
        provider=provider,
        voice=args.voice,
        input_mode=args.input,
        record_seconds=args.record_seconds,
        use_robot=not args.no_robot,
        use_emotion=not args.no_emotion,
        use_thinking_move=not args.no_thinking_move,
        openai_key=os.getenv("OPENAI_API_KEY", ""),
        openai_base_url=os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        ).rstrip("/"),
        gemini_key=os.getenv("GEMINI_API_KEY", ""),
    )
    cfg.model = args.model or cfg.default_model()
    cfg.workdir.mkdir(parents=True, exist_ok=True)

    if cfg.provider == "openai" and not _looks_set(cfg.openai_key):
        raise SystemExit("✗ .env 裡的 OPENAI_API_KEY 沒填好（目前是空的或佔位符）。")
    if cfg.provider == "gemini" and not _looks_set(cfg.gemini_key):
        raise SystemExit("✗ .env 裡的 GEMINI_API_KEY 沒填好（目前是空的或佔位符）。")
    if cfg.input_mode == "mic" and not _looks_set(cfg.openai_key):
        raise SystemExit(
            "✗ STT 走 OpenAI 相容的 Whisper API，--input mic 需要 OPENAI_API_KEY。"
        )

    return cfg


# ---------------------------------------------------------------------------
# 第一段：STT（雲端 Whisper）
# ---------------------------------------------------------------------------


def transcribe(cfg: Config, wav_path: Path) -> str:
    """把 WAV 丟到雲端 Whisper API，回傳辨識出的中文文字.

    走 OpenAI 的 /audio/transcriptions。把 OPENAI_BASE_URL 換成 Groq 等
    相容服務也能直接用（Groq 的 whisper-large-v3 便宜且更快）。
    """
    with open(wav_path, "rb") as f:
        resp = httpx.post(
            f"{cfg.openai_base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {cfg.openai_key}"},
            files={"file": (wav_path.name, f, "audio/wav")},
            data={"model": "whisper-1", "language": "zh"},
            timeout=60.0,
        )
    resp.raise_for_status()
    return str(resp.json().get("text", "")).strip()


# ---------------------------------------------------------------------------
# 第二段：LLM（OpenAI / Gemini，純 REST）
# ---------------------------------------------------------------------------


class LLM:
    """對兩家雲端 LLM 的最小封裝：吃對話歷史，吐出 (要說的話, 情緒代號)."""

    def __init__(self, cfg: Config):
        """建立 client 並組出帶情緒清單的 system prompt."""
        self.cfg = cfg
        menu = "\n".join(f"- {name}: {desc}" for name, desc in EMOTION_MENU.items())
        self.system_prompt = SYSTEM_PROMPT.format(
            emotion_menu=menu, tool_menu=tool_menu()
        )
        # 保持連線重用，省掉每輪的 TLS 握手（對總延遲有感）。
        self.client = httpx.Client(timeout=60.0)
        # 模型不支援關閉 thinking 時會被設成 False，之後就不再帶那個欄位。
        self._gemini_thinking_config = True

    def close(self) -> None:
        """關閉底層連線."""
        self.client.close()

    def reply(
        self, history: list[dict[str, str]]
    ) -> tuple[str, str, list[tuple[str, str, float]]]:
        """送出歷史對話，回傳 (say, emotion, 工具紀錄).

        模型要求用工具時就地執行，把結果接回對話再問一次。只給一輪：
        目前只有一個工具，連兩次都想查通常是模型鬼打牆，多繞一圈只是白等 1 秒。
        """
        # 工具的一來一回只活在這次呼叫裡，不寫進真正的對話歷史，
        # 免得原始資料一直佔著 context。模型講出口的那句話還是會留在歷史裡，
        # 所以「那要帶傘嗎」這種追問不會再打一次 API（實測確實不會）。
        messages = list(history)
        trace: list[tuple[str, str, float]] = []

        say, emotion, tool = self._parse(self._call(messages))
        if tool is None:
            return say, emotion, trace

        t0 = time.perf_counter()
        result = run_tool(tool)
        trace.append((tool, result, time.perf_counter() - t0))

        messages.append({"role": "assistant", "content": json.dumps({"tool": tool})})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"[工具結果] {tool}：{result} "
                    "請用這個結果回覆使用者，這次 tool 一定要填 null。"
                ),
            }
        )
        say, emotion, _ = self._parse(self._call(messages))
        return say, emotion, trace

    def _call(self, messages: list[dict[str, str]]) -> str:
        """依 provider 打對應的 API，回傳模型輸出的原始字串."""
        return (
            self._call_openai(messages)
            if self.cfg.provider == "openai"
            else self._call_gemini(messages)
        )

    def _call_openai(self, history: list[dict[str, str]]) -> str:
        resp = self.client.post(
            f"{self.cfg.openai_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.cfg.openai_key}"},
            json={
                "model": self.cfg.model,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    *history,
                ],
                "temperature": 0.8,
                "max_tokens": 200,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"])

    def _call_gemini(self, history: list[dict[str, str]]) -> str:
        # Gemini 的角色名是 user / model，且 system prompt 走獨立欄位。
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in history
        ]
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.cfg.model}:generateContent"
        )
        gen_config: dict[str, Any] = {
            "temperature": 0.8,
            # 給得寬鬆一點：Gemini 3.x 會先花 token 做內部思考，
            # 上限太小的話真正的回覆會被截斷成半截 JSON。
            "maxOutputTokens": 800,
            "responseMimeType": "application/json",
        }
        if self._gemini_thinking_config:
            # 這是閒聊，不需要思考鏈；關掉省下大半延遲。
            # 實測 gemini-3.1-flash-lite：0.92s 且零 thought token。
            gen_config["thinkingConfig"] = {"thinkingBudget": 0}

        body = {
            "systemInstruction": {"parts": [{"text": self.system_prompt}]},
            "contents": contents,
            "generationConfig": gen_config,
        }
        headers = {"x-goog-api-key": self.cfg.gemini_key}
        resp = self.client.post(url, headers=headers, json=body)

        # 不是每個模型都吃 thinkingBudget（gemini-3.6-flash 就會回 400）。
        # 碰到一次就記住，之後都不帶，不必每輪重試。
        if resp.status_code == 400 and self._gemini_thinking_config:
            self._gemini_thinking_config = False
            gen_config.pop("thinkingConfig", None)
            resp = self.client.post(url, headers=headers, json=body)

        resp.raise_for_status()
        return str(resp.json()["candidates"][0]["content"]["parts"][0]["text"])

    @staticmethod
    def _parse(raw: str) -> tuple[str, str, Optional[str]]:
        """挖出 say / emotion / tool，格式跑掉時退化成「整段當台詞」."""
        text = raw.strip()
        # 就算指定了 JSON 模式，有些相容服務還是會包上 ```json 圍欄。
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()

        try:
            data: Any = json.loads(text)
            say = str(data.get("say", "")).strip()
            emotion = str(data.get("emotion", "none")).strip()
            tool = data.get("tool")
        except (json.JSONDecodeError, AttributeError):
            say, emotion, tool = text, "none", None

        if emotion not in EMOTION_MENU:
            emotion = "none"  # 模型自己發明代號時，別讓 RecordedMoves 丟例外

        # null、"null"、"none"、亂編的代號，一律當成不用工具。
        name = str(tool).strip() if tool else ""
        if name in TOOL_SPECS:
            return say, emotion, name  # 這一輪的 say 是過場話，不會被唸出來
        return say or "抱歉，我剛剛沒想到要說什麼。", emotion, None


# ---------------------------------------------------------------------------
# 第三段：TTS（edge-tts，免金鑰）
# ---------------------------------------------------------------------------


def synthesize(text: str, voice: str, out_path: Path) -> Path:
    """用 edge-tts 合成語音，寫成 mp3 後回傳路徑.

    可用的繁中語音：
        zh-TW-HsiaoChenNeural（女聲·曉臻）
        zh-TW-HsiaoYuNeural  （女聲·曉雨）
        zh-TW-YunJheNeural   （男聲·雲哲）
    """
    import edge_tts

    async def _run() -> None:
        await edge_tts.Communicate(text, voice).save(str(out_path))

    asyncio.run(_run())
    return out_path


# ---------------------------------------------------------------------------
# 本地端：機器人動作與播放
# ---------------------------------------------------------------------------


def play_and_wait(mini: Any, path: Path, timeout_margin: float = 15.0) -> None:
    """播放音檔並確實等到播完.

    不能用 `time.sleep(duration)`：`media.play_sound()` 最後只是
    `playbin.set_state(PLAYING)`，這是非同步的，函式返回時聲音還沒開始播。
    本機實測起步要 0.8~1.1 秒（開 WASAPI 端點 + decodebin 探測格式），
    盲等會把尾巴砍掉，短句甚至整段沒聲音——但頭照樣會動，因為 head wobbler
    在 set_state 之前就啟動了，資料來自 tee 分支，不必等喇叭端點開好。

    正解是等 playbin 完成 async 轉換，再等 bus 上的 EOS。
    """
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    from reachy_mini.media.gstreamer_utils import audio_duration_seconds

    abs_path = str(path.resolve())
    duration = audio_duration_seconds(abs_path)

    mini.media.play_sound(abs_path)

    playbin = getattr(mini.media.audio, "_playbin", None)
    if playbin is None:
        # WEBRTC / NO_MEDIA backend 沒有本地 playbin，只能盲等。
        time.sleep(duration + 1.5)
        return

    playbin.get_state(10 * Gst.SECOND)
    msg = playbin.get_bus().timed_pop_filtered(
        int((duration + timeout_margin) * Gst.SECOND),
        Gst.MessageType.EOS | Gst.MessageType.ERROR,
    )
    if msg is not None and msg.type == Gst.MessageType.ERROR:
        err, _ = msg.parse_error()
        print(f"✗ 播放錯誤：{err.message}")


class Robot:
    """包住 ReachyMini 的動作與播放。--no-robot 時整個退化成印字."""

    def __init__(self, cfg: Config):
        """連線機器人並預載情緒動作庫（第一次會從 HuggingFace 下載）."""
        self.cfg = cfg
        self.mini: Any = None
        self.emotions: Any = None

        if not cfg.use_robot:
            print("（--no-robot：不連機器人，語音只存檔不播放）")
            return

        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        from reachy_mini import ReachyMini
        from reachy_mini.media.device_detection import get_audio_device

        # 找不到 Reachy 的喇叭端點時，SDK 會靜默 fallback 到 autoaudiosink，
        # 聲音會跑去系統預設輸出（藍牙耳機之類）而不是機器人——但頭照樣會動。
        # Gst.init() 要先呼叫，否則 device monitor 拋的例外會被吞成 None。
        Gst.init([])
        if get_audio_device("Sink") is None:
            print("⚠️  找不到 Reachy 喇叭端點，聲音可能會跑到系統預設輸出裝置。")

        self.mini = ReachyMini(log_level="WARNING")
        self.mini.__enter__()
        print("✓ 已連上 Reachy Mini")

        if cfg.use_emotion:
            from reachy_mini.motion.recorded_move import RecordedMoves

            print("… 載入情緒動作庫")
            self.emotions = RecordedMoves(EMOTIONS_DATASET)
            print(f"✓ 情緒動作庫就緒（{len(self.emotions.list_moves())} 種）")

    def close(self) -> None:
        """回到原點後收線.

        中途 Ctrl+C 時動作會停在半路，收線前扶正一次，別讓它歪著頭過夜。
        """
        if self.mini is None:
            return
        try:
            self.mini.cancel_move()
            self.go_home()
        except Exception as e:
            print(f"  ！ 收線前回原點失敗：{e}")
        self.mini.__exit__(None, None, None)

    def cancel_move(self) -> None:
        """中斷正在播的動作（思考動作等到雲端回來就該停）."""
        if self.mini is not None:
            self.mini.cancel_move()

    def go_home(self, duration: float = 1.0) -> None:
        """回到中立姿勢（動作原點）.

        用 SDK 的 INIT_* 常數，跟 `wake_up()` / `goto_sleep()` 收在同一個姿態。
        頭部與 notebook 的 `create_head_pose()` 等價（都是 4x4 單位矩陣），
        但天線用 SDK 的 ±10° 偏移而不是 [0, 0]——完全垂直時天線會抖。
        """
        if self.mini is None:
            return
        from reachy_mini.reachy_mini import (
            INIT_ANTENNAS_JOINT_POSITIONS,
            INIT_HEAD_POSE,
        )

        print(f"  🏠 回到原點（{duration:.1f}s）")
        try:
            self.mini.goto_target(
                head=INIT_HEAD_POSE,
                antennas=INIT_ANTENNAS_JOINT_POSITIONS,
                duration=duration,
                body_yaw=0.0,
            )
        except Exception as e:
            print(f"  ！ 回原點失敗：{e}")

    def play_emotion(self, name: str, sound: bool = False, icon: str = "🎭") -> None:
        """播一段預錄動作，並在終端機顯示是哪一個.

        sound=False 是刻意的：動作自帶的音效會蓋掉等一下要播的 TTS 語音。
        """
        if self.emotions is None or name == "none":
            if name == "none":
                print(f"  {icon} （這句不配動作）")
            return
        print(f"  {icon} {move_label(name)}")
        try:
            move = self.emotions.get(name)
            self.mini.play_move(move, initial_goto_duration=0.4, sound=sound)
        except Exception as e:  # 動作不存在或被 cancel，不該讓整段對話中斷
            print(f"  ！ 動作 {name} 播放失敗：{e}")

    def speak(self, mp3_path: Path) -> None:
        """播放語音，同時開啟音訊反應式擺頭.

        擺頭分析的是「播出去的音訊」，跟麥克風無關，所以麥克風壞掉照樣會動。
        """
        if self.mini is None:
            print(f"（語音已存檔：{mp3_path}）")
            return

        t0 = time.perf_counter()
        self.mini.enable_wobbling()
        try:
            play_and_wait(self.mini, mp3_path)
        finally:
            self.mini.disable_wobbling()
        print(f"  🔊 語音播放 {time.perf_counter() - t0:.1f}s")

    def record(self, seconds: float) -> Optional[Path]:
        """錄一段麥克風音訊存成 WAV。麥克風故障時拿到的會是整段靜音."""
        if self.mini is None:
            print("✗ --no-robot 模式沒有麥克風可用。")
            return None

        import numpy as np

        from reachy_mini.media.audio_utils import save_audio_to_wav

        samples: list[Any] = []
        self.mini.media.start_recording()

        deadline = time.time() + 1.0
        while self.mini.media.get_audio_sample() is None and time.time() < deadline:
            time.sleep(0.005)

        print(f"🎤 錄音 {seconds:.0f} 秒，請說話…")
        deadline = time.time() + seconds
        while time.time() < deadline:
            sample = self.mini.media.get_audio_sample()
            if sample is not None:
                samples.append(sample)
        self.mini.media.stop_recording()

        if not samples:
            print("✗ 沒有收到任何音訊資料。")
            return None

        wav_path = self.cfg.workdir / "input.wav"
        save_audio_to_wav(
            np.concatenate(samples, axis=0),
            self.mini.media.get_input_audio_samplerate(),
            str(wav_path),
        )
        return wav_path


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def get_user_text(cfg: Config, robot: Robot) -> Optional[str]:
    """取得這一輪的使用者輸入；回傳 None 代表要結束對話."""
    if cfg.input_mode == "text":
        try:
            text = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if text.lower() in {"exit", "quit", "bye"} or text in {"掰掰", "再見"}:
            return None
        return text

    # mic 模式：按 Enter 開始錄音
    try:
        cmd = input("\n按 Enter 開始錄音（輸入 q 離開）> ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if cmd.lower() == "q":
        return None

    wav = robot.record(cfg.record_seconds)
    if wav is None:
        return ""

    t0 = time.perf_counter()
    text = transcribe(cfg, wav)
    elapsed = time.perf_counter() - t0
    print(f"你（辨識）> {text or '（沒聽到內容）'}   [STT {elapsed:.2f}s]")
    return text


def run_turn(
    cfg: Config,
    llm: LLM,
    robot: Robot,
    history: list[dict[str, str]],
    user_text: str,
) -> None:
    """跑完一輪：LLM（可能夾一次工具查詢）→ TTS → 動作 + 語音 → 回原點.

    LLM 與 TTS 丟到背景執行緒，同時播「思考中」動作，
    把 1.5~2.5 秒的雲端往返藏在動作底下；雲端一回來就中斷它。
    """
    history.append({"role": "user", "content": user_text})

    def _cloud_work() -> CloudResult:
        t = time.perf_counter()
        say, emotion, tools_used = llm.reply(history)
        # 工具耗時從 LLM 那格扣掉，不然看不出到底是誰慢。
        t_tool = sum(dt for _, _, dt in tools_used)
        t_llm = time.perf_counter() - t - t_tool

        t = time.perf_counter()
        mp3 = synthesize(
            say, cfg.voice, cfg.workdir / f"reply_{int(time.time() * 1000)}.mp3"
        )
        return say, emotion, mp3, t_llm, time.perf_counter() - t, tools_used

    use_thinking = cfg.use_thinking_move and robot.emotions is not None
    with ThreadPoolExecutor(max_workers=2) as pool:
        cloud = pool.submit(_cloud_work)
        # 思考動作也丟到背景：它比雲端往返還久（thoughtful1 有 5.9 秒），
        # 讓它在主線程跑完的話反而變成瓶頸。雲端一回來就把它砍掉。
        mover = (
            pool.submit(robot.play_emotion, THINKING_MOVE, False, "🤔")
            if use_thinking
            else None
        )
        say, emotion, mp3, t_llm, t_tts, tools_used = cloud.result()
        if mover is not None:
            robot.cancel_move()
            mover.result()

    # 工具是在背景執行緒裡跑的，統一在這裡印，才不會跟思考動作的訊息交錯。
    timing = [f"LLM {t_llm:.2f}s"]
    for name, result, dt in tools_used:
        print(f"  🛠️ {name}（{dt:.2f}s）→ {result}")
        timing.append(f"工具 {dt:.2f}s")
    timing.append(f"TTS {t_tts:.2f}s")
    print(f"Reachy > {say}   [{' + '.join(timing)}]")

    history.append(
        {
            "role": "assistant",
            "content": json.dumps({"say": say, "emotion": emotion}, ensure_ascii=False),
        }
    )
    # 只留最近幾輪，避免 token 數與延遲一路往上爬。
    if len(history) > cfg.history_turns * 2:
        del history[: len(history) - cfg.history_turns * 2]

    # 動作與語音同時跑：情緒動作寫的是目標姿態，說話擺頭是疊加在那之上的
    # offset（見 CONTROL_CHAIN.md 的 IK 段），兩者不會互相蓋掉。
    # 串行播的話要多等一整段動作的時間（2~4 秒）。
    with ThreadPoolExecutor(max_workers=1) as pool:
        mover = pool.submit(robot.play_emotion, emotion)
        robot.speak(mp3)
        mover.result()

    robot.go_home()  # 每輪收在同一個姿態，下一輪才不會從奇怪的角度開始


def main() -> None:
    """解析參數並跑對話迴圈."""
    parser = argparse.ArgumentParser(
        description="Reachy Mini 雲端三段式對話 pipeline（STT → LLM → TTS → 動作）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "openai", "gemini"],
        default="auto",
        help="雲端 LLM 供應商（預設 auto：看 .env 裡填了哪把金鑰）",
    )
    parser.add_argument(
        "--model", default="", help="模型名稱（預設 gpt-4o-mini / gemini-2.0-flash）"
    )
    parser.add_argument(
        "--voice", default="zh-TW-HsiaoChenNeural", help="edge-tts 語音"
    )
    parser.add_argument(
        "--input",
        choices=["text", "mic"],
        default="text",
        help="輸入方式（麥克風故障中，預設打字）",
    )
    parser.add_argument(
        "--record-seconds", type=float, default=5.0, help="mic 模式每次錄音秒數"
    )
    parser.add_argument("--text", default="", help="只跑這一句就結束，不進入對話迴圈")
    parser.add_argument(
        "--no-robot", action="store_true", help="不連機器人，只跑雲端三段並存檔"
    )
    parser.add_argument(
        "--no-emotion", action="store_true", help="不播預錄情緒動作（跳過動作庫下載）"
    )
    parser.add_argument(
        "--no-thinking-move", action="store_true", help="等 LLM 時不播思考動作"
    )
    args = parser.parse_args()

    cfg = load_config(args)
    stt_label = "Whisper" if cfg.input_mode == "mic" else "打字"
    print(
        f"雲端 pipeline：{stt_label} → {cfg.provider}/{cfg.model} → "
        f"edge-tts/{cfg.voice} → 馬達"
    )
    if cfg.input_mode == "mic":
        print("⚠️  麥克風先前診斷為硬體故障，辨識結果全空時請先跑 test_microphone.py。")

    llm = LLM(cfg)
    robot = Robot(cfg)
    history: list[dict[str, str]] = []

    def _safe_turn(text: str) -> None:
        """跑一輪，但雲端出錯時只印訊息，不讓整個對話迴圈掛掉."""
        try:
            run_turn(cfg, llm, robot, history, text)
        except httpx.HTTPStatusError as e:
            body = e.response.text[:200]
            print(f"✗ 雲端 API 回錯：{e.response.status_code} {body}")
        except httpx.HTTPError as e:
            print(f"✗ 網路錯誤：{type(e).__name__}: {e}")

    try:
        if args.text:
            _safe_turn(args.text)
            return

        print("\n開始對話（輸入 exit 或按 Ctrl+C 結束）")
        while True:
            user_text = get_user_text(cfg, robot)
            if user_text is None:
                break
            if not user_text:
                continue
            _safe_turn(user_text)
    except KeyboardInterrupt:
        pass
    finally:
        print()
        llm.close()
        robot.close()
        print("收工。")


if __name__ == "__main__":
    main()
