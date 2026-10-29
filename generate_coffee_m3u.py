
# -*- coding: utf-8 -*-
"""
咖啡直播 iPhone M3U 生成工具（GitHub 自动化安全版）

保留原接口：
  1. https://kafeizhibo.cc/api/v1/archor
  2. https://kafeizhibo.cc/api/v1/room/{room_id}

功能：
  - 防空值、防异常
  - 网络失败有限重试
  - 保留最后一次有效的 M3U
  - 原子写入，防止文件写到一半损坏
  - M3U 属性转义
  - 去重、日志记录
  - 支持单次运行和每 10 分钟循环
  - 可供 GitHub Actions 调用

依赖：Python 3.9+，仅使用标准库
"""

import argparse
import json
import logging
import os
import re
import socket
import sys
import tempfile
import time

from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, urlopen


HOST = "https://kafeizhibo.cc"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)

TIMEOUT = 10
RETRIES = 3
RETRY_DELAY = 2
REFRESH_INTERVAL = 600

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Referer": HOST + "/live/all",
    "Origin": HOST,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("coffee_live")


def http_json(url, referer=None, timeout=TIMEOUT):
    """请求 JSON 接口；失败时有限重试，不让单个请求崩溃。"""
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer

    for attempt in range(1, RETRIES + 1):
        try:
            req = Request(url, headers=headers)

            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read()

            text = raw.decode("utf-8", errors="ignore")
            data = json.loads(text)

            if isinstance(data, dict):
                return data

            logger.warning("接口返回的 JSON 不是对象：%s", url)
            return {}

        except Exception as exc:
            logger.warning(
                "请求失败（%s/%s）：%s；原因：%s",
                attempt,
                RETRIES,
                url,
                exc,
            )

            if attempt < RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    logger.error("接口重试耗尽：%s", url)
    return {}


def normalize_url(path):
    """规范化绝对 URL、协议相对 URL 和站内相对路径。"""
    if path is None:
        return ""

    path = str(path).strip()
    if not path:
        return ""

    if path.startswith("//"):
        path = "https:" + path
    else:
        path = urljoin(HOST + "/", path)

    try:
        parsed = urlsplit(path)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ""
        return path
    except (TypeError, ValueError):
        return ""


def safe_text(value, default=""):
    if value is None:
        return default

    if isinstance(value, (dict, list, tuple, set)):
        return default

    result = str(value).strip()
    return result if result else default


def clean_title(text):
    """清理 M3U 标题中的换行和不必要的特殊字符。"""
    text = safe_text(text, "咖啡直播")
    text = re.sub(r"[\r\n]+", " ", text)
    text = text.replace("$", " ").replace("#", " ")
    return " ".join(text.split()).strip() or "咖啡直播"


def clean_attribute(value):
    """清理 M3U 双引号属性，防止破坏 EXTINF 结构。"""
    value = safe_text(value)
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = value.replace("\\", "/").replace('"', "'")
    return value.strip()


def fetch_live_rooms():
    """获取当前直播房间列表，保留原始接口及 code 判断。"""
    data = http_json(
        HOST + "/api/v1/archor",
        referer=HOST + "/live/all",
    )

    if not data or str(data.get("code")) != "200":
        logger.warning("直播房间接口没有返回有效数据")
        return []

    items = data.get("data") or []
    if not isinstance(items, list):
        logger.warning("直播房间 data 字段不是列表")
        return []

    return items


def fetch_room_detail(room_id):
    """获取单个房间详情，保留原始接口。"""
    room_id = safe_text(room_id)
    if not room_id:
        return None

    encoded_id = quote(room_id, safe="")
    url = HOST + "/api/v1/room/" + encoded_id

    data = http_json(
        url,
        referer=HOST + "/room/" + encoded_id,
    )

    if not data or str(data.get("code")) != "200":
        return None

    result = data.get("data")
    return result if isinstance(result, dict) else {}


def build_title(item, room_info=None, signal_name=None):
    room_info = room_info if isinstance(room_info, dict) else {}
    item = item if isinstance(item, dict) else {}

    home = safe_text(
        room_info.get("home_team") or item.get("home_team")
    )
    away = safe_text(
        room_info.get("away_team") or item.get("away_team")
    )
    league = safe_text(
        room_info.get("league")
        or item.get("league_name")
        or item.get("league"),
        "赛事",
    )
    anchor = safe_text(
        item.get("name") or item.get("nickname") or item.get("title")
    )

    # 不使用 or 选择比分，避免数字 0 被误判为空。
    score_h = safe_text(
        room_info.get("home_score")
        if room_info.get("home_score") is not None
        else item.get("home_score"),
        "",
    )
    score_a = safe_text(
        room_info.get("away_score")
        if room_info.get("away_score") is not None
        else item.get("away_score"),
        "",
    )

    parts = []

    if league:
        parts.append(league)

    if home or away:
        parts.append((home + " vs " + away).strip())

    if score_h != "" and score_a != "":
        parts.append(score_h + "-" + score_a)

    if anchor:
        parts.append(anchor)

    if signal_name:
        parts.append(safe_text(signal_name))

    return clean_title(" | ".join(part for part in parts if part))


def extract_logo(item, detail=None):
    item = item if isinstance(item, dict) else {}
    detail = detail if isinstance(detail, dict) else {}

    teams = detail.get("teams") or {}
    if not isinstance(teams, dict):
        teams = {}

    home = teams.get("home") or {}
    away = teams.get("away") or {}

    if not isinstance(home, dict):
        home = {}
    if not isinstance(away, dict):
        away = {}

    candidates = [
        item.get("screenshot"),
        item.get("cover"),
        item.get("home_team_logo"),
        home.get("logo"),
        away.get("logo"),
    ]

    for candidate in candidates:
        url = normalize_url(candidate)
        if url and "default" not in url.lower():
            return url

    return ""


