# -*- coding: utf-8 -*-
"""
咖啡直播 iPhone M3U 生成工具 (终极修正版)
作用：从 kafeizhibo.cc 动态抓取当前在线直播房间，解析线路，生成可用的 coffee_live.m3u。
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

# ⚙️ 强化请求头：模拟最真实的浏览器跨域请求环境，避开服务器 IP 拦截
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": HOST + "/",
    "Origin": HOST,
    "Sec-Ch-Ua": '"Not A(User%20Agent";v="8", "Chromium";v="134", "Google Chrome";v="134"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin"
}

def http_json(url, referer=None, timeout=TIMEOUT):
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
    
    # 增加反防盗链动态时间戳字段
    if "?" in url:
        url += f"&_t={int(time.time() * 1000)}"
    else:
        url += f"?_t={int(time.time() * 1000)}"

    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        text = raw.decode("utf-8", errors="ignore")
        return json.loads(text)
    except Exception as e:
        print(f"请求接口失败 [{url}]: {e}")
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
    """获取当前真实在线的直播房间列表。"""
    # 💡 切换为当前正在使用的聚合赛程和热门推荐房间真实 API
    url = HOST + "/api/v1/live/index"
    data = http_json(url, referer=HOST + "/")
    
    if not data or str(data.get("code")) != "200":
        # 备用方案：尝试请求 /api/v1/match/list
        print("主直播接口未返回数据，尝试备用比赛列表接口...")
        data = http_json(HOST + "/api/v1/match/list", referer=HOST + "/live/all")
        
    if not data:
        return []
        
    raw_data = data.get("data") or {}
    items = []
    
    # 解析可能存在的不同数据嵌套结构
    if isinstance(raw_data, list):
        items = raw_data
    elif isinstance(raw_data, dict):
        # 兼容最新结构的分类列表 (如 hot、recommend 等)
        for key in ["hot", "recommend", "list", "live"]:
            if isinstance(raw_data.get(key), list):
                items.extend(raw_data.get(key))
                
    # 兜底清洗，提取出含有有效 room_id 的集合
    valid_rooms = []
    seen_ids = set()
    for item in items:
        rid = item.get("room_id") or item.get("id") or item.get("roomId")
        if rid and rid not in seen_ids:
            seen_ids.add(rid)
            valid_rooms.append(item)
            
    return valid_rooms

def fetch_room_detail(room_id):
    """获取单个房间线路。"""
    url = HOST + "/api/v1/room/" + quote(str(room_id))
    try:
        data = http_json(url, referer=HOST + f"/pc/room/{room_id}")
        if not data or str(data.get("code")) != "200":
            return None
        return data.get("data") or {}
    except Exception:
        return None

def build_title(item, room_info=None, signal_name=None):
    room_info = room_info or {}
    home = safe_text(room_info.get("home_team") or item.get("home_team") or item.get("homeName"))
    away = safe_text(room_info.get("away_team") or item.get("away_team") or item.get("awayName"))
    league = safe_text(room_info.get("league") or item.get("league_name") or item.get("leagueName"), "赛事")
    anchor = safe_text(item.get("name") or item.get("nickname") or item.get("title") or item.get("anchor_name"))
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
        item.get("homeLogo"),
        (teams.get("home") or {}).get("logo"),
    ]
    for c in candidates:
        url = normalize_url(c)
        if url and "default" not in url:
            return url
    return ""

def generate_m3u(output_path):
    rooms = fetch_live_rooms()
    print(f"正在分析获取到的 {len(rooms)} 个潜在直播间...")
    lines = ["#EXTM3U"]
    count = 0
    seen = set()

    for item in rooms:
        room_id = item.get("room_id") or item.get("id") or item.get("roomId")
        if not room_id:
            continue
            
        detail = fetch_room_detail(room_id) or {}
        signals = detail.get("signals") or []
        
        # 容错：有些流地址直接下发在主房间中
        if not signals:
            anchor = detail.get("archor") or detail.get("anchor") or {}
            if anchor.get("stream_url") or anchor.get("m3u8"):
                signals = [anchor]
            elif item.get("stream_url") or item.get("m3u8"):
                signals = [item]

        room_info = detail.get("room_info") or detail.get("match_info") or {}
        logo = extract_logo(item, detail)
        group = safe_text(room_info.get("league") or item.get("league_name") or "咖啡直播")

        for sig in signals:
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

    # 只有当成功抓到线路时才覆写本地文件，防止没有比赛时生成空列表覆盖之前的记录
    if count > 0:
        Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
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
    print(f"iPhone 和电脑设备连接同一个 Wi-Fi 后，在播放器里导入：")
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
        print(f"直播房间：{rooms} 个，发现并成功组装有效播放线路：{count} 条")
        if count == 0:
            print("提示：当前时间段可能没有比赛正在直播，或者接口被安全盾拦截。请稍后再试。")
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
