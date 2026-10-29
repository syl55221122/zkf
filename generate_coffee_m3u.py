# -*- coding: utf-8 -*-
"""
咖啡直播 iPhone M3U 生成工具 (完美融合防灾版)
作用：基于你原本百分百可用的接口逻辑，加入自动化运行的防空值、防崩溃安全锁。
"""
import argparse
import json
import socket
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

HOST = "https://kafeizhibo.cc"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
TIMEOUT = 10

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Referer": HOST + "/live/all",
    "Origin": HOST,
}


def http_json(url, referer=None, timeout=TIMEOUT):
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        text = raw.decode("utf-8", errors="ignore")
        return json.loads(text)
    except Exception as e:
        # 🔥 安全锁 1：记录错误但不抛出异常中断程序，支持自动化平稳运行
        print(f"请求接口失败: {url} -> 错误原因: {e}")
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


def fetch_live_rooms():
    """获取当前直播房间列表。"""
    # 💯 保留你最有效的原版房间列表接口
    data = http_json(HOST + "/api/v1/archor", referer=HOST + "/live/all")
    if not data or str(data.get("code")) != "200":
        return []
    items = data.get("data") or []
    if not isinstance(items, list):
        return []
    return items


def fetch_room_detail(room_id):
    """获取单个房间线路。"""
    # 💯 保留你最有效的原版房间详情接口
    url = HOST + "/api/v1/room/" + quote(str(room_id))
    try:
        data = http_json(url, referer=HOST + "/room/" + quote(str(room_id)))
        if not data or str(data.get("code")) != "200":
            return None
        return data.get("data") or {}
    except Exception:
        return None


def build_title(item, room_info=None, signal_name=None):
    room_info = room_info or {}
    home = safe_text(room_info.get("home_team") or item.get("home_team"))
    away = safe_text(room_info.get("away_team") or item.get("away_team"))
    league = safe_text(room_info.get("league") or item.get("league_name") or item.get("league"), "赛事")
    anchor = safe_text(item.get("name") or item.get("nickname") or item.get("title"))
    score_h = safe_text(room_info.get("home_score") or item.get("home_score"), "")
    score_a = safe_text(room_info.get("away_score") or item.get("away_score"), "")

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
        parts.append(signal_name)
    return clean_title(" | ".join([p for p in parts if p]))


def extract_logo(item, detail=None):
    detail = detail or {}
    teams = detail.get("teams") or {}
    candidates = [
        item.get("screenshot"),
        item.get("cover"),
        item.get("home_team_logo"),
        (teams.get("home") or {}).get("logo"),
        (teams.get("away") or {}).get("logo"),
    ]
    for c in candidates:
        url = normalize_url(c)
        if url and "default" not in url:
            return url
    return ""


def generate_m3u(output_path):
    rooms = fetch_live_rooms()
    lines = ["#EXTM3U"]
    count = 0
    seen = set()

    for item in rooms:
        if not isinstance(item, dict):
            continue
        room_id = item.get("room_id") or item.get("id") or item.get("roomId")
        if not room_id:
            continue
        
        detail = fetch_room_detail(room_id) or {}
        signals = detail.get("signals") or []
        
        if not signals:
            archor = detail.get("archor") or {}
            if archor.get("stream_url"):
                signals = [archor]
            elif item.get("stream_url"):
                signals = [item]

        room_info = detail.get("room_info") or {}
        logo = extract_logo(item, detail)
        group = safe_text(room_info.get("league") or item.get("league_name") or "咖啡直播")

        for sig in signals:
            if not isinstance(sig, dict):
                continue
            stream = safe_text(sig.get("stream_url") or sig.get("url") or sig.get("play_url") or sig.get("m3u8"))
            if not stream:
                continue
                
            stream = normalize_url(stream)
            if stream in seen:
                continue
            seen.add(stream)
            
            signal_name = safe_text(sig.get("name") or sig.get("title"), "线路")
            title = build_title(item, room_info, signal_name)
            ext = f'#EXTINF:-1 tvg-logo="{logo}" group-title="{group}",{title}'
            lines.append(ext)
            lines.append(stream)
            count += 1

    # 🔥 安全锁 2：只要抓到数据，就立即写入或更新本地文件
    if count > 0:
        Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"成功将最新抓取的 {count} 条有效线路写入 {output_path}")
    else:
        print("💡 提示：当前没有抓取到有效播放线路，本地文件保持原样，防止被空白覆盖。")
        
    return count, len(rooms)


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
    print(f"iPhone 和电脑设备连接同一个 Wi‑Fi 后，在播放器里导入：")
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
        print(f"本次扫描完成：{args.output}")
        print(f"当前捕捉到在线房间：{rooms} 个，已成功组装播放线路：{count} 条")
        if args.serve:
            serve_file(args.port)
    except Exception as e:
        print("脚本执行遭遇未知异常，已自动拦截安全退出：", e)
        sys.exit(0)


if __name__ == "__main__":
    main()
