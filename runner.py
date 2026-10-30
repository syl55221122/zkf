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

代理说明（全自动，无需人工干预）：
  - 代理池由 proxy_harvester.py 每 2 小时实测维护，写入 data/proxies.txt。
  - runner 启动时：先试上次可用的代理（data/.last_proxy 缓存，命中则秒过）；
    否则并行测试池中代理，选用响应最快的可用的；若全部失效则直连尝试。
  - 选定代理后会自动设置 HTTP_PROXY / HTTPS_PROXY 环境变量，
    requests（qiuba.py 用的库）会默认读取，无需改业务代码。
"""
import json
import os
import sys
import types
from concurrent.futures import ThreadPoolExecutor, as_completed
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
LAST_PROXY_FILE = os.path.join(BASE_DIR, "data", ".last_proxy")
POOL_FILE = os.path.join(BASE_DIR, "data", "proxies.txt")


def _mask_proxy(p):
    """脱敏打印代理地址（隐藏账号密码部分）。"""
    return p.split("@")[-1] if "@" in p else p


def _proxy_works(proxy, timeout=8):
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


def _parallel_pick(proxies, timeout=8):
    """并行测试代理列表，返回第一个（响应最快）可用的代理，无则返回 None。"""
    proxies = list(dict.fromkeys(p for p in proxies if p))  # 去重去空，保持顺序
    if not proxies:
        return None
    with ThreadPoolExecutor(max_workers=min(20, len(proxies))) as ex:
        future_to_proxy = {
            ex.submit(_proxy_works, p, timeout): p for p in proxies
        }
        for fut in as_completed(future_to_proxy):
            try:
                if fut.result():
                    winner = future_to_proxy[fut]
                    for f in future_to_proxy:
                        f.cancel()  # 尽力取消未开始的任务
                    return winner
            except Exception:  # noqa: BLE001
                continue
    return None


def _pool_candidates():
    """读取收割器维护的 data/proxies.txt 候选代理。"""
    cands = []
    try:
        with open(POOL_FILE, encoding="utf-8") as f:
            for line in f:
                p = line.strip()
                if p and p not in cands:
                    cands.append(p)
    except Exception:  # noqa: BLE001
        pass
    return cands


def _load_last_proxy():
    """读取上次可用的代理缓存。"""
    try:
        with open(LAST_PROXY_FILE, encoding="utf-8") as f:
            p = f.read().strip()
            return p or None
    except Exception:  # noqa: BLE001
        return None


def _save_last_proxy(proxy):
    """缓存本次可用的代理，供下次优先试用（随 data/ 一起提交）。"""
    try:
        os.makedirs(os.path.dirname(LAST_PROXY_FILE), exist_ok=True)
        with open(LAST_PROXY_FILE, "w", encoding="utf-8") as f:
            f.write(proxy)
    except Exception as e:  # noqa: BLE001
        print(f"WARN: 缓存代理失败: {e}")


def _apply_proxy(proxy):
    os.environ["HTTP_PROXY"] = proxy
    os.environ["HTTPS_PROXY"] = proxy


def setup_proxy():
    """自动挑选可用代理并设置到环境变量。返回是否找到可用代理。"""
    # 1. 先试上次可用的代理，命中则秒过
    last = _load_last_proxy()
    if last:
        if _proxy_works(last, timeout=6):
            print(f"使用上次可用代理: {_mask_proxy(last)}")
            _apply_proxy(last)
            return True
        print(f"上次代理已失效: {_mask_proxy(last)}")

    # 2. 并行测试代理池文件
    cands = [p for p in _pool_candidates() if p != last]
    if cands:
        print(f"并行测试 {len(cands)} 个候选代理...")
        hit = _parallel_pick(cands, timeout=8)
        if hit:
            print(f"使用代理: {_mask_proxy(hit)}")
            _apply_proxy(hit)
            _save_last_proxy(hit)
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


def _resolve_match_signals(sp, item):
    """解析单场比赛全部信号的真实地址，返回 [(排序键, extinf, url)]。

    qiuba.py 的 _get / _post / _decode_play_url 无共享可变状态，
    可在多线程中并发调用。
    """
    results = []
    match_id = str(item.get("id", ""))
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
        extinf = (
            f'#EXTINF:-1 tvg-id="qiuba_{match_id}_{idx}" '
            f'tvg-name="{home} vs {away}" group-title="{comp}",'
            f"{title} - {display}"
        )
        results.append(((comp, match_id, idx), extinf, real_url))
    return results


def build_m3u(sp, max_workers=8):
    """并行解析所有未结束比赛的直播信号真实地址，返回 (m3u文本, 收录条数)。

    复用 qiuba.py 自带的 _get / _post / _decode_play_url 逻辑：
    signals 接口拿信号源 -> play/session 接口拿加密地址 -> 解出 m3u8。
    比赛之间并行解析，输出按 (赛事, 比赛, 信号序号) 排序保证每次顺序稳定。
    """
    data = sp._get(f"{sp.api}/matches?page=1&page_size=100")
    items = data.get("items", []) if isinstance(data, dict) else []
    items = [it for it in items
             if it.get("status") != "finished" and it.get("id")]

    all_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_resolve_match_signals, sp, it): it for it in items}
        for fut in as_completed(futs):
            try:
                all_results.extend(fut.result())
            except Exception as e:  # noqa: BLE001
                print(f"WARN: 比赛信号解析失败: {e}")

    all_results.sort(key=lambda x: x[0])
    lines = ["#EXTM3U"]
    for _, extinf, url in all_results:
        lines.append(extinf)
        lines.append(url)
    return "\n".join(lines) + "\n", len(all_results)


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
