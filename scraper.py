python
import os
import re
import json
import requests
from bs4 import BeautifulSoup

def fetch_m3u8_from_room(room_id):
    """
    针对具体房间号抓取 m3u8 链接
    """
    url = f"https://kafeizhibo.cc{room_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://kafeizhibo.cc"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"房间 {room_id} 请求失败，状态码: {response.status_code}")
            return None
        
        # 1. 尝试直接通过正则匹配页面中所有的 m3u8 链接
        # 匹配包括绝对路径和相对路径下的 .m3u8
        m3u8_matches = re.findall(r'https?://[^\s"\']+\.m3u8[^\s"\']*', response.text)
        
        if m3u8_matches:
            # 优先返回第一个匹配到的流媒体地址
            return m3u8_matches[0]
            
        # 2. 如果未直接匹配到完整 URL，说明是相对路径，尝试抓取 script 中的变量
        # 常见命名如: var videoUrl = "xxx"; 或 source: "xxx"
        script_match = re.search(r'(?:url|source|videoUrl)\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', response.text)
        if script_match:
            stream_url = script_match.group(1)
            # 如果是相对路径，拼接上主域名
            if stream_url.startswith('//'):
                stream_url = 'https:' + stream_url
            elif stream_url.startswith('/'):
                stream_url = 'https://kafeizhibo.cc' + stream_url
            return stream_url
            
        print(f"房间 {room_id} 页面中未解析到 m3u8 链接")
        return None

    except Exception as e:
        print(f"抓取房间 {room_id} 发生异常: {e}")
        return None

def get_all_active_rooms():
    """
    从首页动态获取当前正在直播的所有房间 ID
    """
    index_url = "https://kafeizhibo.cc"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    room_ids = []
    try:
        res = requests.get(index_url, headers=headers, timeout=10)
        # 匹配首页所有类似 /pc/room/1436 的链接
        rooms = re.findall(r'/pc/room/(\d+)', res.text)
        room_ids = list(set(rooms)) # 去重
        print(f"当前首页共发现 {len(room_ids)} 个正在直播的房间")
    except Exception as e:
        print(f"获取首页房间列表失败: {e}")
    return room_ids

def main():
    # 1. 自动获取所有直播间
    rooms = get_all_active_rooms()
    
    # 如果首页抓不到，可以硬编码你截图里的常用房间作为兜底测试
    if not rooms:
        rooms = ["1436"] 
        
    results = {}
    
    # 2. 遍历每个房间抓取 m3u8
    for room in rooms:
        print(f"正在提取房间 {room} 的直播源...")
        m3u8_url = fetch_m3u8_from_room(room)
        if m3u8_url:
            results[room] = m3u8_url
            
    # 3. 保存为标准的文本或 JSON 供播放器订阅
    output = {
        "updated_at": "2026-10-29 11:11:00",
        "live_sources": results
    }
    
    with open("live_list.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=4)
        
    # 同时生成一份可以直接导入小电视或PotPlayer的 .m3u 格式
    with open("live_list.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for room, url in results.items():
            f.write(f"#EXTINF:-1, 咖啡直播 - 房间 {room}\n{url}\n")

if __name__ == "__main__":
    main()
