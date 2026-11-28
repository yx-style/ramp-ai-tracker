# Ramp 企业 token 支出：Anthropic 追踪

用 [Ramp AI Index](https://ramp.com/data/ai-index) 的公开周度接口 `tokenVolumes`，跟踪 Anthropic 在美国企业 token 支出中的份额、用量和单位 token 支出。

- 页面：`docs/`（静态 HTML，无构建依赖），由 GitHub Pages 发布
- 数据：`scripts/build.py` 抓取接口和看板 Tokenomics 表，生成 `docs/data/`；每次接口数据变化在 `data/snapshots/` 留一份原始快照，用于对比修订
- 更新：`.github/workflows/update.yml` 每天运行一次，数据有变化才提交

默认按周拼接。2026-11-01 及以前使用官方厂商指数和当时的模型序列，冻结数据来自 `2026-11-19T0749Z.json.gz`，与 `3c4c2ca` 发布的周序列相同。11 月 2 日起按模型汇总。页面可切换到最新口径全历史，CSV 与当前视图一致。

`data/calibers/<生效周一>_<来源>.json` 保存截至切换前最后一周的厂商、系列和模型指数、原始快照 SHA-256、换算系数与逐月误差范围。每周优先取完整的官方厂商序列；同等精度取当周生效的版本。拼接版 JSON 用 `caliber_by_week` 指向 `calibers` 中的对应记录，混合序列的顶层 `usd` 为空。厂商来源发生变化时，构建从上一次发布的 `tracker-latest.json` 自动冻结旧口径，仅创建新文件。若 Ramp 公布了厂商来源不变的方法调整，在首次构建该次数据时运行 `python3 scripts/build.py --new-caliber`。常规历史修订和指数缩放仍在当前口径内更新。

本地预览：

```sh
python3 scripts/build.py
cd docs && python3 -m http.server 8765
```

## YipitData Anthropic

`docs/yipit.html` 使用 YipitData Anthropic Data File 和同期研究报告。页面与已提交的 `docs/data/yipit.json` 随 GitHub Pages 一起发布。日常 Ramp 更新只重建 Ramp 数据。

下载的 XLSX 放在仓库外。在仓库根目录运行：

```sh
/Users/owen/projects/yipit-data/.venv/bin/python scripts/build_yipit.py /Users/owen/projects/yipit-data/raw/*.xlsx
python3 -m http.server 8766 --bind 127.0.0.1 --directory docs
```

打开 <http://127.0.0.1:8766/yipit.html>。脚本按文件名中的日期识别报告，支持一次传入多份文件，也可只传新一期。重跑同一期会替换该期数据，其余期次保留。页面可切换期次，并在「历史期次与修订」中对比相邻两期的同一观察值。

脚本使用 openpyxl。公式没有缓存时，会用本机 LibreOffice 在临时目录重算。也可传入已由 Excel 重算并保存的副本。网页无需安装依赖。新增期次的图表从工作簿生成。报告摘要及报告专属外推按发布日期单独维护。工作表改名或表头变动时应检查脚本中的字段映射。

## GPU 租价汇总

`docs/gpu-rent.html` 按型号、地区、产品和租期比较公开报价，单独列示公司披露和合同。GitHub Actions 每天 13:23 UTC 更新，可手动选择 `gpu-rent` 单独运行。

```sh
python -m pip install -r scripts/gpu-rent-requirements.txt
python scripts/build_gpu_rent.py
python -m unittest discover -s tests -v
```

每个自动来源单独检查 robots.txt、下载并验证报价。同日重试保留已经确认的记录。缺报时，原有报价保留上次观察日期，检查状态记录当次结果。大多数来源按 UTC 日期和内容摘要在 `data/gpu-rent/<来源>/raw/` 保存 gzip 快照。Oracle 仅归档每个观察日的美元报价、料号及来源链接。AWS 旧版区域 CSV 保留表头和选定实例的原始行，导出数据附有源文件地址。

Impossible Cloud 的普通预留挂牌和候补起价分别计数。GPU Rental Prices 出现同一欧盟 SKU 的普通预留挂牌时，日更按实际采集日归档。SAVRN 的普通报价通过获许可的文件手工导入。

Nebius 任务用本次日线重建并验证 GPU 导出，两者一起提交。打包 Pages 时，工作流对照线上版本。首次发布或 GPU 文件有变化时，工作流在打包前重新核对合约、CSV、目录和分片。

历史观察点存于 `data/gpu-rent/historical.json`，合同及出处存于 `docs/data/gpu-contracts.json`。单独运行 `python scripts/build_gpu_rent.py --export-only` 可从本地归档重建汇总，无需联网。走势图的 JSON 按同一来源、型号、实例、口径、期限和地区分组，points 每行依次为观察日、美元／GPU 小时、计算说明、来源 URL、可公开的快照路径（如有）和配置等补充字段。Nebius 现货引用已有日线归档。受限制的指数按各自条款在页面注明。
