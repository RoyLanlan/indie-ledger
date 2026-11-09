# indie-ledger

> Show HN 上线产品的后续追踪台账：谁还活着，谁死了。

[![data](https://img.shields.io/badge/data-88%2C874_posts-blue)](data/ledger.jsonl)
[![rules](https://img.shields.io/badge/rules-2026--11--09-green)](docs/RULES.md)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

面向中国出海开发者的独立开发者产品情报。以 Show HN 为发现入口，以 GitHub 仓库状态为高置信证据，以域名信号为低置信补充。

**这个仓库回答一个问题**：2024 年那批在 Show HN 上线的独立产品，今天还剩多少？

---

## 为什么不是又一个目录

海外已经有多个独立开发者目录、分类站和榜单（indiemakerlist、indiehackerdirectory、indiegems、useek 等），也已发布过《中国独立开发者报告：11.5% 的产品已不再维护》。

所以这个仓库**不做链接目录**，做的是：

1. **可审计的数据集** —— 每个产品的状态都带证据和判定规则版本
2. **存活台账** —— 记录状态，不做目录式罗列
3. **判决规则库** —— 每一条规则都对应一次真实事故

目录的价值在于「找到产品」；台账的价值在于「看清一个赛道的死亡率与存活时长」。

---

## 数据规模

| 指标 | 数值 |
|---|---|
| Show HN 帖子（2024-01-01 → 2026-11-09） | 88,874 |
| 去重后独立产品 | 61,708 |
| 去重后 GitHub 仓库 | 24,767 |
| 去重后产品域名 | 36,881 |

原始帖子类型分布：`product_site` 55% · `github` 31% · `hosting` 5% · `no_url` 4% · `platform` 2% · `content_host` 0%。

平台类域名（google.com、huggingface.co、apple.com、workers.dev、blogspot.com、itch.io 等）已剔除。

---

## 置信度分层

不同证据强度下的结论**永远不混在同一张表里**。

| 层 | 证据 | 强度 | 覆盖 |
|---|---|---|---|
| **高** | GitHub 仓库状态（404 / archived / pushed_at） | 无歧义 | 24,767 个仓库 |
| **中** | DNS + HTTP 响应 | 可确认站点消失，无法区分死掉与搬家 | 36,881 个域名 |
| **无** | 无信号 | — | 计入 `unverified`，不参与任何比率 |

---

## 判定规则

### GitHub 侧（高置信）

| 信号 | 判定 |
|---|---|
| 仓库 404 / 删除 | `dead` |
| `archived = true` | `dead` |
| `pushed_at` 超过 365 天 | `dormant` |
| `pushed_at` 180–365 天 | `slowing` |
| `pushed_at` 180 天内 | `active` |

### 域名侧（中置信）

1. NXDOMAIN 二次确认（间隔约 1 秒）→ `dead`
2. 80 或 443 任一端口返回 HTTP < 400 → `alive`
3. HTTP 403 / 405 / 429 → `blocked`（**绝不判死**）
4. DNS 可解析 + 端口开放但请求异常 → `degraded`（**绝不判死**）
5. DNS 可解析 + 无监听 → `dead`

> **TLS 证书错误 ≠ 死亡。** 证书过期只说明证书烂了，站点还活着。
> 初版规则把这类情况算作死亡，实测精度只有 53%；修正后降到 13%。

---

## 硬红线

1. 每个公开数字都能回溯到 `data/ledger.jsonl`、`data/repo_status.jsonl`、`data/liveness.jsonl` 的具体行
2. 样本量不足 30 的分组不发布
3. 没有证据的判定不可发布
4. 用「不再维护」，不用「失败」
5. 不承诺营收；MRR 数据必须有公开来源和日期
6. 明确声明总体范围：**Show HN 发帖者**，不是「独立开发者」
7. 条目被移除 ≠ 产品死亡
8. 同数据同输出；报告正文不写时间戳
9. 不宣称任何比率代表整个行业

完整版见 [`docs/RULES.md`](docs/RULES.md)，含 **P1–P12 判例库** —— 每一条都对应一次真实事故，带量级。

---

## 已知偏差

1. 样本是「在 Show HN 发帖的开发者」，不等于全部独立开发者 —— 系统性高估公开构建者与英语使用者
2. GitHub 侧判定偏向开源项目；闭源工具主要靠域名信号，覆盖度较低
3. 「不再维护」不等于「失败」—— 很多项目是主动完成
4. 域名失效可能源于换域名或搬家，被计为死亡属于已知误差

---

## 目录结构

```
data/
  ledger.jsonl        # 主账：每条 Show HN 帖子的产品信息
  repo_status.jsonl   # GitHub 核验结果
  liveness.jsonl      # 域名核验结果
  products.json       # 关联后的产品级视图
  days/               # 逐日原始采集
docs/
  RULES.md            # 完整判定规则 + 判例库
  METHODOLOGY.md      # 方法论与已知偏差
reports/
  YYYY-Www.md         # 周期性报告
src/                  # 采集与判定脚本
```

---

## 复现

```bash
python3 src/backfill.py --start 2024-01-01 --end $(date -I) --outdir data
python3 src/github_sampler.py          # 需要 GITHUB_TOKEN
python3 src/domain_scan.py --delay 1.0
python3 src/analyze.py
python3 src/render_report.py
```

---

## 状态口径

`active` · `slowing` · `dormant` · `dead` · `unverified`

---

## License

MIT（数据与代码）。Show HN 帖子内容遵循 Hacker News 官方条款；GitHub 元数据来自各仓库公开 API。
