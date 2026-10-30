#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
球吧体育数据抓取 - 独立运行入口。

供 GitHub Actions 定时调用：实例化 qiuba.py 里的 Spider，
抓取分类 + 直播中的比赛，输出 data/matches.json。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from qiuba import Spider  # noqa: E402  (需要 base/spider.py 桩模块)


def main():
    sp = Spider()
    sp.init("")

    now = datetime.now(timezone(timedelta(hours=8)))
    result = {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "home": {},
        "categories": [],
    }

    # 首页：分类列表 + 正在直播的比赛
    try:
        result["home"] = sp.homeContent({})
    except Exception as e:  # noqa: BLE001
        result["home"] = {"error": str(e)}

    # 每个分类抓第一页
    for c in result["home"].get("class", []):
        tid = c.get("type_id", "")
        tname = c.get("type_name", "")
        try:
            cat = sp.categoryContent(tid, "1", {}, {})
            result["categories"].append({
                "type_id": tid,
                "type_name": tname,
                "total": cat.get("total", 0),
                "list": cat.get("list", []),
            })
        except Exception as e:  # noqa: BLE001
            result["categories"].append({
                "type_id": tid, "type_name": tname, "error": str(e),
            })

    out_dir = os.path.join(BASE_DIR, "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "matches.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    live_count = len(result["home"].get("list", []))
    print(f"OK: {out_path} | 直播中 {live_count} 场 | 分类 {len(result['categories'])} 个")


if __name__ == "__main__":
    main()
