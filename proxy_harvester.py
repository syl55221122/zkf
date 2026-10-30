#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
代理收割器：从多个免费代理源拉取候选，经目标站并行实测后，
把可用的写入 data/proxies.txt，供主流程 runner.py 使用。

由 .github/workflows/proxies.yml 定时触发（每 2 小时），与主抓取流程解耦：
  - harvester 负责"找代理"（慢而全，每次实测数百个候选）；
  - 主流程负责"用代理"（快而稳，读池子 + 缓存，秒级选中）。

安全熔断：如果实测可用数过少（比如目标站本身挂了），不覆盖旧池，
避免把池子洗成空的。
"""
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

PROXY_TEST_URL = "https://www.qiuba001.vip/api/categories"
POOL_FILE = os.path.join(BASE_DIR, "data", "proxies.txt")

KEEP_TOP_N = 15       # 池子保留延迟最低的 N 个
MAX_CANDIDATES = 600  # 单次实测上限，控制运行时长
MIN_KEEP_OLD = 3      # 实测可用少于此数且旧池存在时，不覆盖旧池

SOURCES = [
    # CN 专区优先：目标站只认国内出口
    ("proxyscrape-cn",
     "https://api.proxyscrape.com/v2/?request=displayproxies"
     "&protocol=http&timeout=10000&country=CN&ssl=all&anonymity=all"),
    ("proxyscrape-all",
     "https://api.proxyscrape.com/v2/?request=displayproxies"
     "&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all"),
    ("thespeedx",
     "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),
    ("monosans",
     "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),
    ("clarketm",
     "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt"),
]


def _extract_proxies(text):
    """从任意文本中提取 ip:port，统一拼成 http:// 代理地址。"""
    out = []
    for m in re.finditer(r"(\d{1,3}(?:\.\d{1,3}){3}:\d{1,5})", text):
        url = "http://" + m.group(1)
        if url not in out:
            out.append(url)
    return out


def _fetch_source(name, url):
    try:
        r = requests.get(url, timeout=25)
        if r.status_code != 200:
            print(f"[{name}] HTTP {r.status_code}，跳过")
            return []
        proxies = _extract_proxies(r.text)
        print(f"[{name}] 拉取到 {len(proxies)} 个候选")
        return proxies
    except Exception as e:  # noqa: BLE001
        print(f"[{name}] 拉取失败: {e}")
        return []


def _read_pool():
    try:
        with open(POOL_FILE, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception:  # noqa: BLE001
        return []


def _test_proxy(proxy, timeout=6):
    """实测代理：返回延迟秒数（可用），不可用返回 None。"""
    t0 = time.time()
    try:
        r = requests.get(
            PROXY_TEST_URL,
            proxies={"http": proxy, "https": proxy},
            timeout=timeout,
        )
        if r.status_code == 200 and r.text[:1] in ("[", "{"):
            return time.time() - t0
    except Exception:  # noqa: BLE001
        pass
    return None


def main():
    # 1. 并行拉取各代理源
    candidates = []
    with ThreadPoolExecutor(max_workers=max(1, len(SOURCES))) as ex:
        futs = {ex.submit(_fetch_source, n, u): n for n, u in SOURCES}
        for fut in as_completed(futs):
            candidates.extend(fut.result())
    candidates = list(dict.fromkeys(candidates))
    print(f"各源去重后共 {len(candidates)} 个候选")

    # 2. 旧池中的代理也加入复测（历史可用的优先保留机会）
    old_pool = _read_pool()
    for p in old_pool:
        if p not in candidates:
            candidates.append(p)
    candidates = candidates[:MAX_CANDIDATES]
    print(f"本次实测 {len(candidates)} 个候选（含旧池 {len(old_pool)} 个复测）")

    # 3. 并行实测，按延迟排序
    working = []
    with ThreadPoolExecutor(max_workers=50) as ex:
        futs = {ex.submit(_test_proxy, p): p for p in candidates}
        for fut in as_completed(futs):
            latency = fut.result()
            if latency is not None:
                working.append((futs[fut], latency))
    working.sort(key=lambda x: x[1])
    print(f"实测可用 {len(working)} 个")

    # 4. 熔断：可用过少且旧池存在 -> 不覆盖（可能是目标站本身故障）
    if len(working) < MIN_KEEP_OLD and old_pool:
        print(f"WARN: 可用代理过少（{len(working)}），保留旧池 {len(old_pool)} 个，不覆盖")
        return

    top = [p for p, _ in working[:KEEP_TOP_N]]
    os.makedirs(os.path.dirname(POOL_FILE), exist_ok=True)
    with open(POOL_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(top) + ("\n" if top else ""))
    for p, lat in working[:KEEP_TOP_N]:
        print(f"  {p}  {lat:.1f}s")
    print(f"OK: 写入 {POOL_FILE}，共 {len(top)} 个")


if __name__ == "__main__":
    main()
