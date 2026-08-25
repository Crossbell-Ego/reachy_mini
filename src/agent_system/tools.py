"""LLM 可呼叫的工具集：目前只有「查台北市即時天氣」.

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
Open-Meteo（https://open-meteo.com）：免金鑰、免註冊、非商業免費。
中央氣象署的 opendata API 資料更在地，但要申請授權碼，這裡刻意避開。

單獨測試：
    python src/agent_system/tools.py
"""

from __future__ import annotations

import time

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


# 工具白名單：名稱 → 給 LLM 看的說明。要加新工具就同時補這裡跟 TOOL_FUNCS。
TOOL_SPECS: dict[str, str] = {
    "weather_taipei": "查詢台北市此刻的天氣實況（氣溫、體感、濕度、風速、有沒有下雨）",
}

TOOL_FUNCS = {
    "weather_taipei": get_taipei_weather,
}


def tool_menu() -> str:
    """組出要塞進 system prompt 的工具清單."""
    return "\n".join(f"- {name}: {desc}" for name, desc in TOOL_SPECS.items())


def run_tool(name: str) -> str:
    """執行工具並回傳結果字串.

    網路或 API 出問題時不拋例外，改回一句話讓 LLM 自己去跟使用者說——
    對話中斷比講錯天氣糟糕得多。
    """
    func = TOOL_FUNCS.get(name)
    if func is None:
        return f"（沒有叫做 {name} 的工具）"
    try:
        return func()
    except httpx.HTTPError as e:
        return f"（查詢失敗：{type(e).__name__}，現在拿不到天氣資料）"
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
    print(f"[{time.perf_counter() - t0:.2f}s 走快取]")
