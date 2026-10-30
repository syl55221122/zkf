# -*- coding: utf-8 -*-
import os
import re
import json
import requests
import base64
from urllib.parse import quote, unquote
from datetime import datetime, timezone, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

class QiubaM3UGenerator:
    def __init__(self):
        self.host = "https://www.qiuba001.vip"
        self.api = self.host + "/api"
        
        # 更加完整的浏览器 Headers，绕过简单的反爬
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.host + "/",
            "Origin": self.host,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin"
        }
        
        # 配置带有重试机制的 session
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

    def _get(self, url):
        try:
            r = self.session.get(url, headers=self.headers, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"GET请求失败 {url}: {e}")
            return {}

    def _post(self, url, data):
        try:
            r = self.session.post(url, json=data, headers=self.headers, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"POST请求失败 {url}: {e}")
            return {}

    def _decode_play_url(self, encrypted):
        if not encrypted or not encrypted.startswith("crawler_b64:"):
            return ""
        try:
            encoded = encrypted.split(":", 1)[1]
            encoded = encoded.replace("-", "+").replace("_", "/")
            padding = 4 - len(encoded) % 4
            if padding != 4:
                encoded += "=" * padding
            return base64.b64decode(encoded).decode("utf-8")
        except:
            return ""

    def get_real_url(self, source_id):
        session_data = self._post(f"{self.api}/play/session", {"source_id": source_id})
        if session_data and "real_url_encrypted" in session_data:
            return self._decode_play_url(session_data["real_url_encrypted"])
        return ""

    def generate(self, filename="live.m3u"):
        print("开始获取球吧直播赛事...")
        match_data = self._get(f"{self.api}/matches?page=1&page_size=100")
        items = match_data.get("items", [])
        
        if not items:
            print("未获取到任何赛事信息。")
            # 即使没获取到，也创建一个空的或者保留旧的m3u文件，避免git commit报错中断
            if not os.path.exists(filename):
                with open(filename, "w", encoding="utf-8") as f:
                    f.write('#EXTM3U\n')
            return

        m3u_content = '#EXTM3U x-tvg-url=""\n'
        
        count = 0
        for item in items:
            match_id = str(item.get("id", ""))
            home_team = item.get("home_team", {})
            away_team = item.get("away_team", {})
            competition = item.get("competition", {})
            
            home_name = home_team.get("name", "主队")
            away_name = away_team.get("name", "客队")
            competition_name = competition.get("name", "综合体育")
            status = item.get("status", "not_started")
            
            if status == "finished":
                continue

            match_title = f"{competition_name}: {home_name} vs {away_name}"
            
            signals_data = self._get(f"{self.api}/matches/{match_id}/signals")
            signals = []
            if isinstance(signals_data, dict):
                signals = signals_data.get("signals", [])
            elif isinstance(signals_data, list):
                signals = signals_data

            if not signals:
                continue

            for idx, sig in enumerate(signals):
                source_id = sig.get("source_id", "")
                display_name = sig.get("display_name", f"信号{idx+1}")
                
                if not source_id:
                    continue
                
                real_url = self.get_real_url(source_id)
                if not real_url:
                    continue
                
                group_title = competition_name
                tvg_name = f"{home_name} vs {away_name}"
                tvid = f"qiuba_{match_id}_{idx}"
                
                m3u_content += f'#EXTINF:-1 tvg-id="{tvid}" tvg-name="{tvg_name}" group-title="{group_title}",{match_title} - {display_name}\n'
                m3u_content += f"{real_url}\n"
                count += 1

        with open(filename, "w", encoding="utf-8") as f:
            f.write(m3u_content)
        print(f"成功生成 M3U 文件，共收录 {count} 个有效直播源，保存为 {filename}。")

if __name__ == "__main__":
    generator = QiubaM3UGenerator()
    generator.generate()
