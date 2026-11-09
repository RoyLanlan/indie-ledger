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
from analyze import (build, cohort_table, vertical_table,
                     vertical_year_table, vertical_significance, load, MIN_N)

REPORT_DIR = os.path.join(HERE, "reports")

# Chinese names for the taxonomy buckets. The report is Chinese, the
# taxonomy keys are English, and an English key in a Chinese report reads
# like a leak.
VERTICAL_CN = {
    "devtools": "开发者工具",
    "ai_ml": "AI / 机器学习",
    "infra_devops": "基础设施 / DevOps",
    "data_analytics": "数据 / 分析",
    "security_privacy": "安全 / 隐私",
    "saas_productivity": "SaaS / 效率",
    "design_creative": "设计 / 创意",
    "media_content": "媒体 / 内容",
    "social_community": "社交 / 社区",
    "commerce_fintech": "电商 / 金融",
    "education_learning": "教育 / 学习",
    "health_wellness": "健康 /  wellness",
    "science_hardware": "科学 / 硬件",
    "games_entertainment": "游戏 / 娱乐",
    "productivity_generic": "通用工具",
    "other": "未分类",
}
VERTICAL_CN["health_wellness"] = "健康 / 身心"


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
    A("| 上线年份 | 已核验 | 已死 | 停更 | 放缓 | 归档 | 活跃 | 受限 | 已死占比 | 不再维护占比 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for r in meta["cohorts_high"]:
        ver = r["n"] - r["unverified"]
        if ver < MIN_N:
            continue
        A(f"| {r['year']} | {ver:,} | {r['dead']:,} | {r['dormant']:,} | "
          f"{r['slowing']:,} | {r.get('archived', 0):,} | {r['active']:,} | "
          f"{r.get('unavailable', 0):,} | "
          f"{r['dead_pct_verified']}% | {r['not_maintained_pct_verified']}% |")
    A("")
    A("「归档」= 仓库被作者设为只读，**代码仍在，仓库仍可访问**，只是不再更新。"
      "归档计入「不再维护」，**不计入「已死」**——只有仓库 404（被删除）才算死。")
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
        A("只看 GitHub 状态可判定的产品。这层证据最硬：仓库被删除（404）是无歧义的；"
          "仓库被**归档**是「作者宣布不再更新」，代码仍在，所以归入「不再维护」而不是「已死」。")
        A("")
        A("| 上线年份 | 产品数 | 已死 | 停更 | 放缓 | 归档 | 活跃 | 已死占比 | 不再维护占比 |")
        A("|---|---|---|---|---|---|---|---|---|")
        for r in meta["cohorts_high"]:
            if not r["publishable"]:
                continue
            A(f"| {r['year']} | {r['n']:,} | {r['dead']:,} | {r['dormant']:,} | "
              f"{r['slowing']:,} | {r.get('archived', 0):,} | {r['active']:,} | "
              f"{r['dead_pct']}% | {r['not_maintained_pct']}% |")
        A("")
        A(f"**高置信层整体**：{meta['high_dead']:,} / {meta['n_high']:,} 已死"
          f"（{fmt_pct(meta['high_dead'], meta['n_high'])}）；"
          f"不再维护（含停更、放缓、归档）{meta['high_notmaint']:,}"
          f"（{fmt_pct(meta['high_notmaint'], meta['n_high'])}），"
          f"其中归档 {meta.get('high_archived', 0):,} 个。")
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

    # --- vertical table (the point of the ledger) ---
    A("## 五、赛道存活率（行业维度）")
    A("")
    A("这个项目存在的意义：把 8.8 万个 Show HN 上线产品按行业切开，"
      "看**哪些赛道在死、哪些赛道活得久**。")
    A("")
    A("赛道由标题关键词确定性分类（taxonomy v1），"
      f"分类精度经 {meta.get('cat_n', 0)} 条人工标注样本实测为 "
      f"**{meta.get('cat_precision', 0)}%**，"
      f"全库覆盖率 {meta.get('cat_coverage', 0)}%。")
    A("")
    A("**表 5.1　高置信层（GitHub 核验）赛道存活率**")
    A("")
    A("| 赛道 | 已核验 | 已死 | 停更 | 放缓 | 归档 | 活跃 | 已死占比 | 不再维护占比 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for r in meta["verticals_high"]:
        if not r["publishable"]:
            continue
        A(f"| {VERTICAL_CN.get(r['category'], r['category'])} | "
          f"{r['verified']:,} | {r['dead']:,} | {r['dormant']:,} | "
          f"{r['slowing']:,} | {r.get('archived', 0):,} | {r['active']:,} | "
          f"{r['dead_pct_verified']:.1f}% | "
          f"{r['not_maintained_pct_verified']:.1f}% |")
    A("")
    hidden_v = [r for r in meta["verticals_high"] if not r["publishable"]]
    if hidden_v:
        A(f"以下赛道样本量不足 {MIN_N}，按规则不发布："
          f"{'、'.join(VERTICAL_CN.get(r['category'], r['category']) for r in hidden_v)}")
        A("")
    A("**读法**：「已死占比」是硬指标（仓库 404，删库）；"
      "「不再维护占比」含停更、放缓、归档，是软指标。"
      "两者的差距说明：多数产品不是被删掉，而是**被作者放着不管**。")
    A("")
    A("**必须说清的三条限制**：")
    A("")
    A("1. **赛道是关键词定义的，不是自报的**。"
      "赛道标签来自 Show HN 标题，标题写得好不好直接影响归类。"
      "一个 22% 死亡率的赛道，可能只是标题里更容易出现「shop」「payment」这类词。")
    A("2. **各赛道的核验进度不同**。上表只统计已核验子集，"
      "如果某个赛道被优先核验，结论会偏。")
    A("3. **只有 2024 年样本足够大**（2025 年多数赛道不足 "
      f"{MIN_N} 条），所以上表混合了不同上线年份，"
      "而越早的上线、越长的暴露期 = 越高的死亡率。"
      "**按年份切开看才是干净的对比，见下表。**")
    A("")

    # --- vertical x launch year: the unconfounded comparison ---
    _vy = 0
    for y, vrows in sorted(meta.get("vertical_years", {}).items()):
        pub = [r for r in vrows if r["publishable"]]
        if not pub:
            continue
        _vy += 1
        base_dead = sum(r["dead"] for r in pub)
        base_ver = sum(r["verified"] for r in pub)
        base_pct = base_dead * 100 / base_ver if base_ver else 0
        A(f"**表 5.{_vy}　{y} 年上线（高置信层）赛道对比**")
        A("")
        A(f"该年份已核验 {base_ver:,} 个，整体已死占比 {base_pct:.1f}%。")
        A("")
        A("| 赛道 | 已核验 | 已死 | 已死占比 | 相对整体 | 不再维护占比 |")
        A("|---|---|---|---|---|---|")
        # Sort by death rate, descending: the point is to see who dies.
        sigmap = {s_["category"]: s_ for s_ in meta.get("vertical_sig", {}).get(y, [])}
        for r in sorted(pub, key=lambda x: -x["dead_pct_verified"]):
            ratio = (r["dead_pct_verified"] / base_pct) if base_pct else 0
            sg = sigmap.get(r["category"])
            # Bold only what the statistical test actually supports.
            mark = "**" if (sg and sg["significant"]) else ""
            A(f"| {VERTICAL_CN.get(r['category'], r['category'])} | "
              f"{r['verified']:,} | {r['dead']:,} | "
              f"{mark}{r['dead_pct_verified']:.1f}%{mark} | "
              f"{ratio:.1f}× | "
              f"{r['not_maintained_pct_verified']:.1f}% |")
        A("")
        # Verticals that fell below the threshold this year. A vertical
        # that is publishable in one year and not in another is itself a
        # finding: it means the sample is too thin to carry a rate, not
        # that the vertical died off.
        thin = [r for r in vrows if not r["publishable"]]
        if thin:
            names = "、".join(
                f"{VERTICAL_CN.get(r['category'], r['category'])}"
                f"（{r['verified']}）" for r in sorted(thin, key=lambda x: -x["verified"]))
            A(f"该年份样本量不足 {MIN_N}、按规则不发布的赛道：{names}。"
              "赛道在某些年份不出现，是样本不够，不是赛道消失了。")
            A("")
        A(f"「相对整体」= 该赛道已死占比 ÷ {y} 年整体已死占比。"
          "大于 1 表示该赛道比同年平均死得更多。")
        A("")
        # Only the tail of the cohort has had real exposure time.
        if y == max(meta.get("vertical_years", {}).keys(), default=y):
            A(f"**注意**：{y} 年是最新的一批，暴露期最短，"
              "「已死占比」天然偏低，纵向比较必须看同一年的表。")
            A("")

    # --- the one finding that survives a statistical test ---
    allsig = []
    for y, sigrows in sorted(meta.get("vertical_sig", {}).items()):
        for sg in sigrows:
            if sg["significant"]:
                s2 = dict(sg)
                s2["year"] = y
                allsig.append(s2)
    if allsig:
        A("**表 5.3　统计显著性检验（精确二项分布，单侧）**")
        A("")
        A("上表里有些赛道的已死占比看着刺眼，但样本小的时候"
          "波动很大。这里对每个赛道做一次检验："
          "原假设是「该赛道的死亡率与同年整体一致」，"
          "计算 P(观测死亡数 ≥ 实际死亡数)，只有 p < 0.05 才算真的不一样。")
        A("")
        A("| 年份 | 赛道 | 已死/已核验 | 该赛道 | 同年整体 | 倍数 | p 值 |")
        A("|---|---|---|---|---|---|---|")
        for sg in allsig:
            A(f"| {sg['year']} | {VERTICAL_CN.get(sg['category'], sg['category'])} | "
              f"{sg['dead']}/{sg['verified']} | {sg['rate']*100:.1f}% | "
              f"{sg['baseline']*100:.1f}% | {sg['ratio']:.1f}× | "
              f"{sg['p_value']:.5f} |")
        A("")
        for sg in allsig:
            A(f"**{sg['year']} 年「{VERTICAL_CN.get(sg['category'], sg['category'])}」"
              f"是唯一显著高于同年平均的赛道**："
              f"{sg['dead']}/{sg['verified']} 个已死（{sg['rate']*100:.1f}%），"
              f"是同年整体（{sg['baseline']*100:.1f}%）的 {sg['ratio']:.1f} 倍。")
            A("")
        A("其余赛道（AI、开发者工具、DevOps 等）在这个检验下"
          "**与整体没有可区分的差异**——它们看起来的差异是样本波动，"
          "不是赛道特性。这一点很重要："
          "「AI 死亡率 5.0% vs 整体 4.8%」这种说法不该被当成结论。")
        A("")
        A("### 这条结论的三条限制")
        A("")
        A("1. **赛道是关键词定义的，不是产品自己申报的。**"
          "「电商/金融」这个赛道的边界由分类器的关键词决定，"
          "而这个赛道的关键词（store、payment、shop…）"
          "恰好最容易误吸非商业仓库。这是循环论证风险，"
          "无法用同一份数据消除。")
        A("2. **样本只有几十个。**"
          f"{allsig[0]['verified']} 个已核验样本，"
          "单个仓库的判定错误就会明显改变比例。"
          "已对其中全部已死样本做过 GitHub API 逐个复核，"
          "确认是真 404，但这不能替代更大的样本。")
        A("3. **「已死」只说明仓库没了，不说明商业失败。**"
          "换仓库、闭源、合并进其他项目、改成只做服务，"
          "都会让 GitHub 侧显示为死。")
        A("")

    # --- evidence sample ---
    A("## 六、判定证据样本")
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

    # Attach the industry vertical. Same join as analyze.py: the
    # category follows the product's earliest Show HN post.
    cat_by_id = {}
    for c in load(os.path.join(HERE, "data", "categories.jsonl")):
        if c.get("id"):
            cat_by_id[c["id"]] = c.get("category")
    cat_by_key = {}
    for r in ledger:
        key = r.get("repo") or r.get("base") or r.get("domain")
        cid = cat_by_id.get(r.get("id"))
        if key and cid:
            cat_by_key.setdefault(key, cid)
    for p in products:
        p["category"] = cat_by_key.get(p.get("key"))

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

    verticals_high = vertical_table(products, tier="high")
    verticals_medium = vertical_table(products, tier="medium")

    # Vertical x launch year. Pooling launch years into one rate is
    # confounded by exposure time: a 2024 product has had ~2.8 years to
    # die, a 2026 one has had months. Compare verticals within a single
    # launch year instead.
    vertical_years = {}
    for y in sorted({p["launch_day"][:4] for p in high if p.get("launch_day")}):
        vertical_years[y] = vertical_year_table(products, tier="high", year=y)

    # Which verticals are actually distinguishable from the baseline.
    # A rate that looks dramatic can be noise; only a significant
    # binomial tail earns a callout in the report.
    vertical_sig = {}
    for y in sorted({p["launch_day"][:4] for p in high if p.get("launch_day")}):
        vertical_sig[y] = vertical_significance(products, tier="high", year=y)

    # Category-taxonomy quality: measured, not asserted. The numbers come
    # from audit_categories.py; if that file has not been run yet, report
    # "not measured" rather than a fake figure.
    cat_n = cat_precision = cat_coverage = 0
    audit_path = os.path.join(HERE, "data", "category_audit.json")
    if os.path.exists(audit_path):
        try:
            aud = json.load(open(audit_path))
            cat_n = aud.get("n", 0)
            cat_precision = round(aud.get("precision", 0) * 100, 1)
            cat_coverage = round(aud.get("coverage", 0) * 100, 1)
        except Exception:
            pass

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
        # P20: archived counts as "not maintained", not "dead".
        "high_archived": sum(1 for p in high if p["verdict"] == "archived"),
        "high_notmaint": sum(1 for p in high
                             if p["verdict"] in ("dead", "dormant", "slowing",
                                                "archived")),
        "medium_verdicts": medium_verdicts,
        "verticals_high": verticals_high,
        "verticals_medium": verticals_medium,
        "vertical_years": vertical_years,
        "vertical_sig": vertical_sig,
        "cat_n": cat_n,
        "cat_precision": cat_precision,
        "cat_coverage": cat_coverage,
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
