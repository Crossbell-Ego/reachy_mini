"""LLM 可呼叫的工具集：台北天氣、上網搜尋、本機行事曆.

為什麼不用原生 function calling
--------------------------------
cloud_voice_chat.py 走的是純 REST、不裝任何官方 SDK，而且已經用
`response_format` / `responseMimeType` 把輸出鎖成 JSON。OpenAI 的 tools 與
Gemini 的 functionDeclarations 兩家格式不同（Gemini 甚至不允許同時指定
responseMimeType=application/json 與 functionDeclarations），接下去等於要寫兩套
分支。改成在既有的 JSON 輸出裡多一個 `tool` 欄位，兩家共用同一條路，
程式碼少一半，代價只是模型偶爾會亂填代號——那個用白名單擋掉就好。

資料來源
--------
- weather_taipei：Open-Meteo（https://open-meteo.com），免金鑰、免註冊。
  中央氣象署的 opendata 資料更在地，但要申請授權碼，這裡刻意避開。
- web_search：DuckDuckGo lite，免金鑰。剖 HTML，對方改版就會壞（有防呆）。
- calendar：本機的 calendar.json，沒有任何雲端服務、沒有帳號。
- look（VISION_TOOL）：鏡頭畫面。這個模組只放它的名字跟說明，實際抓畫面、
  塞進雲端 LLM 的多模態輸入，都在 cloud_voice_chat.py 的 LLM._reply_with_vision()
  ——那裡才拿得到機器人的 Robot 物件，這裡刻意不依賴機器人。

單獨測試：
    python src/agent_system/tools.py              # 三個工具各跑一遍
    python src/agent_system/tools.py "搜尋關鍵字"
"""

from __future__ import annotations

import html
import json
import re
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

import httpx

# 台北市中心（中正區）。Open-Meteo 會自動吸附到最近的網格點。
TAIPEI_LAT, TAIPEI_LON = 25.0330, 121.5654

# 觀測資料每 15 分鐘才更新一次（回應裡的 interval=900），
# 同一輪對話裡連問好幾次沒必要每次都打 API，快取 5 分鐘。
_CACHE_TTL = 300.0
_cache: tuple[float, str] | None = None

# WMO 天氣代碼 → 中文。挑常見的翻，查不到就退回「天氣代碼 N」。
# 完整表格見 https://open-meteo.com/en/docs（WMO Weather interpretation codes）
WMO_ZH: dict[int, str] = {
    0: "晴朗",
    1: "大致晴朗",
    2: "局部多雲",
    3: "陰天",
    45: "有霧",
    48: "濃霧",
    51: "毛毛雨",
    53: "毛毛雨",
    55: "毛毛雨偏大",
    56: "凍毛毛雨",
    57: "凍毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "凍雨",
    67: "凍雨偏大",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "短暫陣雨",
    81: "陣雨",
    82: "強陣雨",
    85: "陣雪",
    86: "強陣雪",
    95: "雷陣雨",
    96: "雷陣雨夾冰雹",
    99: "強雷陣雨夾冰雹",
}


def get_taipei_weather() -> str:
    """查台北市當下天氣，回傳一句給 LLM 讀的中文摘要.

    刻意回「一句話」而不是 JSON：這段文字會被塞回對話歷史，
    講白話的話模型比較不會照抄欄位名，唸出來也自然。
    """
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL:
        return _cache[1]

    resp = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": TAIPEI_LAT,
            "longitude": TAIPEI_LON,
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "precipitation,weather_code,wind_speed_10m"
            ),
            "timezone": "Asia/Taipei",
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    cur = resp.json()["current"]

    code = int(cur["weather_code"])
    sky = WMO_ZH.get(code, f"天氣代碼 {code}")
    temp = float(cur["temperature_2m"])
    feels = float(cur["apparent_temperature"])
    humidity = int(cur["relative_humidity_2m"])
    wind = float(cur["wind_speed_10m"])
    rain = float(cur["precipitation"])
    hhmm = str(cur["time"])[11:16]  # "2026-08-25T18:15" → "18:15"

    parts = [
        f"台北市 {hhmm} 實況：{sky}",
        f"氣溫 {temp:.0f} 度",
        f"體感 {feels:.0f} 度",
        f"濕度 {humidity}%",
        f"風速每小時 {wind:.0f} 公里",
    ]
    if rain > 0:
        parts.append(f"目前有降雨，時雨量 {rain:.1f} 毫米")
    summary = "，".join(parts) + "。"

    _cache = (now, summary)
    return summary


