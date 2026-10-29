
# -*- coding: utf-8 -*-
"""
LiveLeagues 公开赛事发现与 HLS 候选检查器

功能：
1. 遍历有限数量的同站公开赛事页面。
2. 检查公开 HTML 与外部 JavaScript 文件。
3. 识别源码中直接出现的 HTTP(S) M3U8 地址。
4. 验证可公开读取的候选播放列表。
5. 只有发现有效候选时才原子更新输出文件。
6. 没有候选、网络失败或解析失败时保留旧文件。

不执行网站 JavaScript，不复用浏览器 Cookie、会话或令牌，
不访问未明确允许的第三方资源。

Python 3.10+
"""

import html
import logging
import os
import re
import sys
import tempfile
import time

from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urldefrag
from urllib.request import Request, urlopen


BASE_URL = "https://liveleagues.st/"
OUTPUT_FILE = "liveleagues.m3u"

TIMEOUT = 12
RETRIES = 2
MAX_PAGES = 80
MAX_SCRIPTS = 100
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_JS_BYTES = 2 * 1024 * 1024
MAX_PLAYLIST_BYTES = 512 * 1024

# 只允许明确配置的公开资源域名。
# 如服务商书面授权了其他静态资源域名，可在核实后添加。
ALLOWED_HOSTS = {
    "liveleagues.st",
    "www.liveleagues.st",
    "static.liveleagues.st",
}

USER_AGENT = (
    "Mozilla/5.0 (compatible; LiveLeaguesPublicIndexer/1.0)"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("liveleagues")


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.scripts = []
        self.title_parts = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()

        if tag == "title":
            self.in_title = True

        if tag == "a":
            href = attrs.get("href")
            if href:
                self.links.append(href)

        if tag == "script":
            src = attrs.get("src")
            if src:
                self.scripts.append(src)

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self):
        value = " ".join(" ".join(self.title_parts).split())
        return value or "LiveLeagues"


def allowed_url(url):
    """仅允许 HTTP(S) 和明确列出的主机名。"""
    try:
        parts = urlsplit(url)

        if parts.scheme not in ("http", "https"):
            return False

        if not parts.hostname:
            return False

        if parts.username or parts.password:
            return False

        return parts.hostname.lower() in ALLOWED_HOSTS
    except ValueError:
        return False


def canonical_url(url):
    url = urldefrag(url.strip())[0]

    if not allowed_url(url):
        return ""

    parts = urlsplit(url)
    return parts._replace(fragment="").geturl()


def fetch_bytes(url, accept, max_bytes):
    """读取允许访问的公开资源，限制重试次数和响应体大小。"""
    if not allowed_url(url):
        log.debug("拒绝非允许域名：%s", url)
        return None, None, None

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
    }

    for attempt in range(1, RETRIES + 1):
        try:
            req = Request(url, headers=headers)

            with urlopen(req, timeout=TIMEOUT) as response:
                final_url = response.geturl()

                if not allowed_url(final_url):
                    log.warning("跳过外部重定向：%s", url)
                    return None, None, None

                status = getattr(response, "status", 200)
                content_type = response.headers.get(
                    "Content-Type", ""
                ).lower()

                raw = response.read(max_bytes + 1)

            if status != 200 or len(raw) > max_bytes:
                log.warning("状态异常或响应体过大：%s", url)
                return None, None, None

            return raw, content_type, final_url

        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            log.warning(
                "读取失败 %s/%s：%s（%s）",
                attempt, RETRIES, url, exc
            )
            if attempt < RETRIES:
                time.sleep(attempt)

        except Exception:
            log.exception("读取资源时出现异常：%s", url)
            return None, None, None

    return None, None, None


def fetch_text(url, accept, max_bytes):
    raw, content_type, final_url = fetch_bytes(
        url, accept, max_bytes
    )

    if raw is None:
        return None, None, None

    text = raw.decode("utf-8", errors="replace")
    return text, content_type, final_url


# 仅识别源码中直接写出的完整 HTTP(S) M3U8 URL。
ABSOLUTE_M3U8 = re.compile(
    r"""https?://[^\s"'<>\\]+?\.m3u8(?:\?[^\s"'<>\\]*)?""",
    re.IGNORECASE,
)

# 识别页面中直接出现的相对 M3U8 路径。
RELATIVE_M3U8 = re.compile(
    r"""(?<![A-Za-z0-9])(?:/|\./|\.\./)[^\s"'<>\\]+?\.m3u8(?:\?[^\s"'<>\\]*)?""",
    re.IGNORECASE,
)


def extract_candidates(base_url, source):
    found = set()

    source = html.unescape(source)

    candidates = (
        ABSOLUTE_M3U8.findall(source)
        + RELATIVE_M3U8.findall(source)
    )

    for candidate in candidates:
        candidate = candidate.strip().rstrip(".,;)]}")

        if len(candidate) > 4096:
            continue

        full_url = urljoin(base_url, candidate)

        # 播放源可能托管在第三方 CDN。
        # 默认不抓取未列入允许名单的主机。
        if not allowed_url(full_url):
            continue

        if not urlsplit(full_url).path.lower().endswith(".m3u8"):
            continue

        found.add(full_url)

    return found


