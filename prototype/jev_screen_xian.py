# -*- coding: utf-8 -*-
"""jev 筛图：对每张候选图评分（西安 3 日行程点匹配度 1-10）。
key 从 jev-demo/.env 读取，不打印。timeout 2000ms，总调用 ≤20（含重试）。
用法: python jev_screen_xian.py  → 输出 JSON 结果到 stdout。
"""
import json, os, sys, time, urllib.request

# --- key：只从 .env 读取进内存，绝不输出 ---
env = {}
with open(r"D:\aiLocal\jev-demo\.env", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
KEY = env["TYPESAFE_API_KEY"]
RETRY = os.environ.get("JEV_RETRY", "1") != "0"  # 首轮结果因 GBK 编码丢失，复跑设 0 以守住 20 次总量

URL = "https://api.typesafe.ai/v1/systemone"
TIMEOUT_MS = 2000
MAX_CALLS = 20

# 候选图：vision 描述（宿主 AI 亲眼核图所得）+ 笔记来源 + 目标行程点
CANDIDATES = [
    {"img": "xian-bingmayong.jpg", "spot": "Day1 兵马俑",
     "vision": "兵马俑一号坑展厅全景拼贴：秦始皇帝陵博物院正门、一号坑拱形大棚下成千上百尊陶俑军阵俯瞰、跪姿与站姿陶俑近景特写",
     "note": "《西安📍3天2夜 精华路线保姆级逛吃住攻略》@奶罐糖环游记（Day3 兵马俑→丽山园→华清宫）"},
    {"img": "xian-bingmayong-alt.jpg", "spot": "Day1 兵马俑",
     "vision": "秦始皇兵马俑博物馆：跪射俑彩色特写在展柜中、一号坑陶俑排布与编号、大棚全景",
     "note": "《西安3-5天保姆级攻略｜吃玩住行一篇搞定✨》@爱分享｜大知闲闲（Day1 兵马俑→丽山园→华清宫）"},
    {"img": "xian-huaqing-palace.jpg", "spot": "Day1 华清宫",
     "vision": "华清宫园区：贵妃出浴白色雕像喷泉、唐代海棠汤遗址建筑、园林池水与骊山绿树倒影",
     "note": "《西安📍3天2夜 精华路线保姆级逛吃住攻略》@奶罐糖环游记（Day3 华清宫）"},
    {"img": "xian-huiminjie.jpg", "spot": "Day1 回民街",
     "vision": "回民街街景：老米家泡馍等招牌悬匾、西安钟楼鸡、biangbiang面老字号店铺招牌与树影下的美食街人流",
     "note": "《西安📍3天2夜 精华路线保姆级逛吃住攻略》@奶罐糖环游记（Day1 回民街）"},
    {"img": "xian-shanxi-history-museum.jpg", "spot": "Day2 陕历博",
     "vision": "陕西历史博物馆展厅文物拼贴：唐代舞马衔杯银壶、三彩武士俑、青釉提梁壶等馆藏珍品特写",
     "note": "《西安📍3天2夜 精华路线保姆级逛吃住攻略》@奶罐糖环游记（Day2 陕西历史博物馆）"},
    {"img": "xian-dayanta.jpg", "spot": "Day2 大雁塔",
     "vision": "大雁塔：塔身与玄奘雕像同框、大慈恩寺匾额、檐角铜铃与大雁塔塔尖特写",
     "note": "《西安📍3天2夜 精华路线保姆级逛吃住攻略》@奶罐糖环游记（Day2 大雁塔）"},
    {"img": "xian-datang-everbright.jpg", "spot": "Day2 大唐不夜城",
     "vision": "大唐不夜城夜景：西安年·最中国红色灯光牌坊、贞观之治群雕、李白与玄奘雕塑在金色灯光下的夜景街区",
     "note": "《西安📍3天2夜 精华路线保姆级逛吃住攻略》@奶罐糖环游记（Day2 大唐不夜城）"},
    {"img": "xian-citywall.jpg", "spot": "Day3 城墙",
     "vision": "西安城墙：永宁门牌匾门楼、城墙砖石步道与红色灯笼旌旗、角楼箭楼城门洞",
     "note": "《西安📍3天2夜 精华路线保姆级逛吃住攻略》@奶罐糖环游记（Day1 西安城墙）"},
    {"img": "xian-bell-drum-tower.jpg", "spot": "Day3 钟鼓楼",
     "vision": "钟楼鼓楼夜景拼贴：钟楼金顶绿瓦夜景、文武盛地匾额的鼓楼城台、夜晚车流灯光环绕的钟楼全景",
     "note": "《西安📍3天2夜 精华路线保姆级逛吃住攻略》@奶罐糖环游记（Day1 钟楼→鼓楼）"},
    {"img": "xian-sajinqiao.jpg", "spot": "Day3 洒金桥",
     "vision": "洒金桥美食街：洒金桥牌匾浮雕、羊肉泡馍特写、酸汤饺子与金桥裡洒金桥特色美食街红色门头",
     "note": "《西安📍3天2夜 精华路线保姆级逛吃住攻略》@奶罐糖环游记（Day1 洒金桥）"},
]

BODY_TMPL = {
    "model": "jev-latest",  # 必填：成都试点踩过缺 model 字段 422 的坑
    "state": "",
    "questions": {
        "match": {
            "type": "score",
            "instructions": "这张候选图与目标行程点的匹配度评分（1-10）。图描述与行程点语义一致、构图清晰、能代表该景点给攻略配图 → 高分；无关、拼贴过乱、主体不符 → 低分。",
            "criteria": [
                "1-3 完全不符或主体错误",
                "4-6 部分相关但辨识度低",
                "7-8 相关且可辨识，适合做行程点配图",
                "9-10 高度契合、主体清晰、出片",
            ],
        }
    },
}

calls = 0
results = []
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jev_xian_cache.jsonl")
done = {}
if os.path.exists(CACHE):
    with open(CACHE, encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            done[o["img"]] = o
for c in CANDIDATES:
    if c["img"] in done:  # 已评分，直接复用（防崩溃重跑重复调用）
        results.append(done[c["img"]])
        continue
    if calls >= MAX_CALLS:
        results.append({**c, "jev": None, "err": "call budget exhausted"})
        continue
    body = dict(BODY_TMPL)
    body["state"] = (
        "行程点：{}（西安 3 日游攻略配图筛选）。\n"
        "候选图内容（vision 描述）：{}\n"
        "图片来源笔记：{}".format(c["spot"], c["vision"], c["note"])
    )
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    attempt, score, err, ms = 0, None, None, None
    while attempt < (2 if RETRY else 1) and calls < MAX_CALLS:  # RETRY=0 时单次不重试
        attempt += 1
        calls += 1
        req = urllib.request.Request(
            URL, data=payload, method="POST",
            headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
        )
        try:
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=TIMEOUT_MS / 1000) as r:
                raw = json.loads(r.read().decode("utf-8"))
            ms = int((time.time() - t0) * 1000)
            answers = raw.get("answers") or raw.get("result", {}).get("answers") or {}
            m = answers.get("match")
            score = m.get("score") if isinstance(m, dict) else m
            err = None
            break
        except Exception as e:
            err = "{}: {}".format(type(e).__name__, str(e)[:120])
    rec = {**c, "jev": score, "err": err, "attempts": attempt, "ms": ms}
    results.append(rec)
    with open(CACHE, "a", encoding="utf-8") as f:  # 逐条落盘，崩溃可续
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

print(json.dumps({"calls": calls, "limit": MAX_CALLS, "results": results},
                 ensure_ascii=False, indent=1))