def extract_signals(item, detail):
    """保留原来的线路优先级，不擅自改变接口字段。"""
    signals = detail.get("signals") or []

    if isinstance(signals, dict):
        signals = [signals]

    if not isinstance(signals, list):
        signals = []

    if signals:
        return signals

    archor = detail.get("archor") or {}
    if not isinstance(archor, dict):
        archor = {}

    if archor.get("stream_url"):
        return [archor]

    if item.get("stream_url"):
        return [item]

    return []


def write_m3u_atomically(output_path, lines):
    """先写临时文件，再替换目标文件，防止中途损坏。"""
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    temp_path = None

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
        logger.info("已安全更新文件：%s", target)
        return True

    except Exception:
        logger.exception("写入 M3U 文件失败：%s", target)

        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

        return False


def generate_m3u(output_path):
    """扫描房间并生成有效线路；无有效线路时保留旧文件。"""
    rooms = fetch_live_rooms()

    if not rooms:
        logger.warning("未取得有效房间列表，保留原有 M3U")
        return 0, 0

    lines = ["#EXTM3U"]
    count = 0
    seen = set()

    for item in rooms:
        if not isinstance(item, dict):
            continue

        room_id = (
            item.get("room_id")
            or item.get("id")
            or item.get("roomId")
        )
        if room_id is None or not safe_text(room_id):
            continue

        try:
            detail = fetch_room_detail(room_id) or {}
            if not isinstance(detail, dict):
                detail = {}

            signals = extract_signals(item, detail)

            room_info = detail.get("room_info") or {}
            if not isinstance(room_info, dict):
                room_info = {}

            logo = extract_logo(item, detail)
            group = clean_attribute(
                room_info.get("league")
                or item.get("league_name")
                or "咖啡直播"
            )

            for sig in signals:
                if not isinstance(sig, dict):
                    continue

                stream = safe_text(
                    sig.get("stream_url")
                    or sig.get("url")
                    or sig.get("play_url")
                    or sig.get("m3u8")
                )
                if not stream:
                    continue

                stream = normalize_url(stream)
                if not stream:
                    logger.debug("跳过无效播放地址：room_id=%s", room_id)
                    continue

                if stream in seen:
                    continue

                seen.add(stream)

                signal_name = safe_text(
                    sig.get("name") or sig.get("title"),
                    "线路",
                )

                title = build_title(item, room_info, signal_name)
                logo_attr = clean_attribute(logo)

                extinf = (
                    '#EXTINF:-1 tvg-logo="'
                    + logo_attr
                    + '" group-title="'
                    + group
                    + '",'
                    + title
                )

                lines.append(extinf)
                lines.append(stream)
                count += 1

        except Exception:
            # 单个房间损坏，不影响其他房间。
            logger.exception("处理房间失败：room_id=%s", room_id)
            continue

    if count > 0:
        if write_m3u_atomically(output_path, lines):
            logger.info(
                "成功生成 %s 条线路；扫描房间 %s 个",
                count,
                len(rooms),
            )
        else:
            logger.error("本次生成失败，旧文件应保持不变")
    else:
        logger.warning("没有有效播放线路，保留原有 M3U")

    return count, len(rooms)


def get_lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def serve_file(port, output_path):
    """仅用于手动运行的局域网播放场景。"""
    if not 1 <= port <= 65535:
        raise ValueError("端口必须在 1 到 65535 之间")

    target = Path(output_path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError("M3U 文件不存在：" + str(target))

    class M3UHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(
                *args,
                directory=str(target.parent),
                **kwargs,
            )

        def log_message(self, fmt, *args):
            logger.info("HTTP " + fmt, *args)

    ip = get_lan_ip()
    server = ThreadingHTTPServer(("0.0.0.0", port), M3UHandler)

    print("\n局域网 HTTP 服务已启动")
    print("iPhone 与电脑连接同一 Wi-Fi 后导入：")
    print("http://{}:{}/{}".format(ip, port, quote(target.name)))
    print("按 Ctrl+C 停止服务。\n")

    try:
        server.serve_forever()
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(
        description="生成咖啡直播 iPhone M3U 播放列表"
    )
    parser.add_argument(
        "--output",
        "-o",
        default="coffee_live.m3u",
        help="输出 M3U 文件路径",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="生成后启动局域网 HTTP 服务",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8099,
        help="HTTP 服务端口",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="每隔指定时间持续刷新（适合常驻进程）",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=REFRESH_INTERVAL,
        help="循环刷新间隔，单位秒，默认 600 秒",
    )
    args = parser.parse_args()

    if args.interval < 1:
        parser.error("--interval 必须大于 0")

    try:
        while True:
            started = time.monotonic()
            logger.info(
                "开始扫描：%s",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

            try:
                count, rooms = generate_m3u(args.output)
                logger.info(
                    "扫描结束：房间 %s 个，有效线路 %s 条",
                    rooms,
                    count,
                )
            except Exception:
                logger.exception("本轮扫描异常，保留已有文件并继续运行")

            if not args.loop:
                break

            elapsed = time.monotonic() - started
            delay = max(1, args.interval - int(elapsed))
            logger.info("下一轮约 %s 秒后开始", delay)
            time.sleep(delay)

        if args.serve:
            serve_file(args.port, args.output)

    except KeyboardInterrupt:
        logger.info("收到停止信号，程序退出")
    except Exception:
        logger.exception("程序启动或运行失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
