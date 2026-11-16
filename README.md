# indie-ledger

> Show HN 上线产品的后续追踪台账：谁还活着，谁死了。

[![data](https://img.shields.io/badge/data-89%2C350_posts-blue)](data/ledger.jsonl)
[![rules](https://img.shields.io/badge/rules-2026--11--12-green)](docs/RULES.md)
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
| Show HN 帖子（2024-01-01 → 2026-11-12） | 89,350 |
| 去重后独立产品 | 62,073 |
| 去重后 GitHub 仓库 | 24,895 |
| 去重后产品域名 | 38,828 |

> 四个数字是不同口径，不能互相替代：62,073 是产品，24,895 是仓库，
> 38,828 是域名。一个域名可以承载多个 Show HN 产品，一个仓库也可以
> 被多个产品引用——写「X / Y 已核验」之前必须确认 X 和 Y 数的是同一种东西。

原始帖子类型分布：`product_site` 56% · `github` 32% · `hosting` 5% · `no_url` 5% · `platform` 2% · `content_host` 0%。

平台类域名（google.com、huggingface.co、apple.com、workers.dev、blogspot.com、itch.io 等）已剔除。

---

## 置信度分层

不同证据强度下的结论**永远不混在同一张表里**。

| 层 | 证据 | 强度 | 覆盖 |
|---|---|---|---|
| **高** | GitHub 仓库状态（404 / archived / pushed_at） | 无歧义 | **24,929 / 24,929 仓库，已全量核验** |
| **中** | DNS + HTTP 响应 | 可确认站点消失，无法区分死掉与搬家 | **38,828 / 38,828 域名，已全量核验** |
| **无** | 无信号 | — | 计入 `unverified`，不参与任何比率 |

两层都已扫描完毕：GitHub 侧 24,929 个仓库、域名侧 38,828 个产品域名，
无重复、无遗漏，每条判定都带证据。报告中的队列存活率是终值，
不再随采样推进而变化。

---

## 判定规则

### GitHub 侧（高置信）

| 信号 | 判定 |
|---|---|
| 仓库 404 / 删除 | `dead` |
| `archived = true` | `archived`（**独立判定**，计入「不再维护」，不计入「已死」） |
| `pushed_at` 超过 365 天 | `dormant` |
| `pushed_at` 180–365 天 | `slowing` |
| `pushed_at` 180 天内 | `active` |

> 限流、网络失败、403 次级限流、451 法律下架、仓库封禁**都不写判定**，
> 分别记为 `unavailable` / `repo_blocked`，绝不写成 `dead`。

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
10. 跨批次比较必须先对齐暴露期（被观察时长），再谈显著性
11. 描述性事实不需要 p 值，但也不允许因为没有 p 值就被丢掉

完整版见 [`docs/RULES.md`](docs/RULES.md)，含 **P1–P48 判例库** —— 每一条都对应一次真实事故，带量级。

其中三条直接约束「哪些数字可以对外说」：

- **P32**：一年检验十几个赛道时必须做 Holm–Bonferroni 校正，裸 `p<0.05` 不构成结论
- **P44**：跨批次比较必须**先对齐暴露期**再检验；分层后效应消失，说明测的是观察者
- **P46**：描述性分布不需要显著性检验，但也不能因为没有 p 值就丢掉

> 早期版本曾发布「2024 年电商/金融赛道死亡率 17.5%、全库唯一显著」，
> 经复核后该结论**不成立**（分类错误 + 暴露期混淆），已在报告中明确撤回。

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
  categories.jsonl    # 行业标签（taxonomy v2）
  category_audit.json # 分类精度审计结果
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

`active` · `slowing` · `dormant` · `archived` · `dead` · `unavailable` · `repo_blocked` · `unverified`

GitHub 层与域名层的判定名不同域（域名层是 `alive` / `dead` / `degraded` / `blocked`），
两层分母永远不合并。

---

## License

MIT（数据与代码）。Show HN 帖子内容遵循 Hacker News 官方条款；GitHub 元数据来自各仓库公开 API。
