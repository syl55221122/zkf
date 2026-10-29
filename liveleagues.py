#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LiveLeagues public-page scanner.

- 扫描公开关联的赛事页面。
- 自动识别多源 Stream Links 按钮与嵌入的播放器（如 romchat.me）。
- 深入播放器内部源码解析真正的 .m3u8 直播源。
- 自动将验证过的直播源输出为标准 IPTV 格式的 liveleagues.m3u 文件。
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from collections import deque
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit, quote
from urllib.request import Request, urlopen

BASE_URL = "https://liveleagues.st"
OUTPUT_FILE = os.getenv("LIVELEAGUES_OUTPUT", "liveleagues.m3u")
MAX_PAGES = max(1, int(os.getenv("LIVELEAGUES_MAX_PAGES", "100")))
TIMEOUT = 20
DELAY_SECONDS = 0.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("liveleagues")


def normalize_url(url: str) -> str:
    """标准化 URL，同时避免对已有的百分号编码进行二次编码。"""
    parts = urlsplit(url.strip())

    if parts.scheme not in ("http", "https"):
        return ""

    if not parts.hostname:
        return ""

    # 为了支持深入探测第三方播放器（如 romchat.me），在规范化时允许通过播放器域名的校验
    hostname = parts.hostname.lower()
    allowed_domains = ("liveleagues.st", "www.liveleagues.st", "romchat.me")
    if not any(domain in hostname for domain in allowed_domains):
        return ""

    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="=&%:@!$'()*+,;/?-._~")

    return urlunsplit((parts.scheme.lower(), parts.netloc, path, query, ""))


def fetch_url(url: str) -> tuple[bytes, str, str] | None:
    """使用纯 ASCII 请求头请求公开页面，支持防盗链 Referer 设置。"""
    safe_url = normalize_url(url)
    if not safe_url:
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": BASE_URL,  # 设置来源页，绕过部分防盗链检查
    }

    for key, value in headers.items():
        try:
            key.encode("ascii")
            value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(f"检测到非 ASCII HTTP 请求头: {key}={value!r}") from exc

    request = Request(safe_url, headers=headers, method="GET")

    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            content_type = response.headers.get("Content-Type", "")
            final_url = response.geturl()
            return response.read(), content_type, final_url
    except HTTPError as exc:
        log.warning("HTTP 错误 %s: %s", exc.code, safe_url)
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        log.warning("请求失败 %s: %s", safe_url, exc)

    return None


