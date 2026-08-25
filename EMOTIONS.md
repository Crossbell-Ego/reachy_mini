# 🎭 Reachy Mini 表情與動作庫清單 (Emotions Library)

本清單整理自官方資料集 [`pollen-robotics/reachy-mini-emotions-library`](https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library)，共有 **85** 種預錄表情與動作可直接透過 SDK 調用。

可在線上預覽所有表情動畫：👉 [Reachy Mini Emotions Viewer](https://huggingface.co/spaces/RemiFabre/emotions)

---

## 💻 調用方式範例

```python
import asyncio
from reachy_mini import ReachyMini
from reachy_mini.motion.recorded_move import RecordedMoves

# 載入資料庫
emotions = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")

async def main():
    async with ReachyMini() as mini:
        # 播放指定動作 (例如 "happy" 或 "cheerful1")
        move = emotions.get("cheerful1")
        await mini.async_play_move(move, initial_goto_duration=1.0)

asyncio.run(main())
```

---

## 📋 完整動作清單

| 編號 | 動作名稱 (Move ID) | 時長 (秒) | 包含音效 | 中文情緒/動作說明 |
| :---: | :--- | :---: | :---: | :--- |
| 1 | `amazed1` | 3.42s | ✅ 是 | 驚嘆、讚嘆 |
| 2 | `anxiety1` | 8.12s | ✅ 是 | 焦慮不安 |
| 3 | `attentive1` | 4.28s | ✅ 是 | 專注專心 (1) |
| 4 | `attentive2` | 6.46s | ✅ 是 | 專注專心 (2) |
| 5 | `boredom1` | 15.70s | ✅ 是 | 無聊發呆 (1) |
| 6 | `boredom2` | 14.18s | ✅ 是 | 無聊發呆 (2) |
| 7 | `calming1` | 6.06s | ✅ 是 | 平靜、安撫 |
| 8 | `cheerful1` | 2.80s | ✅ 是 | 歡快高興 |
| 9 | `come1` | 3.16s | ✅ 是 | 招呼過來 |
| 10 | `confused1` | 7.88s | ✅ 是 | 疑惑困惑 |
| 11 | `contempt1` | 3.56s | ✅ 是 | 輕蔑、不屑 |
| 12 | `curious1` | 11.78s | ✅ 是 | 好奇打量 |
| 13 | `dance1` | 3.24s | ✅ 是 | 跳舞律動 (1) |
| 14 | `dance2` | 17.26s | ✅ 是 | 跳舞律動 (2) |
| 15 | `dance3` | 18.36s | ✅ 是 | 跳舞律動 (3) |
| 16 | `disgusted1` | 6.94s | ✅ 是 | 厭惡反感 |
| 17 | `displeased1` | 3.36s | ✅ 是 | 不悅不滿 (1) |
| 18 | `displeased2` | 2.94s | ✅ 是 | 不悅不滿 (2) |
| 19 | `downcast1` | 5.88s | ✅ 是 | 垂頭喪氣 |
| 20 | `dying1` | 5.68s | ✅ 是 | 沒電/倒下 |
| 21 | `electric1` | 3.50s | ✅ 是 | 觸電抽搐 |
| 22 | `enthusiastic1` | 2.72s | ✅ 是 | 熱情熱烈 (1) |
| 23 | `enthusiastic2` | 3.42s | ✅ 是 | 熱情熱烈 (2) |
| 24 | `exhausted1` | 18.26s | ✅ 是 | 精疲力竭 |
| 25 | `fear1` | 3.48s | ✅ 是 | 害怕恐懼 |
| 26 | `frustrated1` | 5.96s | ✅ 是 | 沮喪挫折 |
| 27 | `furious1` | 5.72s | ✅ 是 | 狂怒暴怒 |
| 28 | `go_away1` | 4.84s | ✅ 是 | 走開/拒絕 |
| 29 | `grateful1` | 2.50s | ✅ 是 | 感激感謝 |
| 30 | `helpful1` | 4.36s | ✅ 是 | 樂意幫忙 (1) |
| 31 | `helpful2` | 3.84s | ✅ 是 | 樂意幫忙 (2) |
| 32 | `impatient1` | 4.02s | ✅ 是 | 不耐煩 (1) |
| 33 | `impatient2` | 3.90s | ✅ 是 | 不耐煩 (2) |
| 34 | `incomprehensible2` | 3.44s | ✅ 是 | 無法理解/聽不懂 |
| 35 | `indifferent1` | 2.56s | ✅ 是 | 漠不關心/無所謂 |
| 36 | `inquiring1` | 2.14s | ✅ 是 | 詢問打聽 (1) |
| 37 | `inquiring2` | 2.58s | ✅ 是 | 詢問打聽 (2) |
| 38 | `inquiring3` | 2.92s | ✅ 是 | 詢問打聽 (3) |
| 39 | `irritated1` | 2.48s | ✅ 是 | 惱火煩躁 (1) |
| 40 | `irritated2` | 5.26s | ✅ 是 | 惱火煩躁 (2) |
| 41 | `laughing1` | 4.64s | ✅ 是 | 大笑 (1) |
| 42 | `laughing2` | 2.92s | ✅ 是 | 大笑 (2) |
| 43 | `lonely1` | 10.22s | ✅ 是 | 寂寞孤單 |
| 44 | `lost1` | 8.12s | ✅ 是 | 迷惘失落 |
| 45 | `loving1` | 5.60s | ✅ 是 | 喜愛/充滿愛意 |
| 46 | `mini-deep-sleep` | 15.98s | ✅ 是 | 深度睡眠 |
| 47 | `no1` | 2.68s | ✅ 是 | 搖頭說不 (普通) |
| 48 | `no_excited1` | 4.48s | ✅ 是 | 激動拒絕 (不!) |
| 49 | `no_sad1` | 7.04s | ✅ 是 | 傷心拒絕 (不...) |
| 50 | `oops1` | 2.46s | ✅ 是 | 糟糕/哎呀 (1) |
| 51 | `oops2` | 2.70s | ✅ 是 | 糟糕/哎呀 (2) |
| 52 | `proud1` | 3.76s | ✅ 是 | 驕傲自豪 (1) |
| 53 | `proud2` | 3.18s | ✅ 是 | 驕傲自豪 (2) |
| 54 | `proud3` | 3.36s | ✅ 是 | 驕傲自豪 (3) |
| 55 | `rage1` | 5.08s | ✅ 是 | 盛怒憤怒 |
| 56 | `relief1` | 5.00s | ✅ 是 | 鬆了一口氣 (1) |
| 57 | `relief2` | 6.90s | ✅ 是 | 鬆了一口氣 (2) |
| 58 | `reprimand1` | 4.70s | ✅ 是 | 斥責訓誡 (1) |
| 59 | `reprimand2` | 11.14s | ✅ 是 | 斥責訓誡 (2) |
| 60 | `reprimand3` | 4.28s | ✅ 是 | 斥責訓誡 (3) |
| 61 | `resigned1` | 4.74s | ✅ 是 | 無奈認命 |
| 62 | `sad1` | 9.02s | ✅ 是 | 傷心難過 (1) |
| 63 | `sad2` | 7.34s | ✅ 是 | 傷心難過 (2) |
| 64 | `scared1` | 7.20s | ✅ 是 | 受驚害怕 |
| 65 | `serenity1` | 4.58s | ✅ 是 | 寧靜祥和 |
| 66 | `shy1` | 7.80s | ✅ 是 | 害羞靦腆 |
| 67 | `sleep1` | 19.76s | ✅ 是 | 打瞌睡/睡覺 |
| 68 | `success1` | 2.26s | ✅ 是 | 成功慶祝 (1) |
| 69 | `success2` | 2.42s | ✅ 是 | 成功慶祝 (2) |
| 70 | `surprised1` | 2.48s | ✅ 是 | 驚訝意外 (1) |
| 71 | `surprised2` | 3.02s | ✅ 是 | 驚訝意外 (2) |
| 72 | `thoughtful1` | 5.90s | ✅ 是 | 深思熟慮 (1) |
| 73 | `thoughtful2` | 5.46s | ✅ 是 | 深思熟慮 (2) |
| 74 | `tired1` | 7.44s | ✅ 是 | 疲憊想睡 |
| 75 | `toc-toc-toc` | 13.37s | ✅ 是 | 敲門/敲擊聲 |
| 76 | `uncertain1` | 6.14s | ✅ 是 | 不確定/猶豫 |
| 77 | `uncomfortable1` | 6.02s | ✅ 是 | 不自在/難受 |
| 78 | `understanding1` | 3.94s | ✅ 是 | 理解贊同 (1) |
| 79 | `understanding2` | 2.62s | ✅ 是 | 理解贊同 (2) |
| 80 | `waiting` | 9.96s | ❌ 否 | 等待中 |
| 81 | `wake-mini-up` | 15.66s | ✅ 是 | 喚醒動作 |
| 82 | `welcoming1` | 3.46s | ✅ 是 | 熱情歡迎 (1) |
| 83 | `welcoming2` | 4.32s | ✅ 是 | 熱情歡迎 (2) |
| 84 | `yes1` | 3.40s | ✅ 是 | 點頭答應 (普通) |
| 85 | `yes_sad1` | 5.08s | ✅ 是 | 無奈點頭答應 |
