# Champions 战术台

这是一个可直接由 GitHub Pages 发布的静态网页，无需 Node.js 或后端。

## 发布

仓库的 **Settings → Pages → Build and deployment** 请选择 **GitHub Actions**。之后每次推送到 `main`，工作流会自动更新网站。

## 数据范围

`data/champions-db.js` 当前包含 Regulation M-B 的 323 个可用形态（其中 76 个超级进化）、500 个招式、151 件道具和 201 个特性。页面将普通形态与超级进化分组，并按照每只宝可梦的合法招式表生成下拉列表。

数据生成脚本为 `scripts/update_champions_data.py`。安装 `requests` 与 `beautifulsoup4` 后运行：

```powershell
python scripts/update_champions_data.py
```

脚本使用以下公开资料交叉整理：

- Smogon Champions：可用形态、种族值、属性、特性、招式表、道具与招式参数
- 52Poké Champions 列表：版本/可用名单核对
- PokéStats 开源项目：中文名称翻译
- Pikalytics Champions：当前使用率排序

选择宝可梦时会优先采用 Pikalytics Regulation M-B S3 当前最高使用率的四个招式、道具、特性、性格和能力点分配。没有独立统计的战斗/超级形态会继承基础形态统计；仍无足够样本时才回退到合法招式默认配置。

`pokechampdb.com` 当前证书无效，脚本不会绕过 TLS 安全校验。Bulbapedia/口袋图鉴可用于人工复核，但不是自动生成所必需的数据源。

Champions 使用“能力点数”：单项最多 32 点，总计最多 66 点；不是系列正作的 252/510 努力值制。

队伍预设保存在访问者自己的浏览器 Local Storage 中。
