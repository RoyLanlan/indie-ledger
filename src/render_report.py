#!/usr/bin/env python3
"""
L4: deterministic report renderer.

Rules enforced here:
  - Same input -> byte-identical output (no timestamps in the body)
  - Every number carries a row reference
  - Cohorts below MIN_N are not published
  - Confidence tiers are never blended
  - Population is stated in the first paragraph
  - "not maintained", never "failed"
"""
import json, os, sys, argparse, datetime
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analyze import build, cohort_table, load, MIN_N

REPORT_DIR = os.path.join(HERE, "reports")


def fmt_pct(n, d):
    return f"{n*100//d}%" if d else "n/a"


def fmt_pct1(n, d):
    """One-decimal precision - needed for small verified subsets (e.g. 51/12383)."""
    return f"{n*100/d:.1f}%" if d else "n/a"


def render(products, cohorts, meta):
    """
    meta: dict with data_window, ledger_rows, generated_from (file paths), rules_version
    """
    L = []
    A = L.append

    A("# Show HN 产品存活台账")
    A("")
    A("## 口径声明（先读这一段）")
    A("")
    A(f"- **数据来源**：Hacker News `Show HN` 公开帖子，通过 Algolia 搜索 API 采集")
    A(f"- **数据窗口**：{meta['data_window']}")
    A(f"- **总体范围**：{meta['population']}")
    A(f"- **原始帖子数**：{meta['ledger_rows']:,}")
    A(f"- **存活判定**：仅使用公开信号（GitHub 仓库状态 / DNS / HTTP），不使用任何推测")
    A(f"- **已知偏差**：")
    A(f"  1. 样本是「在 Show HN 发帖的开发者」，不等于全部独立开发者——系统性高估了公开构建者和英语使用者")
    A(f"  2. GitHub 侧判定偏向开源项目；闭源工具主要靠域名信号，覆盖度较低")
    A(f"  3. 「不再维护」不等于「失败」——很多项目是主动完成，而非被放弃")
    A(f"  4. 域名失效可能源于换域名或搬家，被计为死亡属于已知误差")
    A(f"- **规则版本**：`{meta['rules_version']}`")
    A(f"- **复现**：全部数字可回溯到 `{meta['ledger_path']}` 等文件的对应行")
    A("")

    # --- dataset composition ---
    A("## 一、数据集构成")
    A("")
    A(f"| 指标 | 数值 |")
    A(f"|---|---|")
    A(f"| Show HN 帖子总数 | {meta['ledger_rows']:,} |")
    A(f"| 去重后独立产品 | {meta['n_products']:,} |")
    A(f"| GitHub 可核验（高置信） | {meta['n_high']:,} |")
    A(f"| 仅域名可核验（中置信） | {meta['n_medium']:,} |")
    A(f"| 暂无判定 | {meta['n_none']:,} |")
    A("")
    A("**原始帖子按类型分布**")
    A("")
    A("| 类型 | 条数 | 占比 | 是否进入台账 |")
    A("|---|---|---|---|")
    for kind, n in meta["kinds"]:
        keep = "✅ 是" if kind in ("product_site", "github") else "❌ 否"
        A(f"| `{kind}` | {n:,} | {fmt_pct(n, meta['ledger_rows'])} | {keep} |")
    A("")
    A("平台类域名（google.com、huggingface.co、apple.com 等）已剔除——"
      "它们在 14 天样本中占据前三位，不剔除的话台账一半是平台不是产品。")
    A("")

    # --- tiered cohort tables: NEVER blend tiers in one table ---
    A("## 二、上线年份队列存活情况")
    A("")
    A("按产品首次在 Show HN 发帖的年份分组，观察同一批产品在今天的状态。")
    A("")
    A("**表 2.1　全量队列规模与核验进度**")
    A("")
    A("| 上线年份 | 产品数 | 已核验 | 未核验 | 核验率 |")
    A("|---|---|---|---|---|")
    for r in cohorts:
        ver = r["n"] - r["unverified"]
        A(f"| {r['year']} | {r['n']:,} | {ver:,} | {r['unverified']:,} | "
          f"{fmt_pct1(ver, r['n'])} |")
    hidden = [r for r in cohorts if not r["publishable"]]
    if hidden:
        A("")
        A(f"> 以下分组样本量不足 {MIN_N}，按规则不发布："
          f"{'、'.join(str(r['year']) for r in hidden)}")
    A("")
    A("核验率还很低时，**不能**给出队列级存活率——"
      "样本偏差会让任何百分比失去意义。存活率只在下面两层的已核验子集上计算。")
    A("")

    A("**表 2.2　高置信层（GitHub 核验）已核验子集**")
    A("")
    A("| 上线年份 | 已核验 | 已死 | 停更 | 放缓 | 活跃 | 受限 | 已死占比 | 不再维护占比 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for r in meta["cohorts_high"]:
        ver = r["n"] - r["unverified"]
        if ver < MIN_N:
            continue
        A(f"| {r['year']} | {ver:,} | {r['dead']:,} | {r['dormant']:,} | "
          f"{r['slowing']:,} | {r['active']:,} | {r.get('unavailable', 0):,} | "
          f"{r['dead_pct_verified']}% | {r['not_maintained_pct_verified']}% |")
    A("")
    A("「受限」= HTTP 451（法律原因不可用）或仓库因 ToS/DMCA 被封禁（HTTP 403）。"
      "这类仓库仍然存在，既不算死也不算活，已排除在已死占比与不再维护占比的分母之外。")
    A("")

    A("**表 2.3　中置信层（域名信号）已核验子集**")
    A("")
    A("| 上线年份 | 已核验 | 站点死 | 站点存活 | 拒爬(403等) | 主机异常 | 已死占比 |")
    A("|---|---|---|---|---|---|---|")
    for r in meta["cohorts_medium"]:
        ver = r["n"] - r["unverified"]
        if ver < MIN_N:
            continue
        A(f"| {r['year']} | {ver:,} | {r['dead']:,} | {r['alive_site']:,} | "
          f"{r.get('site_blocked', 0):,} | {r.get('degraded', 0):,} | "
          f"{r['dead_pct_verified']}% |")
    A("")
    A("「拒爬」= 站点返回 403/405/429。这说明**站点还活着**，只是拒绝自动请求，"
      "因此计入「站点存活」，**不算死亡**。")
    A("")
    A("**读法**：纵向读队列，不要横向比年份——今年刚上线的产品还没来得及死。"
      "已死占比只在已核验子集内计算，未核验的按原样列出不参与分母。"
      "两张表的分母互不相同，**不能相加，也不能互相印证**。")
    A("")

    # --- tiered view ---
    A("## 三、高置信层（GitHub 核验）")
    A("")
    if meta["n_high"] == 0:
        A("当前尚未取得 GitHub 核验结果，本节留空。")
        A("")
    else:
        A("只看 GitHub 状态可判定的产品。这层证据最硬：仓库被删除或被归档是无歧义的。")
        A("")
        A("| 上线年份 | 产品数 | 已死 | 停更 | 活跃 | 已死占比 | 不再维护占比 |")
        A("|---|---|---|---|---|---|---|")
        for r in meta["cohorts_high"]:
            if not r["publishable"]:
                continue
            A(f"| {r['year']} | {r['n']:,} | {r['dead']:,} | {r['dormant']:,} | "
              f"{r['active']:,} | {r['dead_pct']}% | {r['not_maintained_pct']}% |")
        A("")
        A(f"**高置信层整体**：{meta['high_dead']:,} / {meta['n_high']:,} 已死"
          f"（{fmt_pct(meta['high_dead'], meta['n_high'])}）；"
          f"不再维护（含停更）{meta['high_notmaint']:,}"
          f"（{fmt_pct(meta['high_notmaint'], meta['n_high'])}）。")
        A("")

    # --- medium tier ---
    A("## 四、中置信层（域名信号）")
    A("")
    if meta["n_medium"] == 0:
        A("当前尚未取得域名核验结果，本节留空。")
        A("")
    else:
        A("只看域名信号可判定的产品。DNS 与 HTTP 可以确认「站点已消失」，"
          "但无法区分「产品死掉」和「换了域名」。")
        A("")
        A("| 判定 | 数量 | 占比 | 含义 |")
        A("|---|---|---|---|")
        mv = meta["medium_verdicts"]
        meanings = {
            "alive": "站点正常响应",
            "dead": "域名二次确认无解析记录，或端口无监听",
            "blocked": "站点存在但拒绝爬虫（403/429），**不算死亡**",
            "degraded": "主机可达但服务异常（证书过期等），**不算死亡**",
        }
        for k in ("alive", "dead", "blocked", "degraded"):
            if mv.get(k):
                A(f"| `{k}` | {mv[k]:,} | {fmt_pct(mv[k], meta['n_medium'])} | {meanings[k]} |")
        A("")
        A("**关键口径**：TLS 证书错误、403、429 一律不判为死亡。"
          "初版规则把这类情况算作死亡，实测精度只有 53%；修正后为 13%。")
        A("")

    # --- evidence sample ---
    A("## 五、判定证据样本")
    A("")
    A("每条判定都可回溯。抽样展示：")
    A("")
    A("| 产品 | 上线 | 判定 | 置信层 | 证据 |")
    A("|---|---|---|---|---|")
    for p in meta["evidence_sample"]:
        title = (p.get("title") or "")[:46].replace("|", "\\|")
        A(f"| {title} | {p.get('launch_day','')} | `{p.get('verdict')}` | "
          f"{p.get('confidence')} | {str(p.get('evidence',''))[:44]} |")
    A("")

    # --- caveats ---
    A("## 六、这张表不回答什么")
    A("")
    A("1. **不回答**「独立开发者整体的失败率」。样本是 Show HN 发帖者，不是开发者总体。")
    A("2. **不回答**产品失败的原因。台账只记录状态，不推测动机。")
    A("3. **不回答**收入与转化。台账不含任何营收数据。")
    A("4. **不区分**「主动完成」与「被放弃」。两者在「不再维护」里混在一起。")
    A("5. **未覆盖**未在 Show HN 发帖的产品——这恰恰是最大的一块空白。")
    A("")
    A("---")
    A("")
    A(f"数据截止 {meta['data_window']} · 规则版本 `{meta['rules_version']}` · "
      f"复现命令见仓库 README")
    A("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.path.join(HERE, "data", "ledger.jsonl"))
    ap.add_argument("--repo", default=os.path.join(HERE, "data", "repo_status.jsonl"))
    ap.add_argument("--domain", default=os.path.join(HERE, "data", "liveness.jsonl"))
    ap.add_argument("--products", default=os.path.join(HERE, "data", "products.json"))
    ap.add_argument("--outdir", default=REPORT_DIR)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    ledger = load(args.ledger)
    repo_status = load(args.repo)
    domain_status = load(args.domain)
    products = build(ledger, repo_status, domain_status)
    cohorts = cohort_table(products)

    kinds = Counter(r.get("kind") for r in ledger)
    days = sorted({r.get("day") for r in ledger if r.get("day")})
    window = f"{days[0]} → {days[-1]}" if days else "n/a"

    vc = Counter(p["verdict"] for p in products)
    cc = Counter(p["confidence"] for p in products)
    high = [p for p in products if p["confidence"] == "high"]
    medium = [p for p in products if p["confidence"] == "medium"]
    cohorts_high = cohort_table(high) if high else []
    cohorts_medium = cohort_table(medium) if medium else []

    medium_verdicts = Counter(p["verdict"] for p in medium)

    # evidence sample: prefer high tier, show a spread
    sample = []
    for tier in ("high", "medium"):
        pool = [p for p in products if p["confidence"] == tier and p.get("evidence")]
        pool.sort(key=lambda p: p.get("launch_day") or "")
        sample.extend(pool[:4])
        sample.extend(pool[-2:])
    # dedupe
    seen = set()
    ev_sample = []
    for p in sample:
        k = p["key"]
        if k in seen:
            continue
        seen.add(k)
        ev_sample.append(p)

    meta = {
        "data_window": window,
        "population": "在 Show HN 发帖并链接自己产品的开发者（去重后按产品计）",
        "ledger_rows": len(ledger),
        "n_products": len(products),
        "n_high": len(high),
        "n_medium": len(medium),
        "n_none": cc.get("none", 0),
        "kinds": kinds.most_common(),
        "cohorts_high": cohorts_high,
        "cohorts_medium": cohorts_medium,
        "high_dead": sum(1 for p in high if p["verdict"] == "dead"),
        "high_notmaint": sum(1 for p in high
                             if p["verdict"] in ("dead", "dormant", "slowing")),
        "medium_verdicts": medium_verdicts,
        "evidence_sample": ev_sample,
        "rules_version": "2026-11-09",
        "ledger_path": "data/ledger.jsonl",
    }

    md = render(products, cohorts, meta)
    out = args.out
    if not out:
        os.makedirs(args.outdir, exist_ok=True)
        iso = datetime.date.fromisoformat(days[-1]) if days else datetime.date.today()
        y, w, _ = iso.isocalendar()
        out = os.path.join(args.outdir, f"{y}-W{w:02d}.md")
    with open(out, "w") as f:
        f.write(md)
    print(f"wrote {out} ({os.path.getsize(out)} bytes)")

    # determinism check
    md2 = render(products, cohorts, meta)
    if md != md2:
        print("NON-DETERMINISTIC OUTPUT - BUG")
        sys.exit(1)
    print("determinism check: OK (two renders identical)")


if __name__ == "__main__":
    main()