class PageParser(HTMLParser):
    """解析公开链接及页面标题。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() == "a":
            href = attrs_dict.get("href", "")
            if href:
                self.links.append((href, ""))

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if self.in_title and text:
            self.title_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    @property
    def title(self) -> str:
        raw_title = " ".join(self.title_parts).strip()
        # 清洗掉网站自带的冗余后缀，提取干净的赛事/房间名
        return re.sub(r"\s*-\s*LiveLeagues.*$", "", raw_title, flags=re.IGNORECASE)


def decode_html(body: bytes, content_type: str) -> str:
    """自适应安全解码 HTML。"""
    match = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", content_type, flags=re.IGNORECASE)
    if match:
        encoding = match.group(1)
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            pass
    return body.decode("utf-8", errors="replace")


def is_event_page(url: str) -> bool:
    """识别该 URL 是否为具体的直播房间页。"""
    path = urlsplit(url).path.lower()
    return "/tag-" in path and path.endswith("-live")


def extract_m3u8(html: str, page_url: str) -> str | None:
    """
    核心升级函数：从赛事房间页面或嵌套的独立播放器页面中提取真实 .m3u8 链接
    """
    potential_urls = set()

    # 1. 检查当前页面是否直接包含 m3u8
    direct_m3u8 = re.findall(r'["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', html, re.IGNORECASE)
    if direct_m3u8:
        return direct_m3u8[0]

    # 2. 匹配 Stream Links 按钮中可能隐藏的跳转属性 (例如 data-link, data-url 或是 href)
    custom_attrs = re.findall(r'data-(?:link|url|src|video|stream)\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
    for attr in custom_attrs:
        if attr.startswith(("http://", "https://", "//")):
            potential_urls.add(urljoin(page_url, attr))

    # 3. 匹配页面里嵌套的 <iframe> 播放器地址 (如截图中的 romchat.me)
    iframes = re.findall(r'<iframe\s+[^>]*src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    for src in iframes:
        if src.startswith(("http://", "https://", "//")):
            potential_urls.add(urljoin(page_url, src))

    # 4. 深度追踪：循环请求上面提取到的每一个可能隐藏着视频流的独立播放页
    for p_url in potential_urls:
        # 过滤掉非播放功能的第三方统计或分享链接
        if any(x in p_url for x in ["facebook", "twitter", "google", "analytics"]):
            if "romchat.me" not in p_url:  # 特别保留截图里的关键域名
                continue

        log.info("正在探测深层播放器页面: %s", p_url)
        player_result = fetch_url(p_url)
        if not player_result:
            continue

        player_body, _, _ = player_result
        player_html = player_body.decode("utf-8", errors="replace")

        # 4.1 在播放页面的源码/JavaScript 中提取 .m3u8 地址
        m3u8_matches = re.findall(r'["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', player_html, re.IGNORECASE)
        if m3u8_matches:
            log.info("成功从深层页面抓取到 m3u8: %s", m3u8_matches[0])
            return m3u8_matches[0]

        # 4.2 兼容无协议头的特殊情况 (如 //://stream.com)
        proto_matches = re.findall(r'["\'](//[^"\']+\.m3u8[^"\']*)["\']', player_html, re.IGNORECASE)
        if proto_matches:
            full_stream_url = "https:" + proto_matches[0]
            log.info("成功还原无协议头 m3u8: %s", full_stream_url)
            return full_stream_url

    return None


def crawl_live_streams() -> list[tuple[str, str]]:
    """广度优先遍历发现公开页面，并解析里面的真实直播链。"""
    queue = deque([BASE_URL])
    visited: set[str] = set()
    live_entries: list[tuple[str, str]] = []

    while queue and len(visited) < MAX_PAGES:
        current = queue.popleft()
        current = normalize_url(current)

        # 确保只扫描主站的索引与跳转逻辑
        if not current or current in visited or "romchat.me" in current:
            continue

        visited.add(current)
        log.info("正在扫描索引页 [%s/%s]: %s", len(visited), MAX_PAGES, current)

        result = fetch_url(current)
        if result is None:
            continue

        body, content_type, final_url = result
        if content_type and "html" not in content_type.lower():
            continue

        html = decode_html(body, content_type)
        parser = PageParser()

        try:
            parser.feed(html)
        except Exception as exc:
            log.warning("HTML 解析失败 %s: %s", current, exc)
            continue

        # 如果进入单场赛事房间，执行深层 m3u8 直播源提取
        if is_event_page(final_url):
            stream_url = extract_m3u8(html, final_url)
            if stream_url:
                title = parser.title or final_url.rsplit("/", 1)[-1]
                log.info("★ 成功锁定有效直播流: %s -> %s", title, stream_url)
                live_entries.append((title, stream_url))
            else:
                log.debug("房间未找到匹配流: %s", final_url)

        # 发现下一层待爬取的链接
        for href, _ in parser.links:
            absolute = normalize_url(urljoin(final_url, href))
            if not absolute or absolute in visited:
                continue

            path = urlsplit(absolute).path.lower()
            # 过滤干扰路由和多媒体静态文件
            if any(part in path for part in ("/login", "/register", "/search", "/wp-admin")):
                continue
            if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".css", ".js", ".svg")):
                continue

            queue.append(absolute)

        time.sleep(DELAY_SECONDS)

    return live_entries


def write_m3u_playlist(live_entries: list[tuple[str, str]]) -> None:
    """输出标准 IPTV 适用的 M3U 播放列表。"""
    if not live_entries:
        log.warning("未找到任何有效直播流。M3U 文件将为空。")

    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="\n") as file:
        file.write("#EXTM3U\n")
        for title, stream_url in live_entries:
            clean_title = " ".join(title.split())
            # 写入带有频道名和分组的 #EXTINF 头
            file.write(f'#EXTINF:-1 tvg-name="{clean_title}" group-title="LiveLeagues", {clean_title}\n')
            file.write(f"{stream_url}\n")

    log.info("成功将 %s 条直播源写入至 %s 文件中。", len(live_entries), OUTPUT_FILE)


def main() -> int:
    log.info("开始执行 LiveLeagues 体育赛事流媒体提取")
    try:
        live_entries = crawl_live_streams()
        write_m3u_playlist(live_entries)
    except KeyboardInterrupt:
        log.warning("用户手动中止进程")
        return 130
    except Exception:
        log.exception("自动化运行异常失败")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
