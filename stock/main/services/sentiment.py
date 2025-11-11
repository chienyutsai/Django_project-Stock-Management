# -*- coding: utf-8 -*-
import re

# ---- 繁簡轉換（建議：繁->繁 或直接關掉）----
try:
    from opencc import OpenCC
    _cc = OpenCC('tw2tw')   # ← 改這行（原本 tw2sp 會把「買氣續旺」轉成簡體）
    def _norm(s: str) -> str:
        return _cc.convert(s or "")
except Exception:
    def _norm(s: str) -> str:
        return s or ""

# ---- 金融情緒詞庫（可自行擴充）----
POS_PHRASES = [
    "買氣續旺", "買氣回升", "大漲", "上漲", "勁揚", "利多", "創高", "看好",
    "獲利成長", "優於預期", "上修評等", "調升評等", "增持", "上調目標價",
    "需求強勁", "營收創新高", "創歷史新高"
]
NEG_PHRASES = [
    "暴跌", "下跌", "利空", "裁員", "虧損擴大", "不如預期",
    "調降評等", "減持", "下調目標價", "需求轉弱", "營收衰退"
]
NEGATORS = ["不", "未", "無", "別", "難以", "恐", "未如", "並未"]
BOOSTERS = ["大幅", "顯著", "強勁", "明顯", "急遽", "明顯"]
DOWNGRADERS = ["些微", "輕微", "溫和", "小幅"]

# 事先把詞組轉成 regex，做「子字串」匹配
def _compile(phrases):
    # 避免括號/加號等字元影響
    escaped = [re.escape(p) for p in phrases]
    return re.compile("|".join(escaped))

_RE_POS = _compile(POS_PHRASES)
_RE_NEG = _compile(NEG_PHRASES)
_RE_NEGATOR = _compile(NEGATORS)
_RE_BOOST = _compile(BOOSTERS)
_RE_DOWN = _compile(DOWNGRADERS)

def simple_score(text: str):
    """
    回傳 (label, score)
    - label: 'positive' | 'neutral' | 'negative'
    - score: 0~1 信心值
    """
    if not text:
        return ("neutral", 0.5)

    t = _norm(text)

    pos_hits = len(_RE_POS.findall(t))
    neg_hits = len(_RE_NEG.findall(t))
    base = float(pos_hits - neg_hits)

    # 否定詞：簡單反轉
    if _RE_NEGATOR.search(t):
        base = -base

    # 程度詞：加權
    if _RE_BOOST.search(t):
        base *= 1.3
    if _RE_DOWN.search(t):
        base *= 0.7

    if base > 0.3:
        return ("positive", min(0.95, 0.6 + base * 0.1))
    if base < -0.3:
        return ("negative", min(0.95, 0.6 + abs(base) * 0.1))
    return ("neutral", 0.5)
