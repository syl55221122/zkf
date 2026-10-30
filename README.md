# 球吧体育数据自动更新

用 GitHub Actions 定时运行 `qiuba.py` 抓取比赛数据，自动提交到仓库的 `data/matches.json`。

## 文件说明

| 文件 | 作用 |
|---|---|
| `qiuba.py` | 原始 Spider 脚本（未改动） |
| `runner.py` | 独立运行入口：调用 Spider 抓取分类 + 直播比赛，输出 JSON |
| `base/spider.py` | 最小基类桩，让 `qiuba.py` 脱离 TVBox 框架也能独立运行 |
| `requirements.txt` | 依赖（requests） |
| `.github/workflows/qiuba.yml` | GitHub Actions 定时任务配置 |
| `data/matches.json` | 抓取结果（由 Actions 自动更新） |

## 部署步骤

1. 在 GitHub 新建一个仓库（公开或私有均可）。
2. 把本目录所有文件上传到仓库根目录。
3. 打开仓库的 **Actions** 标签页，启用 workflow；也可以点 **Run workflow** 手动触发一次验证。
4. 之后每小时整点（UTC）自动运行一次，数据变化时会自动 commit 并 push。

## 调整运行频率

编辑 `.github/workflows/qiuba.yml` 里的 cron 表达式（UTC 时间）：

```yaml
- cron: "0 * * * *"     # 每小时
- cron: "*/30 * * * *"  # 每 30 分钟
- cron: "0 0 * * *"     # 每天 UTC 0 点（北京时间 8 点）
```

## 本地运行

```bash
pip install -r requirements.txt
python runner.py
```

## 输出格式

`data/matches.json`：

```json
{
  "updated_at": "2026-10-30 12:57:06",
  "home": { "class": [...], "list": [...] },
  "categories": [ { "type_id": "...", "type_name": "...", "list": [...] } ]
}
```
