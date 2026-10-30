#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
球吧体育数据抓取 - 独立运行入口。

供 GitHub Actions 定时调用：实例化 qiuba.py 里的 Spider，
抓取分类 + 直播中的比赛，输出 data/matches.json。

qiuba.py 顶部有 `from base.spider import Spider`（原本依赖 TVBox/py
框架）。为了让它能独立运行，这里在导入 qiuba 之前先向 sys.modules
注入一个最小的 base.spider 桩模块，因此不再需要 base/ 目录。
"""
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


def _install_spider_stub():
    """注入最小的 base.spider 桩模块，使 qiuba.py 可独立导入。"""
    if "base.spider" in sys.modules:
        return
    base_pkg = types.ModuleType("base")
    base_pkg.__path__ = []  # 标记为包，避免后续导入歧义
    spider_mod = types.ModuleType("base.spider")

    class Spider:
        def init(self, extend=""):
            pass

    spider_mod.Spider = Spider
    base_pkg.spider = spider_mod
    sys.modules["base"] = base_pkg
    sys.modules["base.spider"] = spider_mod


_install_spider_stub()

from qiuba import Spider  # noqa: E402


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
