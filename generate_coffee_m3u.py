# -*- coding: utf-8 -*-
"""
咖啡直播 iPhone M3U 生成工具（2026全新修复版）
作用：利用 Next.js 状态解析安全抓取 kafeizhibo.cc 当前直播房间及 M3U8 线路。
运行：python generate_coffee_m3u.py
"""
import argparse
import json
import socket
import sys
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

HOST = "https://kafeizhibo.cc"
USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
TIMEOUT = 8

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": HOST + "/",
}

def get_html_soup(url):
    """获取网页 HTML 源码"""
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="ignore")

def extract_next_data(html_text):
    """解析 Next.js 的 __NEXT_DATA__ 预加载 JSON"""
    try:
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_text, re.S)
        if match:
            return json.loads(match.group(1).strip())
    except Exception as e:
        print("解析 __NEXT_DATA__ 失败:", e)
    return {}

def normalize_url(path):
    if not path:
        return ""
    path = str(path).strip()
    if path.startswith("http"):
        return path
    if path.startswith("//"):
        return "https:" + path
    return HOST + ("" if path.startswith("/") else "/") + path

def safe_text(value, default=""):
    if value is None:
        return default
    return str(value).strip() or default

def clean_title(text):
    text = safe_text(text, "咖啡直播")
    return text.replace("\n", " ").replace("\r", " ").replace("$", " ").replace("#", " ").strip()

def generate_m3u(output_path):
    print("正在从首页抓取直播列表状态...")
    try:
        home_html = get_html_soup(HOST + "/")
    except Exception as e:
        print(f"无法访问网站首页: {e}")
        return 0, 0

    next_data = extract_next_data(home_html)
    props = next_data.get("props", {}).get("pageProps", {})
    
    # 兼容处理：获取首页的比赛列表数据
    rooms_list = props.get("initLiveList") or props.get("liveList") or props.get("data") or []
    
    if not rooms_list:
        # 如果首页未提取到，尝试从 /live/all 页面提取
        try:
            print("尝试从备用直播页抓取...")
            all_html = get_html_soup(HOST + "/live/all")
            next_data = extract_next_data(all_html)
            props = next_data.get("props", {}).get("pageProps", {})
            rooms_list = props.get("initLiveList") or props.get("liveList") or props.get("data") or []
        except Exception:
            pass

    if not isinstance(rooms_list, list):
        rooms_list = []

    lines = ["#EXTM3U"]
    count = 0
    seen_streams = set()
    rooms_count = 0

    for item in rooms_list:
        room_id = item.get("room_id") or item.get("id")
        if not room_id:
            continue
        
        rooms_count += 1
        # 请求单个直播间页面来抓取它当前生效的加密/未加密 m3u8 线路
        room_url = f"{HOST}/pc/room/{room_id}"
        try:
            room_html = get_html_soup(room_url)
            room_data = extract_next_data(room_html)
            room_props = room_data.get("props", {}).get("pageProps", {})
            room_detail = room_props.get("initRoomDetail") or room_props.get("roomDetail") or {}
        except Exception:
            # 某个房间请求失败则跳过，不影响大局
            continue

        # 提取线路集合
        signals = room_detail.get("signals") or []
        if not signals:
            anchor = room_detail.get("anchor") or {}
            if anchor.get("stream_url"):
                signals = [anchor]
            elif item.get("stream_url"):
                signals = [item]

        # 建立元数据
        home = safe_text(item.get("home_team") or room_detail.get("home_team"))
        away = safe_text(item.get("away_team") or room_detail.get("away_team"))
        league = safe_text(item.get("league_name") or item.get("league") or "体育赛事")
        logo = normalize_url(item.get("home_team_logo") or item.get("cover") or item.get("screenshot"))

        for sig in signals:
            stream = safe_text(sig.get("stream_url") or sig.get("url") or sig.get("play_url") or sig.get("m3u8"))
            if not stream:
                continue
            
            # 部分流在不通协议下是相对路径
            if stream.startswith("/") and not stream.startswith("//"):
                stream = HOST + stream
            elif stream.startswith("//"):
                stream = "https:" + stream

            if stream in seen_streams:
                continue
            seen_streams.add(stream)

            signal_name = safe_text(sig.get("name") or sig.get("title"), "线路")
            
            # 组装播放器友好的标准标题
            title_parts = [league]
            if home and away:
                title_parts.append(f"{home} vs {away}")
            title_parts.append(signal_name)
            full_title = clean_title(" | ".join([p for p in title_parts if p]))

            ext = f'#EXTINF:-1 tvg-logo="{logo}" group-title="{league}",{full_title}'
            lines.append(ext)
            lines.append(stream)
            count += 1

    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count, rooms_count


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def serve_file(port):
    ip = get_lan_ip()
    print("\n已启动局域网服务。")
    print(f"iPhone 和电脑/安卓设备连接同一个 Wi‑Fi 后，在播放器里导入：")
    print(f"http://{ip}:{port}/coffee_live.m3u")
    print("\n保持这个窗口不要关闭。按 Ctrl+C 停止。\n")
    httpd = ThreadingHTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    httpd.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="生成咖啡直播 iPhone M3U 直播列表")
    parser.add_argument("--output", "-o", default="coffee_live.m3u", help="输出 M3U 文件名")
    parser.add_argument("--serve", action="store_true", help="生成后启动局域网 HTTP 服务")
    parser.add_argument("--port", type=int, default=8099, help="HTTP 服务端口")
    args = parser.parse_args()

    try:
        count, rooms = generate_m3u(args.output)
        print(f"生成完成：{args.output}")
        print(f"当前在线直播房间：{rooms} 个，抓取到有效播放线路：{count} 条")
        if count == 0:
            print("提示：当前没有正在进行的比赛，或网站改版了标签。")
        if args.serve:
            serve_file(args.port)
    except (HTTPError, URLError) as e:
        print("访问咖啡直播接口失败：", e)
        sys.exit(1)
    except Exception as e:
        print("生成失败：", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
