#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
球吧体育数据抓取 - 独立运行入口。

供 GitHub Actions 定时调用：实例化 qiuba.py 里的 Spider，
抓取分类 + 直播中的比赛，输出：
  - data/matches.json  比赛数据（分类、直播列表）
  - data/qiuba.m3u     可播放的直播源列表（每小时刷新，地址多为限时签名）

qiuba.py 顶部有 `from base.spider import Spider`（原本依赖 TVBox/py
框架）。为了让它能独立运行，这里在导入 qiuba 之前先向 sys.modules
注入一个最小的 base.spider 桩模块，因此不再需要 base/ 目录。

网络说明：requests 默认读取 HTTP_PROXY / HTTPS_PROXY 环境变量，
workflow 里配置的国内代理会自动生效，无需改代码。
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


def fetch_matches_json(sp):
    """抓取首页 + 各分类数据，返回可序列化的 dict。"""
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

    return result


def build_m3u(sp):
    """解析所有未结束比赛的直播信号真实地址，返回 (m3u文本, 收录条数)。

    复用 qiuba.py 自带的 _get / _post / _decode_play_url 逻辑：
    signals 接口拿信号源 -> play/session 接口拿加密地址 -> 解出 m3u8。
    """
    lines = ["#EXTM3U"]
    count = 0

    data = sp._get(f"{sp.api}/matches?page=1&page_size=100")
    items = data.get("items", []) if isinstance(data, dict) else []

    for item in items:
        if item.get("status") == "finished":
            continue
        match_id = str(item.get("id", ""))
        if not match_id:
            continue
        home = item.get("home_team", {}).get("name", "主队")
        away = item.get("away_team", {}).get("name", "客队")
        comp = item.get("competition", {}).get("name", "综合体育")

        sig_data = sp._get(f"{sp.api}/matches/{match_id}/signals")
        signals = []
        if isinstance(sig_data, dict):
            signals = sig_data.get("signals", [])
        elif isinstance(sig_data, list):
            signals = sig_data

        for idx, sig in enumerate(signals):
            source_id = sig.get("source_id", "")
            if not source_id:
                continue
            display = sig.get("display_name", f"信号{idx + 1}")

            sess = sp._post(f"{sp.api}/play/session", {"source_id": source_id})
            enc = (sess or {}).get("real_url_encrypted", "")
            real_url = sp._decode_play_url(enc)
            if not real_url:
                continue

            title = f"{comp}: {home} vs {away}"
            lines.append(
                f'#EXTINF:-1 tvg-id="qiuba_{match_id}_{idx}" '
                f'tvg-name="{home} vs {away}" group-title="{comp}",'
                f"{title} - {display}"
            )
            lines.append(real_url)
            count += 1

    return "\n".join(lines) + "\n", count


def main():
    sp = Spider()
    sp.init("")

    out_dir = os.path.join(BASE_DIR, "data")
    os.makedirs(out_dir, exist_ok=True)

    # 1. 比赛数据 JSON
    result = fetch_matches_json(sp)
    json_path = os.path.join(out_dir, "matches.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    live_count = len(result["home"].get("list", []))
    print(f"OK: {json_path} | 直播中 {live_count} 场 | 分类 {len(result['categories'])} 个")

    # 2. 可播放 m3u 列表
    try:
        m3u_text, m3u_count = build_m3u(sp)
        m3u_path = os.path.join(out_dir, "qiuba.m3u")
        with open(m3u_path, "w", encoding="utf-8") as f:
            f.write(m3u_text)
        print(f"OK: {m3u_path} | 共收录 {m3u_count} 个直播源")
    except Exception as e:  # noqa: BLE001
        print(f"WARN: m3u 生成失败: {e}")


if __name__ == "__main__":
    main()
