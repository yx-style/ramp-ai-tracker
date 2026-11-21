# Ramp 企业 token 支出：Anthropic 追踪

用 [Ramp AI Index](https://ramp.com/data/ai-index) 的公开周度接口 `tokenVolumes`，跟踪 Anthropic 在美国企业 token 支出中的份额、用量和单位 token 支出。

- 页面：`docs/`（静态 HTML，无构建依赖），由 GitHub Pages 发布
- 数据：`scripts/build.py` 抓取接口和看板 Tokenomics 表，生成 `docs/data/`；每次接口数据变化在 `data/snapshots/` 留一份原始快照，用于对比修订
- 更新：`.github/workflows/update.yml` 每天运行一次，数据有变化才提交

接口只提供指数（每个「指标 × 维度」组合单周最高值 = 100），本站只展示份额、环比和相对单位支出，不推算美元金额。口径细节见页面「方法」一节。

本地预览：

```sh
python3 scripts/build.py
cd docs && python3 -m http.server 8765
```

## YipitData Anthropic

`docs/yipit.html` 使用 YipitData Anthropic Data File 和同期研究报告。该部分含付费数据，上线前需访问控制，保护范围包括页面和 `docs/data/yipit.json`。当前只在本地使用。

下载的 XLSX 放在仓库外。在仓库根目录运行：

```sh
/Users/owen/projects/yipit-data/.venv/bin/python scripts/build_yipit.py /Users/owen/projects/yipit-data/raw/*.xlsx
python3 -m http.server 8766 --bind 127.0.0.1 --directory docs
```

打开 <http://127.0.0.1:8766/yipit.html>。脚本按文件名中的日期识别报告，支持一次传入多份文件，也可只传新一期。重跑同一期会替换该期数据，其余期次保留。页面可切换期次，并在「历史期次与修订」中对比相邻两期的同一观察值。

脚本使用 openpyxl。公式没有缓存时，会用本机 LibreOffice 在临时目录重算。也可传入已由 Excel 重算并保存的副本。网页无需安装依赖。新增期次的图表从工作簿生成。报告摘要及报告专属外推按发布日期单独维护。工作表改名或表头变动时应检查脚本中的字段映射。