def is_hls_playlist(url):
    """
    只验证允许域名中可直接读取的 M3U8 文本。
    不跟随播放器流程，不请求密钥或媒体分片。
    """
    text, content_type, final_url = fetch_text(
        url,
        accept=(
            "application/vnd.apple.mpegurl,"
            "application/x-mpegURL,text/plain,*/*"
        ),
        max_bytes=MAX_PLAYLIST_BYTES,
    )

    if not text or not final_url:
        return False

    if not text.lstrip("\ufeff \t\r\n").startswith("#EXTM3U"):
        log.info("候选地址未返回 M3U8 内容：%s", url)
        return False

    # 仅确认格式，不代表已验证播放权限或完整播放能力。
    log.info("发现格式有效的 HLS 播放列表：%s", redact_url(url))
    return True


def redact_url(url):
    """日志不输出查询参数，避免泄漏会话信息。"""
    parts = urlsplit(url)
    return parts._replace(query="", fragment="").geturl()


def discover_pages():
    start = canonical_url(BASE_URL)

    if not start:
        raise RuntimeError("起始页面不在允许域名列表中")

    queue = deque([start])
    visited = set()
    script_urls = set()
    candidates = {}
    pages = 0

    while queue and pages < MAX_PAGES:
        page_url = queue.popleft()

        if page_url in visited:
            continue

        visited.add(page_url)

        log.info("检查公开页面 %s/%s：%s",
                 pages + 1, MAX_PAGES, redact_url(page_url))

        source, _, final_url = fetch_text(
            page_url,
            accept="text/html,application/xhtml+xml",
            max_bytes=MAX_HTML_BYTES,
        )

        if not source or not final_url:
            continue

        pages += 1

        parser = PageParser()

        try:
            parser.feed(source)
            parser.close()
        except Exception:
            log.warning("HTML 解析不完整：%s", redact_url(page_url))

        for stream in extract_candidates(final_url, source):
            candidates.setdefault(stream, parser.title)

        for src in parser.scripts:
            script_url = canonical_url(urljoin(final_url, src))
            if script_url:
                script_urls.add(script_url)

        for href in parser.links:
            link = canonical_url(urljoin(final_url, href))
            if not link:
                continue

            if link not in visited and link not in queue:
                if len(queue) < MAX_PAGES * 4:
                    queue.append(link)

    log.info(
        "公开页面检查完成：页面 %s 个，脚本 %s 个，"
        "直接发现的 M3U8 候选 %s 个",
        pages, len(script_urls), len(candidates)
    )

    # 检查有限数量的公开 JavaScript 静态文本。
    scripts_checked = 0

    for script_url in sorted(script_urls)[:MAX_SCRIPTS]:
        source, _, final_url = fetch_text(
            script_url,
            accept="application/javascript,text/javascript,*/*",
            max_bytes=MAX_JS_BYTES,
        )

        if not source or not final_url:
            continue

        scripts_checked += 1

        for stream in extract_candidates(final_url, source):
            candidates.setdefault(stream, "LiveLeagues")

    log.info(
        "静态脚本检查完成：读取 %s 个脚本，"
        "共发现 %s 个候选",
        scripts_checked, len(candidates)
    )

    return candidates, pages


def write_m3u_atomically(output_path, entries):
    target = Path(output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    temp_path = None
    lines = ["#EXTM3U"]

    for url, title in sorted(entries.items(), key=lambda x: x[1].lower()):
        title = " ".join(
            title.replace("\r", " ").replace("\n", " ").split()
        )
        title = title.replace(",", " ").replace("#", " ").replace("$", " ")

        lines.append(
            f'#EXTINF:-1 group-title="LiveLeagues",{title}'
        )
        lines.append(url)

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=str(target.parent),
            prefix="." + target.name + ".",
            suffix=".tmp",
            delete=False,
        ) as file:
            temp_path = Path(file.name)
            file.write("\n".join(lines) + "\n")
            file.flush()
            os.fsync(file.fileno())

        os.replace(str(temp_path), str(target))
        log.info("播放列表已原子更新：%s", target)
        return True

    except Exception:
        log.exception("写入播放列表失败")

        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

        return False


def main():
    output_path = (
        sys.argv[1] if len(sys.argv) > 1 else OUTPUT_FILE
    )

    try:
        candidates, page_count = discover_pages()
    except Exception:
        log.exception("赛事发现失败，保留现有文件")
        return 1

    # 仅验证允许主机上可直接读取的候选播放列表。
    valid = {}

    for url, title in sorted(candidates.items()):
        if is_hls_playlist(url):
            valid[url] = title

    if not valid:
        log.warning(
            "没有发现可直接读取的有效 M3U8；"
            "保留旧文件。静态检查无法发现运行时动态生成的地址。"
        )
        log.info("本次检查公开页面数：%s", page_count)
        return 0

    if not write_m3u_atomically(output_path, valid):
        return 1

    log.info(
        "任务完成：页面 %s 个，有效 M3U8 候选 %s 个",
        page_count, len(valid)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
