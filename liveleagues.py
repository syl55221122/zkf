
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LiveLeagues 公开赛事扫描器
- 递归发现公开赛事详情页
- 识别 Live 状态和 Stream Links
- 验证明确公开提供的 HLS 地址
- 生成 liveleagues.m3u
- 生成 liveleagues.json 诊断报告
- 没有有效线路时保留旧 M3U 文件
"""

import json
import logging
import re
import sys
import time
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

BASE_URL = "https://liveleagues.st/"
OUTPUT_M3U = Path("liveleagues.m3u")
OUTPUT_JSON = Path("liveleagues.json")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 Chrome/134.0.0.0 Safari/537.36"
)

TIMEOUT = 15
MAX_PAGES = 300
PAGE_DELAY = 0.3

# 只检查本站公开页面，避免无限爬取外站。
ALLOWED_HOSTS = {"liveleagues.st", "www.liveleagues.st"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class PageParser(HTMLParser):
    """提取 HTML 中的公开链接、标题及页面文本。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.text_parts = []
        self.in_title = False
        self.title_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "a":
            href = attrs.get("href")
            if href:
                self.links.append(href)

        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.text_parts.append(text)

        if self.in_title and text:
            self.title_parts.append(text)

    @property
    def title(self):
        return " ".join(self.title_parts).strip()

    @property
    def text(self):
        return " ".join(self.text_parts)


def fetch_page(url):
    """获取公开 HTML 页面；失败时返回 None。"""
    try:
        req = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
        )

        with urlopen(req, timeout=TIMEOUT) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return None

            charset = response.headers.get_content_charset() or "utf-8"
            raw = response.read(3 * 1024 * 1024)
            return raw.decode(charset, errors="replace")

    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        logging.warning("页面获取失败：%s (%s)", url, exc)
        return None


def is_allowed_page(url):
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in ALLOWED_HOSTS
        and not parsed.query
        and not parsed.fragment
    )


def normalize_page_url(base, href):
    url = urljoin(base, href)
    parsed = urlparse(url)

    if not is_allowed_page(url):
        return None

    # 统一去除末尾斜杠，便于去重。
    path = parsed.path.rstrip("/") or "/"
    return f"https://{parsed.hostname}{path}"


def parse_page(url, html):
    parser = PageParser()
    parser.feed(html)

    text = parser.text

    links = []
    for href in parser.links:
        normalized = normalize_page_url(url, href)
        if normalized:
            links.append(normalized)

    is_live = bool(re.search(r"\blive\b", text, re.IGNORECASE))

    # 仅统计页面中可见的 Stream Links / Broadcast 文本。
    stream_section = bool(
        re.search(r"Stream\s+Links", text, re.IGNORECASE)
    )

    broadcast_count = len(
        re.findall(r"\bBroadcast\b", text, re.IGNORECASE)
    )

    # 记录页面中明确出现的绝对 HTTPS M3U8 URL。
    # 不推测、不拼接隐藏接口，也不访问页面之外的播放器会话。
    hls_urls = sorted(set(
        re.findall(
            r"""https://[^\s"'<>]+?\.m3u8(?:\?[^\s"'<>]*)?""",
            html,
            re.IGNORECASE,
        )
    ))

    return {
        "url": url,
        "title": parser.title or url.rstrip("/").split("/")[-1],
        "live": is_live,
        "has_stream_links": stream_section,
        "broadcast_count": broadcast_count,
        "links": sorted(set(links)),
        "public_hls": hls_urls,
    }


def discover_pages():
    """从赛事入口逐层发现本站公开 HTML 页面。"""
    queue = deque([BASE_URL.rstrip("/")])
    visited = set()
    results = []

    while queue and len(visited) < MAX_PAGES:
        url = queue.popleft()

        if url in visited:
            continue

        visited.add(url)
        logging.info("检查公开页面 %d/%d：%s",
                     len(visited), MAX_PAGES, url)

        html = fetch_page(url)
        if html is None:
            continue

        record = parse_page(url, html)
        results.append(record)

        for link in record["links"]:
            if link not in visited:
                queue.append(link)

        time.sleep(PAGE_DELAY)

    logging.info("公开页面扫描完成：%d 个页面", len(results))
    return results


def validate_public_hls(url):
    """检查公开 HLS 地址是否可访问且返回 M3U 播放列表。"""
    parsed = urlparse(url)

    if parsed.scheme != "https":
        return False

    # 不跟随到其他主机，避免把页面提取到的任意 URL 当作可信源。
    if parsed.hostname not in ALLOWED_HOSTS:
        return False

    try:
        req = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.apple.mpegurl, application/x-mpegURL, */*",
            },
        )

        with urlopen(req, timeout=TIMEOUT) as response:
            data = response.read(64 * 1024)
            text = data.decode("utf-8", errors="replace")

        return "#EXTM3U" in text[:4096]

    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def write_report(pages):
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pages_checked": len(pages),
        "live_pages": sum(1 for p in pages if p["live"]),
        "pages_with_stream_links": sum(
            1 for p in pages if p["has_stream_links"]
        ),
        "pages": [
            {
                "url": p["url"],
                "title": p["title"],
                "live": p["live"],
                "has_stream_links": p["has_stream_links"],
                "broadcast_count": p["broadcast_count"],
                "public_hls_count": len(p["public_hls"]),
            }
            for p in pages
        ],
    }

    OUTPUT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def generate_playlist(pages):
    lines = ["#EXTM3U"]
    seen = set()
    count = 0

    for page in pages:
        for url in page["public_hls"]:
            if url in seen:
                continue

            if not validate_public_hls(url):
                continue

            seen.add(url)
            title = re.sub(r"[\r\n,]", " ", page["title"]).strip()
            lines.append(f"#EXTINF:-1 group-title=\"LiveLeagues\",{title}")
            lines.append(url)
            count += 1

    if count:
        OUTPUT_M3U.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )
        logging.info("已更新 %s：%d 条有效 HLS", OUTPUT_M3U, count)
    else:
        logging.warning(
            "未发现符合条件的公开 HLS 地址，保留现有 %s",
            OUTPUT_M3U,
        )

    return count


def main():
    started = time.monotonic()

    pages = discover_pages()
    write_report(pages)
    count = generate_playlist(pages)

    logging.info(
        "扫描结束：页面 %d，Live 页面 %d，带 Stream Links 页面 %d，"
        "有效 HLS %d，耗时 %.1f 秒",
        len(pages),
        sum(1 for p in pages if p["live"]),
        sum(1 for p in pages if p["has_stream_links"]),
        count,
        time.monotonic() - started,
    )

    # 没有有效 HLS 并不等于扫描失败。
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logging.warning("用户中断扫描")
        sys.exit(130)
    except Exception:
        logging.exception("程序遇到未处理异常")
        sys.exit(1)