# --- 上網搜尋 -------------------------------------------------------------

# DuckDuckGo 的 lite 版：純 HTML、免金鑰、免註冊，回應也小（20~30 KB）。
# 為什麼不用 Google/Bing：都要 API 金鑰跟帳單。為什麼不用 api.duckduckgo.com：
# 那個是 Instant Answer，只有百科式問題答得出來，一般查詢回空的。
# 代價是這裡在剖析 HTML，DuckDuckGo 改版就會壞——所以剖不出東西時回一句話，
# 不拋例外（見 _RESULT_RE 附近）。
SEARCH_URL = "https://lite.duckduckgo.com/lite/"
SEARCH_RESULTS = 4  # 給 LLM 幾筆。再多只是塞 token，模型也只看前面幾筆。
SNIPPET_CHARS = 110  # 每筆摘要截這麼長，整包大約 500 字，塞得進 context。
_SEARCH_TTL = 300.0
_search_cache: dict[str, tuple[float, str]] = {}

# 不帶 UA 會被擋。裝成一般瀏覽器，其他 header 沒必要。
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

_TITLE_RE = re.compile(r"<a[^>]+class=['\"]result-link['\"][^>]*>(.*?)</a>", re.DOTALL)
_SNIPPET_RE = re.compile(r"class=['\"]result-snippet['\"]>(.*?)</td>", re.DOTALL)
_DOMAIN_RE = re.compile(r"class=['\"]link-text['\"]>(.*?)</span>", re.DOTALL)


