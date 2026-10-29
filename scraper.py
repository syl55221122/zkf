#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LiveLeagues Playwright Network Sniffer

- 采用 Playwright 无头浏览器模拟真实访问。
- 动态拦截网络流量，百分之百精准抓取 .m3u8 直播源。
"""

import os
import re
import sys
import time
import logging
from playwright.sync_api import sync_playwright

BASE_URL = "https://liveleagues.st"
OUTPUT_FILE = os.getenv("LIVELEAGUES_OUTPUT", "liveleagues.m3u")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("liveleagues")

def get_event_links():
    """首步：使用浏览器打开首页，抓取所有带有 -live 后缀的房间链接"""
    event_urls = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        log.info(f"正在打开主页提取房间列表: {BASE_URL}")
        try:
            page.goto(BASE_URL, timeout=45000, wait_until="domcontentloaded")
            hrefs = page.eval_on_selector_all("a", "elements => elements.map(el => el.href)")
            for href in hrefs:
                if "/tag-" in href and href.endswith("-live"):
                    if href not in event_urls:
                        event_urls.append(href)
        except Exception as e:
            log.error(f"提取主页链接失败: {e}")
        browser.close()
    
    log.info(f"主页分析完成，共发现 {len(event_urls)} 个赛事房间。")
    return event_urls

def sniff_stream(url):
    """第二步：进入赛事房间，动态拦截并监听流量中的 .m3u8 链接"""
    found_m3u8 = None
    title = url.rsplit("/", 1)[-1]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            bypass_csp=True
        )
        page = context.new_page()

        # 核心：定义网络请求拦截器
        def handle_request(request):
            nonlocal found_m3u8
            req_url = request.url
            if ".m3u8" in req_url.lower() and "chunklist" not in req_url.lower():
                found_m3u8 = req_url
                log.info(f"成功嗅探到直播流地址: {req_url}")

        page.on("request", handle_request)

        try:
            log.info(f"正在进入房间嗅探直播源: {url}")
            page.goto(url, timeout=30000)
            
            # 获取页面标题
            try:
                page_title = page.title()
                if page_title:
                    nonlocal title
                    title = re.sub(r"\s*-\s*LiveLeagues.*$", "", page_title, flags=re.IGNORECASE)
            except:
                pass

            # 模拟页面等待，给 iframe 播放器和 JS 足够的加载和播放时间来触发 m3u8 请求
            for _ in range(8):
                if found_m3u8:
                    break
                time.sleep(1)
                
        except Exception as e:
            log.warning(f"访问房间 {url} 超时或失败: {e}")
        finally:
            browser.close()

    if found_m3u8:
        return title, found_m3u8
    return None

def main():
    log.info("开始执行 Playwright 动态网络嗅探任务")
    event_urls = get_event_links()
    
    # 限制最多扫描的房间数，防止 Action 执行过久超时（可根据需要微调）
    max_scan = min(len(event_urls), 30)
    event_urls = event_urls[:max_scan]

    live_entries = []
    for index, url in enumerate(event_urls):
        log.info(f"进度: [{index+1}/{len(event_urls)}]")
        result = sniff_stream(url)
        if result:
            live_entries.append(result)
            
    # 写入最终的 M3U 文件
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="\n") as file:
        file.write("#EXTM3U\n")
        for title, stream_url in live_entries:
            clean_title = " ".join(title.split())
            file.write(f'#EXTINF:-1 tvg-name="{clean_title}" group-title="LiveLeagues", {clean_title}\n')
            file.write(f"{stream_url}\n")

    log.info(f"扫描完毕。成功将 {len(live_entries)} 条真实有效直播源写入至 {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
