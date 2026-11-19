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