def _clean(raw: str) -> str:
    """把一段 HTML 變成乾淨的一行純文字."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())


def web_search(query: str) -> str:
    """用關鍵字上網搜尋，回傳前幾筆結果的標題、來源網站與摘要.

    回的是「搜尋結果摘要」，不是網頁內文——刻意不去抓每個連結的全文：
    那會多花好幾秒，而且大部分頁面是 JS 算出來的，抓回來也是空殼。
    摘要通常足夠讓模型回答，不夠的話它會照實說查到的資訊有限。
    """
    query = query.strip()
    if not query:
        return "（沒有給搜尋關鍵字，沒辦法查）"

    now = time.monotonic()
    hit = _search_cache.get(query)
    if hit is not None and now - hit[0] < _SEARCH_TTL:
        return hit[1]

    resp = httpx.get(
        SEARCH_URL,
        params={"q": query},
        headers={"User-Agent": _UA},
        timeout=12.0,
        follow_redirects=True,
    )
    resp.raise_for_status()
    page = resp.text

    titles = [_clean(t) for t in _TITLE_RE.findall(page)]
    snippets = [_clean(s) for s in _SNIPPET_RE.findall(page)]
    domains = [_clean(d) for d in _DOMAIN_RE.findall(page)]
    if not titles:
        # 查無結果、被限流、或版面改了——對 LLM 來說都是同一件事：沒資料。
        return f"（「{query}」查不到結果，可能是關鍵字太冷門或搜尋服務暫時擋住了）"

    lines = [f"「{query}」的網路搜尋結果（來源 DuckDuckGo）："]
    for i, title in enumerate(titles[:SEARCH_RESULTS]):
        site = f"（{domains[i]}）" if i < len(domains) else ""
        text = snippets[i][:SNIPPET_CHARS] if i < len(snippets) else ""
        lines.append(f"{i + 1}. {title}{site}：{text}")
    summary = "\n".join(lines)

    # 同一輪追問常常查一樣的東西。快取小一點，過期的順手清掉就好。
    if len(_search_cache) > 32:
        _search_cache.clear()
    _search_cache[query] = (now, summary)
    return summary


# --- 行事曆 ---------------------------------------------------------------

# 教學示範用的行事曆：整份就存在這支程式旁邊的一個 JSON 檔，沒有 Google
# Calendar、沒有任何雲端服務、也不需要帳號。要看資料就直接打開那個檔。
#
# 為什麼是檔案而不是純記憶體：伺服器重開一次就整份不見的話，示範到一半重開
# 就得重排一次行程。檔案壞掉或讀不到時就當作空的，不讓工具把對話弄斷。
CALENDAR_PATH = Path(__file__).resolve().parent / "calendar.json"
CALENDAR_MAX = 200  # 示範用，不必無上限；滿了就叫使用者自己刪。

# "2026-09-02 15:00 牙醫回診" / "2026-09-02 牙醫回診"（時間可省略）
#
# 日期後面**不要**先寫 \s*：那樣空白會先被吃掉，時間那組因為找不到分隔符就直接
# 跳過，整個 "10:00 與客戶開會" 都變成標題（實測踩過）。分隔符併進可選群組裡，
# 引擎才會優先試著把時間吃下來。
_EVENT_RE = re.compile(
    r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})"
    r"(?:[\sT]+(\d{1,2}):(\d{2}))?"
    r"[\s,，、]*(.+?)\s*$"
)
_WEEK_ZH = "一二三四五六日"


def _load_calendar() -> list[dict[str, str]]:
    """讀出整份行事曆（檔案不在或壞掉都回空的）."""
    try:
        data = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [e for e in data if isinstance(e, dict) and e.get("date")]


def _save_calendar(events: list[dict[str, str]]) -> None:
    """寫回檔案，順便照日期時間排好."""
    events.sort(key=lambda e: (e.get("date", ""), e.get("time", "")))
    try:
        CALENDAR_PATH.write_text(
            json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError as e:
        print(f"！ 行事曆存檔失敗：{e}")


def _fmt_event(e: dict[str, str]) -> str:
    """一則行程排成一行中文."""
    when = e["date"]
    try:
        day = datetime.strptime(when, "%Y-%m-%d")
        when += f"（週{_WEEK_ZH[day.weekday()]}）"
    except ValueError:
        pass  # 手動改壞 calendar.json 也不該讓工具爆掉
    return " ".join(x for x in (when, e.get("time", ""), e.get("title", "")) if x)


def _cal_add(query: str) -> str:
    """新增一則行程。query 格式：YYYY-MM-DD [HH:MM] 事件描述."""
    m = _EVENT_RE.match(query)
    if not m:
        return (
            "（格式看不懂，沒有加進去。要寫成「YYYY-MM-DD HH:MM 事情」，"
            "例如 2026-09-02 15:00 牙醫回診；時間可以省略。）"
        )
    year, month, day, hour, minute, title = m.groups()
    try:
        date = datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
    except ValueError:
        return f"（{year}-{month}-{day} 不是有效的日期，沒有加進去。）"
    time_ = f"{int(hour):02d}:{minute}" if hour else ""

    events = _load_calendar()
    if len(events) >= CALENDAR_MAX:
        return f"（行事曆已經有 {CALENDAR_MAX} 筆，先刪掉一些再加。）"
    event = {"date": date, "time": time_, "title": title}
    events.append(event)
    _save_calendar(events)
    return f"已加入行事曆：{_fmt_event(event)}。目前共 {len(events)} 筆。"


def _cal_list(query: str = "") -> str:
    """查行事曆。query 留空=今天起的所有行程；也可給 YYYY-MM-DD 或 YYYY-MM."""
    events = _load_calendar()
    if not events:
        return "（行事曆是空的，目前沒有任何行程。）"

    key = query.strip()
    if key:
        hits = [e for e in events if e["date"].startswith(key)]
        title = f"「{key}」的行程"
    else:
        # 不給條件時只看今天以後：過期的行程講出來只是噪音。
        today = datetime.now().strftime("%Y-%m-%d")
        hits = [e for e in events if e["date"] >= today]
        title = "今天起的行程"
    if not hits:
        return f"（{title}：沒有安排。）"
    lines = [f"{title}（共 {len(hits)} 筆）："]
    lines += [f"- {_fmt_event(e)}" for e in hits[:20]]
    if len(hits) > 20:
        lines.append(f"…另外還有 {len(hits) - 20} 筆。")
    return "\n".join(lines)


def _cal_remove(query: str) -> str:
    """刪行程。query 是關鍵字或日期，只對得上一筆時才真的刪."""
    key = query.strip()
    if not key:
        return "（沒說要刪哪一筆。）"
    events = _load_calendar()
    # 逐詞比對整行（日期＋時間＋標題），不是拿整串去比標題：模型很常把查到的
    # 那一行原封不動貼回來刪（"2026-09-02 15:00 牙醫回診"），只比標題會全部落空。
    words = key.split()
    hits = [e for e in events if all(w in _fmt_event(e) for w in words)]
    if not hits:
        return f"（行事曆裡找不到跟「{key}」有關的行程。）"
    if len(hits) > 1:
        # 對到好幾筆就不動手，列出來讓使用者說清楚是哪一筆——
        # 刪錯行程比多問一句糟糕。
        listed = "；".join(_fmt_event(e) for e in hits[:5])
        return f"（「{key}」對到 {len(hits)} 筆：{listed}。請說明是哪一筆。）"
    events.remove(hits[0])
    _save_calendar(events)
    return f"已刪掉：{_fmt_event(hits[0])}。剩下 {len(events)} 筆。"


# 動作代號 → 處理函式。中文與常見寫法都收，模型很愛自己換句話說。
_CAL_ACTIONS: dict[str, Callable[[str], str]] = {
    "add": _cal_add,
    "新增": _cal_add,
    "list": _cal_list,
    "查詢": _cal_list,
    "remove": _cal_remove,
    "delete": _cal_remove,
    "del": _cal_remove,
    "刪除": _cal_remove,
}


def calendar(query: str) -> str:
    """行事曆總入口：query 的第一個詞是動作，其餘是內容.

    三件事（新增／查詢／刪除）合成一個工具而不是三個：工具選單每多一個就多佔
    prompt 的篇幅，模型要挑的選項也變多；合起來之後模型只要決定「要不要碰行事
    曆」，動作寫在 query 裡就好。代價是 query 的格式要自己剖，就是下面這幾行。

        add 2026-09-02 15:00 牙醫回診
        list / list 2026-09-02 / list 2026-09
        remove 牙醫
    """
    text = query.strip()
    if not text:
        return _cal_list("")  # 什麼都沒說 = 看一下有什麼行程，最安全的預設

    head, _, rest = text.partition(" ")
    action = _CAL_ACTIONS.get(head.lower().strip("：:，,"))
    if action is None:
        # 模型偶爾會忘記寫動作。看得出是一整筆行程（有日期又有內容）就當新增，
        # 其他（只給日期、只給月份）就當查詢——兩種都比回一句「格式錯誤」有用。
        return _cal_add(text) if _EVENT_RE.match(text) else _cal_list(text)
    return action(rest.strip())


def _today_line() -> str:
    """給 system prompt 的今日日期。模型不知道今天幾號就換算不了「下週三」."""
    now = datetime.now()
    return (
        f"（今天是 {now:%Y-%m-%d} 星期{_WEEK_ZH[now.weekday()]} {now:%H:%M}。"
        "「明天」「下週三」這種說法請自己換算成 YYYY-MM-DD 再填給工具。）"
    )


# 「看鏡頭」這個工具的代號。獨立成常數是因為它跟其他工具走的路不一樣：
# cloud_voice_chat.py 的 LLM.reply() 會攔在 run_tool() 之前特殊處理——抓一張
# 鏡頭畫面，直接餵給模型的視覺能力，而不是像其他工具那樣回一段文字。
# 這裡只負責讓它出現在選單與開關清單裡，實際抓畫面在 tools.py 拿不到
# （機器人是 cloud_voice_chat.Robot 管的，這個模組刻意不依賴機器人）。
VISION_TOOL = "look"

# 工具白名單：名稱 → 給 LLM 看的說明。要加新工具就同時補這裡跟 TOOL_FUNCS。
TOOL_SPECS: dict[str, str] = {
    "weather_taipei": "查詢台北市此刻的天氣實況（氣溫、體感、濕度、風速、有沒有下雨）",
    "web_search": (
        "用關鍵字上網搜尋，拿回前幾筆結果的標題與摘要。"
        "新聞、股價、店家、產品規格、你不確定或可能過時的事實都用這個查"
    ),
    "calendar": (
        "使用者的行事曆，可以新增、查詢、刪除行程（存在本機，不會上傳到任何地方）。"
        "query 的第一個字是動作：add 新增、list 查詢、remove 刪除。"
        "問「最近有什麼事」用 list 且後面不接東西，指名某一天或某個月才補日期"
    ),
    VISION_TOOL: (
        "打開鏡頭看一眼面前的畫面，用視覺直接理解現在看到什麼（人、物品、場景等）。"
        "使用者問「你看到什麼」「這是什麼」「我手上拿的是什麼」之類跟眼前畫面有關的問題時用這個"
    ),
}

# 要填 query 才能跑的工具。cloud_voice_chat.py 的 SYSTEM_PROMPT 講了 query 欄位，
# 這裡負責在工具選單上標出誰要填、格式長什麼樣。
TOOL_NEEDS_QUERY: set[str] = {"web_search"}

# query 的格式範例。有寫在這裡的工具，介面上「試跑」才會出現輸入框；
# 沒被 TOOL_NEEDS_QUERY 列到的（calendar）表示可以留空。
TOOL_QUERY_HINT: dict[str, str] = {
    "web_search": "台北 捷運 票價",
    "calendar": "add 2026-09-02 15:00 牙醫回診／list／list 2026-09／remove 牙醫",
}

# 統一簽章成「吃一個字串、回一個字串」，run_tool 就不必分兩種呼叫方式。
# 不需要關鍵字的工具直接把它丟掉。
TOOL_FUNCS: dict[str, Callable[[str], str]] = {
    "weather_taipei": lambda _query: get_taipei_weather(),
    "web_search": web_search,
    "calendar": calendar,
    # 正常對話不會走到這裡（cloud_voice_chat.py 在呼叫 run_tool 之前就攔截掉
    # VISION_TOOL 了），這條只是給 run_tool() 一個不會 KeyError 的安全網——
    # 例如有人在沒有機器人、沒有這段特殊處理的地方直接呼叫 run_tool("look")。
    VISION_TOOL: lambda _query: (
        "（鏡頭功能只在完整的語音／網頁對話 pipeline 裡才能用，"
        "這裡「試跑」看不到畫面。）"
    ),
}


def tool_menu(names: Iterable[str] | None = None) -> str:
    """組出要塞進 system prompt 的工具清單.

    names 給定時只列出那幾個（web_ui 讓使用者逐個關掉工具用）。全部關掉時要
    明講「沒有工具」——留一個空白的「可用工具：」標題，模型會自己編一個代號。

    沒有工具時**不要**叫模型說「我查不到」：這個介面是拿來教學的，關掉工具的
    重點就是讓學員看到 LLM 憑印象講得多順、又錯得多離譜。加了但書就演不出來。
    """
    items = (
        TOOL_SPECS
        if names is None
        else {n: d for n, d in TOOL_SPECS.items() if n in set(names)}
    )
    if not items:
        return (
            "（目前沒有任何工具可用。所有問題都直接用你自己知道的回答，"
            "不要提到工具、不要說你查不到，也不要加上「可能不是最新」之類的但書。）"
        )
    lines = []
    for name, desc in items.items():
        hint = TOOL_QUERY_HINT.get(name)
        if hint is None:
            lines.append(f"- {name}: {desc}")
        else:
            need = "要" if name in TOOL_NEEDS_QUERY else "可"
            lines.append(f"- {name}: {desc}（{need}填 query，例：{hint}）")
    # 行事曆要換算「明天」「下週三」，模型得先知道今天幾號才有辦法算。
    if "calendar" in items:
        lines.append(_today_line())
    return "\n".join(lines)


def run_tool(name: str, query: str = "") -> str:
    """執行工具並回傳結果字串.

    網路或 API 出問題時不拋例外，改回一句話讓 LLM 自己去跟使用者說——
    對話中斷比講錯天氣糟糕得多。
    """
    func = TOOL_FUNCS.get(name)
    if func is None:
        return f"（沒有叫做 {name} 的工具）"
    try:
        return func(query)
    except httpx.HTTPError as e:
        return f"（查詢失敗：{type(e).__name__}，現在拿不到資料）"
    except (KeyError, ValueError, TypeError) as e:
        return f"（回傳格式看不懂：{type(e).__name__}）"


if __name__ == "__main__":
    import sys

    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    t0 = time.perf_counter()
    print(run_tool("weather_taipei"))
    print(f"[{time.perf_counter() - t0:.2f}s]")
    t0 = time.perf_counter()
    print(run_tool("weather_taipei"))
    print(f"[{time.perf_counter() - t0:.2f}s 走快取]\n")

    q = sys.argv[1] if len(sys.argv) > 1 else "台北 捷運 票價"
    t0 = time.perf_counter()
    print(run_tool("web_search", q))
    print(f"[{time.perf_counter() - t0:.2f}s]\n")

    # 行事曆：加一筆、查、刪掉，跑完不留痕跡（示範用的假資料）。
    print(tool_menu(["calendar"]))
    for _q in (
        "add 2026-09-02 15:00 tools.py 自我測試",
        "add 亂寫的格式",
        "list 2026-09",
        "2026-09-02 09:00 沒寫動作的新增",  # 沒動作但看得出是一整筆 → 當新增
        "list",
        "remove 自我測試",
        "remove 沒寫動作",
    ):
        print(f"  {_q!r} -> {run_tool('calendar', _q)}")
