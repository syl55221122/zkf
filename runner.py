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

代理说明（自动处理，无需人工干预）：
  - workflow 通过 PROXY_URL 环境变量传入代理池，多个代理用英文逗号分隔。
  - runner 启动时会自动测试并选用第一个可用的代理；若池内全部失效，
    则自动从代理池 API 拉取新的国内代理重试；若仍无可用则直连尝试。
  - 选定代理后会自动设置 HTTP_PROXY / HTTPS_PROXY 环境变量，
    requests（qiuba.py 用的库）会默认读取，无需改业务代码。
"""
import json
import os
import re
import sys
import types
from datetime import datetime, timedelta, timezone

import requests

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


PROXY_TEST_URL = "https://www.qiuba001.vip/api/categories"
PROXY_POOL_API = (
    "https://api.proxyscrape.com/v2/?request=displayproxies"
    "&protocol=http&timeout=10000&country=CN&ssl=all&anonymity=all"
)


def _mask_proxy(p):
    """脱敏打印代理地址（隐藏账号密码部分）。"""
    return p.split("@")[-1] if "@" in p else p


def _proxy_works(proxy, timeout=10):
    """测试代理能否正常访问目标站（状态 200 且返回 JSON）。"""
    try:
        r = requests.get(
            PROXY_TEST_URL,
            proxies={"http": proxy, "https": proxy},
            timeout=timeout,
        )
        return r.status_code == 200 and r.text[:1] in ("[", "{")
    except Exception:
        return False


def _saved_candidates():
    """从 PROXY_URL 环境变量解析候选代理（逗号/分号分隔，去重）。"""
    raw = os.environ.get("PROXY_URL", "")
    cands = []
    for p in raw.replace(";", ",").split(","):
        p = p.strip()
        if p and p not in cands:
            cands.append(p)
    return cands


def _fetch_fresh_proxies(limit=30):
    """从免费代理池 API 拉取新的国内代理（直连拉取，不走代理）。"""
    try:
        r = requests.get(PROXY_POOL_API, timeout=20)
        out = []
        for line in r.text.splitlines():
            line = line.strip()
            if re.match(r"^\d{1,3}(\.\d{1,3}){3}:\d+$", line):
                url = "http://" + line
                if url not in out:
                    out.append(url)
            if len(out) >= limit:
                break
        return out
    except Exception as e:  # noqa: BLE001
        print(f"拉取新代理列表失败: {e}")
        return []


def setup_proxy():
    """自动挑选可用代理并设置到环境变量。返回是否找到可用代理。"""
    cands = _saved_candidates()
    if cands:
        print(f"测试 {len(cands)} 个候选代理...")
        for p in cands:
            if _proxy_works(p):
                print(f"使用代理: {_mask_proxy(p)}")
                os.environ["HTTP_PROXY"] = p
                os.environ["HTTPS_PROXY"] = p
                return True
            print(f"代理不可用: {_mask_proxy(p)}")
        print("候选代理全部失效，尝试拉取新的代理列表...")

    fresh = _fetch_fresh_proxies()
    print(f"拉取到 {len(fresh)} 个新代理，逐个测试...")
    for p in fresh:
        if _proxy_works(p):
            print(f"使用新代理: {_mask_proxy(p)}")
            os.environ["HTTP_PROXY"] = p
            os.environ["HTTPS_PROXY"] = p
            return True

    print("WARN: 无可用代理，将直连尝试")
    return False


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
    # 先自动挑选可用代理（会设置 HTTP_PROXY / HTTPS_PROXY 环境变量）
    setup_proxy()

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
