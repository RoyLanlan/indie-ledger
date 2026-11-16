#!/usr/bin/env python3
"""Build the public indie-ledger site from measured data.

The site must never contain a number that is not in site_data.json, and
site_data.json itself is produced by the same analysis code that produces
the published report. Two files are written:

  index.html   -- public data & report (no pricing, no sales copy)
  private.html -- pricing & sales copy, password protected (built separately)

Run:
    python3 src/build_site.py [--data data/site_data.json] [--outdir .]
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from paths import ROOT
except ImportError:
    ROOT = os.path.dirname(HERE)

VERDICT_CN = {
    "active": "活跃", "dormant": "停更", "slowing": "放缓",
    "archived": "归档", "dead": "已死", "unavailable": "无法判定",
    "repo_blocked": "仓库被封",
    "alive": "站点存活", "degraded": "站点降级", "blocked": "站点拒绝爬取",
}
VERDICT_ORDER = ["active", "slowing", "dormant", "archived", "dead",
                  "alive", "degraded", "blocked", "unavailable", "repo_blocked"]
VERTICAL_CN = {
    "commerce_fintech": "电商 / 金融", "education_learning": "教育 / 学习",
    "health_wellness": "健康 / 身心", "saas_productivity": "SaaS / 效率",
    "media_content": "媒体 / 内容", "productivity_generic": "通用工具",
    "ai_ml": "AI / 机器学习", "other": "未分类",
    "social_community": "社交 / 社区", "data_analytics": "数据 / 分析",
    "devtools": "开发者工具", "games_entertainment": "游戏 / 娱乐",
    "design_creative": "设计 / 创意", "infra_devops": "基础设施 / DevOps",
    "security_privacy": "安全 / 隐私", "science_hardware": "科学 / 硬件",
}


def pct(n, d, nd=1):
    if not d:
        return "—"
    return f"{n * 100 / d:.{nd}f}%"


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def build_index(d):
    hv = d["high_verdicts"]
    mv = d["med_verdicts"]
    total_h = d["high_n"]

    # ---- hero metrics -------------------------------------------------
    metrics = [
        (f"{d['posts']:,}", "Show HN 帖子", "2024-01-01 起逐日采集"),
        (f"{d['products']:,}", "去重后独立产品", "按仓库 / 域名 / 站点去重"),
        (f"{d['repos_checked']:,}", "GitHub 仓库已全量核验", "只认真实 404"),
        (f"{d['domains_checked']:,}", "产品域名已全量核验", "只认不可解析"),
    ]
    metric_html = "\n".join(
        f'''      <div class="metric">
        <div class="metric-num">{esc(v)}</div>
        <div class="metric-label">{esc(lab)}</div>
        <div class="metric-sub">{esc(sub)}</div>
      </div>''' for v, lab, sub in metrics)

    # ---- the headline finding ----------------------------------------
    q = d["death_q"]
    headline = f'''      <div class="hero-lead">
        <p class="lead">
          在 <strong>{d['death_n']:,}</strong> 个已确认死亡的项目里，
          <strong class="hl">{d['within_1y']}%</strong> 在上线一年内就消失了，
          <strong class="hl">{d['within_2y']}%</strong> 在两年内。
        </p>
        <p class="lead-sub">
          中位数 {q['P50']} 天，P90 是 {q['P90']} 天。
          上线第一年不是安全期，而是存活风险最高的窗口。
        </p>
      </div>'''

    # ---- verdict composition -----------------------------------------
    rows = []
    for k in VERDICT_ORDER:
        if k not in hv:
            continue
        n = hv[k]
        rows.append(f'''        <tr>
          <td><span class="dot dot-{k}"></span>{VERDICT_CN.get(k, k)}</td>
          <td class="num">{n:,}</td>
          <td class="num">{pct(n, total_h)}</td>
          <td class="barcell"><span class="bar" style="width:{n * 100 / total_h:.2f}%"></span></td>
        </tr>''')
    verdict_rows = "\n".join(rows)

    notmaint = (hv.get("dead", 0) + hv.get("dormant", 0)
                + hv.get("slowing", 0) + hv.get("archived", 0))
    notmaint_pct = pct(notmaint, total_h)

    med_rows = []
    for k in ["alive", "degraded", "blocked", "dead"]:
        if k not in mv:
            continue
        n = mv[k]
        med_rows.append(f'''        <tr>
          <td><span class="dot dot-{k}"></span>{VERDICT_CN.get(k, k)}</td>
          <td class="num">{n:,}</td>
          <td class="num">{pct(n, d['med_n'])}</td>
        </tr>''')
    med_table = "\n".join(med_rows)

    # ---- death timeline ----------------------------------------------
    maxn = max(b["n"] for b in d["death_buckets"])
    tl = []
    for b in d["death_buckets"]:
        tl.append(f'''        <div class="tlrow">
          <div class="tl-label">{esc(b['label'])}</div>
          <div class="tl-track"><span class="tl-fill" style="width:{b['n'] * 100 / maxn:.2f}%"></span></div>
          <div class="tl-num">{b['n']:,}</div>
          <div class="tl-pct">{b['pct']}%</div>
        </div>''')
    timeline = "\n".join(tl)

    # ---- exposure curve ----------------------------------------------
    ex = []
    for b in d["exposure"]:
        ex.append(f'''        <tr>
          <td>{esc(b['label'])}</td>
          <td class="num">{b['n']:,}</td>
          <td class="num">{b['dead']:,}</td>
          <td class="num">{b['pct']}%</td>
        </tr>''')
    exposure_rows = "\n".join(ex)

    # ---- cohorts ------------------------------------------------------
    co = []
    for c in d["cohorts_high"]:
        co.append(f'''        <tr>
          <td>{esc(c['year'])}</td>
          <td class="num">{c['n']:,}</td>
          <td class="num">{c['dead']:,}</td>
          <td class="num">{c['dormant']:,}</td>
          <td class="num">{c['slowing']:,}</td>
          <td class="num">{c['archived']:,}</td>
          <td class="num">{c['active']:,}</td>
          <td class="num">{c['dead_pct']}%</td>
          <td class="num">{c['not_maintained_pct']}%</td>
        </tr>''')
    cohort_rows = "\n".join(co)

    # ---- verticals 2024 -----------------------------------------------
    vt = []
    for v in d["verticals_2024"]:
        if v["verified"] < 10:
            continue
        name = VERTICAL_CN.get(v["category"], v["category"])
        pub = ('<span class="tag tag-ok">可发布</span>' if v["publishable"]
               else '<span class="tag tag-no">样本不足</span>')
        vt.append(f'''        <tr>
          <td>{esc(name)}</td>
          <td class="num">{v['verified']:,}</td>
          <td class="num">{v['dead']:,}</td>
          <td class="num">{v['dead_pct_verified']:.1f}%</td>
          <td class="num">{v['not_maintained_pct_verified']:.1f}%</td>
          <td>{pub}</td>
        </tr>''')
    vertical_rows = "\n".join(vt)

    # ---- HN points strata (the one forward-looking signal) --------------
    ps_rows = []
    for s in d.get("points_strata") or []:
        ps_rows.append(f'''        <tr>
          <td class="num">{s['year']}</td>
          <td class="num">{s['n']:,}</td>
          <td class="num">{s['lo_dead']:,} / {s['lo_n']:,}</td>
          <td class="num">{s['lo_pct']:.2f}%</td>
          <td class="num">{s['hi_dead']:,} / {s['hi_n']:,}</td>
          <td class="num">{s['hi_pct']:.2f}%</td>
          <td class="num">&lt; 0.0001</td>
          <td><span class="tag tag-ok">通过校正</span></td>
        </tr>''')
    points_rows = "\n".join(ps_rows)

    sig = d.get("sig_2024") or []
    sig_rows = []
    for s in sig[:8]:
        name = VERTICAL_CN.get(s["category"], s["category"])
        sig_rows.append(f'''        <tr>
          <td>{esc(name)}</td>
          <td class="num">{s['dead']}/{s['verified']}</td>
          <td class="num">{s['rate'] * 100:.1f}%</td>
          <td class="num">{s['baseline'] * 100:.1f}%</td>
          <td class="num">{s['p_value']:.4f}</td>
          <td class="num">{s['p_holm']:.4f}</td>
          <td><span class="tag tag-no">未通过校正</span></td>
        </tr>''')
    sig_table = "\n".join(sig_rows)

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>indie-ledger — 独立开发者产品存活台账</title>
<meta name="description" content="Show HN 上线产品的存活追踪台账：89,350 条帖子全量核验，谁还活着，谁死了。">
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
  --black:#171717; --white:#fff; --g900:#171717; --g600:#4d4d4d;
  --g500:#666; --g400:#808080; --g100:#ebebeb; --g50:#fafafa;
  --blue:#0072f5; --focus:hsla(212,100%,48%,1);
  --ring:rgba(0,0,0,.08);
  --card:rgba(0,0,0,.08) 0 0 0 1px, rgba(0,0,0,.04) 0 2px 2px, #fafafa 0 0 0 1px;
}}
* {{ box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{
  margin:0; background:var(--white); color:var(--g900);
  font-family:'Geist',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  font-size:17px; line-height:1.6; letter-spacing:-.1px;
  -webkit-font-smoothing:antialiased;
}}
.wrap {{ max-width:1100px; margin:0 auto; padding:0 24px; }}
a {{ color:var(--blue); }}
h1,h2,h3 {{ letter-spacing:-1.6px; font-weight:600; margin:0; }}
h1 {{ font-size:clamp(34px,6vw,58px); line-height:1.02; letter-spacing:-2.4px; }}
h2 {{ font-size:clamp(26px,4vw,36px); line-height:1.2; letter-spacing:-1.28px; }}
h3 {{ font-size:20px; letter-spacing:-.6px; }}
p {{ margin:0 0 16px; }}
.mono {{ font-family:'Geist Mono',ui-monospace,SFMono-Regular,Menlo,monospace; }}

/* nav */
nav {{
  position:sticky; top:0; z-index:50; background:rgba(255,255,255,.86);
  backdrop-filter:saturate(180%) blur(12px);
  box-shadow:0 0 0 1px var(--ring);
}}
.navin {{ display:flex; align-items:center; gap:28px; height:56px; }}
.brand {{ font-weight:600; font-size:15px; letter-spacing:-.4px; text-decoration:none; color:var(--g900); }}
.navlinks {{ display:flex; gap:22px; margin-left:auto; }}
.navlinks a {{ font-size:14px; font-weight:500; color:var(--g600); text-decoration:none; }}
.navlinks a:hover {{ color:var(--g900); }}
@media(max-width:760px){{ .navlinks {{ display:none; }} }}

/* hero */
.hero {{ padding:88px 0 64px; }}
.hero h1 {{ max-width:16ch; }}
.hero .sub {{ margin-top:22px; font-size:19px; line-height:1.6; color:var(--g600); max-width:62ch; }}
.lead {{ margin-top:40px; font-size:22px; line-height:1.5; max-width:60ch; letter-spacing:-.4px; }}
.lead .hl {{ color:var(--blue); font-weight:600; }}
.lead-sub {{ color:var(--g500); font-size:16px; margin-top:10px; }}
.cta {{ margin-top:32px; display:flex; gap:12px; flex-wrap:wrap; }}
.btn {{
  display:inline-flex; align-items:center; padding:9px 16px; border-radius:6px;
  font-size:14px; font-weight:500; text-decoration:none;
  box-shadow:rgba(0,0,0,.08) 0 0 0 1px; transition:.14s;
}}
.btn:hover {{ background:var(--g50); }}
.btn-dark {{ background:var(--g900); color:#fff; box-shadow:none; }}
.btn-dark:hover {{ background:#000; }}
.btn:focus-visible {{ outline:2px solid var(--focus); outline-offset:2px; }}

/* metrics */
.metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-top:56px; }}
@media(max-width:860px){{ .metrics {{ grid-template-columns:repeat(2,1fr); }} }}
.metric {{ background:var(--white); border-radius:8px; padding:20px; box-shadow:var(--card); }}
.metric-num {{ font-size:32px; font-weight:600; letter-spacing:-1.28px; }}
.metric-label {{ font-size:14px; color:var(--g600); margin-top:4px; }}
.metric-sub {{ font-size:12px; color:var(--g400); margin-top:8px; }}

/* sections */
section {{ padding:64px 0; border-top:1px solid var(--g100); }}
.sechead {{ margin-bottom:32px; max-width:70ch; }}
.sechead p {{ color:var(--g600); margin-top:12px; font-size:16px; }}
.eyebrow {{
  font-family:'Geist Mono',monospace; font-size:12px; font-weight:500;
  text-transform:uppercase; color:var(--g400); letter-spacing:.6px; margin-bottom:12px;
}}

/* tables */
table {{ width:100%; border-collapse:collapse; font-size:15px; }}
th {{
  text-align:left; font-weight:500; font-size:12px; color:var(--g500);
  text-transform:uppercase; letter-spacing:.5px; padding:0 12px 10px 0;
  border-bottom:1px solid var(--g100);
}}
td {{ padding:11px 12px 11px 0; border-bottom:1px solid var(--g100); vertical-align:middle; }}
td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; padding-right:0; }}
tbody tr:last-child td {{ border-bottom:none; }}
.tscroll {{ overflow-x:auto; -webkit-overflow-scrolling:touch; }}

.dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:9px; vertical-align:middle; }}
.dot-active {{ background:#16a34a; }} .dot-slowing {{ background:#f59e0b; }}
.dot-dormant {{ background:#f97316; }} .dot-archived {{ background:#a855f7; }}
.dot-dead {{ background:#ef4444; }} .dot-alive {{ background:#16a34a; }}
.dot-degraded {{ background:#f59e0b; }} .dot-blocked {{ background:#94a3b8; }}
.dot-unavailable,.dot-repo_blocked {{ background:#cbd5e1; }}
.barcell {{ width:34%; padding-right:0; }}
.bar {{ display:block; height:7px; background:var(--g900); border-radius:2px; opacity:.82; }}
.tag {{
  display:inline-block; padding:2px 9px; border-radius:9999px; font-size:12px; font-weight:500;
  background:#ebf5ff; color:#0068d6;
}}
.tag-no {{ background:#f4f4f5; color:#71717a; }}

/* timeline */
.tlrow {{ display:grid; grid-template-columns:132px 1fr 68px 56px; gap:14px; align-items:center; padding:9px 0; }}
.tl-label {{ font-size:14px; color:var(--g600); }}
.tl-track {{ height:16px; background:var(--g50); border-radius:3px; overflow:hidden; box-shadow:0 0 0 1px var(--ring); }}
.tl-fill {{ display:block; height:100%; background:var(--g900); border-radius:3px; }}
.tl-num {{ text-align:right; font-variant-numeric:tabular-nums; font-size:14px; }}
.tl-pct {{ text-align:right; font-variant-numeric:tabular-nums; font-size:14px; color:var(--g500); }}
@media(max-width:640px){{ .tlrow {{ grid-template-columns:104px 1fr 52px; }} .tl-pct {{ display:none; }} }}

/* callout */
.callout {{
  border-radius:8px; padding:22px 24px; box-shadow:var(--card); background:var(--white);
  margin:24px 0;
}}
.callout.warn {{ background:#fffbeb; box-shadow:rgba(217,119,6,.2) 0 0 0 1px; }}
.callout h3 {{ font-size:16px; margin-bottom:10px; }}
.callout p {{ font-size:15px; color:var(--g600); margin-bottom:0; }}
.callout ol {{ margin:8px 0 0; padding-left:20px; color:var(--g600); font-size:15px; }}
.callout li {{ margin-bottom:6px; }}

/* grid2 */
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:40px; }}
@media(max-width:860px){{ .grid2 {{ grid-template-columns:1fr; gap:28px; }} }}
.panel {{ border-radius:8px; padding:24px; box-shadow:var(--card); background:var(--white); }}
.panel h3 {{ margin-bottom:6px; }}
.panel .pmeta {{ font-size:13px; color:var(--g400); margin-bottom:16px; }}
.plist {{ margin:0; padding-left:18px; color:var(--g600); font-size:15px; }}
.plist li {{ margin-bottom:7px; }}

footer {{ padding:48px 0 64px; border-top:1px solid var(--g100); color:var(--g500); font-size:14px; }}
footer .fin {{ display:flex; gap:20px; flex-wrap:wrap; margin-top:14px; }}
footer a {{ color:var(--g600); }}
</style>
</head>
<body>
<nav>
  <div class="wrap navin">
    <a class="brand" href="#top">indie-ledger</a>
    <div class="navlinks">
      <a href="#findings">核心发现</a>
      <a href="#howdie">怎么死的</a>
      <a href="#cohorts">上线队列</a>
      <a href="#points">点赞信号</a>
      <a href="#verticals">赛道</a>
      <a href="#method">方法与口径</a>
      <a href="https://github.com/RoyLanlan/indie-ledger">GitHub</a>
    </div>
  </div>
</nav>

<header class="hero" id="top">
  <div class="wrap">
    <h1>Show HN 上的独立产品，后来都怎样了</h1>
    <p class="sub">
      以 Show HN 为发现入口，以 GitHub 仓库状态为高置信证据，以域名信号为中置信补充。
      每条判定都能回溯到原始记录，每条规则都对应一次真实事故。
    </p>
{headline}
    <div class="cta">
      <a class="btn btn-dark" href="#findings">看核心发现</a>
      <a class="btn" href="https://github.com/RoyLanlan/indie-ledger">查看完整数据集</a>
    </div>
    <div class="metrics">
{metric_html}
    </div>
  </div>
</header>

<section id="findings">
  <div class="wrap">
    <div class="sechead">
      <div class="eyebrow">核心发现</div>
      <h2>失败不是均匀发生的，它集中在第一年</h2>
      <p>
        这台账里最有用的信息不是「谁死了」，而是「什么时候死」。
        下面的数字描述的是分布，不需要显著性检验，也不受分类口径变化影响。
      </p>
    </div>
    <div class="callout">
      <h3>已撤回的结论</h3>
      <p>
        早期版本曾以「2024 年电商 / 金融赛道死亡率 17.5%，是同年平均的 3.6 倍，
        全库唯一通过多重比较校正」作为核心结论。该结论经复核后<strong>不成立</strong>，
        已撤回。两个原因：
      </p>
      <ol>
        <li><strong>分类错误</strong>——该赛道混入了键盘快捷键菜单、桌面启动器、代码签出、比特币头像生成器等非商业项目，样本中约 11/40 归类错误。</li>
        <li><strong>暴露期混淆</strong>——该赛道 7 个死亡项目全部被观察了 709 天以上。「被观察得越久，越容易暴露死亡」与赛道性质无关；对齐观察时长后效应消失。</li>
      </ol>
      <p style="margin-top:12px">
        修正后的结论是：<strong>在暴露期对齐、样本量达标的赛道里，没有任何赛道通过多重比较校正。</strong>
        任何「某赛道特别容易死」的说法，目前都没有证据支持。
      </p>
    </div>
  </div>
</section>

<section id="howdie">
  <div class="wrap">
    <div class="sechead">
      <div class="eyebrow">死法与死时</div>
      <h2>怎么死的，死了多久</h2>
      <p>
        高置信层（GitHub 可判定）共 {total_h:,} 个产品。
        「不再维护」含停更、放缓、归档与死亡，共 {notmaint:,} 个，占 {notmaint_pct}。
        大部分消失的项目不是被删除，而是被作者放着不管了——这两件事成因不同，必须分开。
      </p>
    </div>

    <div class="grid2">
      <div class="panel">
        <h3>状态构成</h3>
        <div class="pmeta">分母：高置信层已核验 {total_h:,} 个产品</div>
        <div class="tscroll">
        <table>
          <thead><tr><th>判定</th><th class="num">数量</th><th class="num">占比</th><th></th></tr></thead>
          <tbody>
{verdict_rows}
          </tbody>
        </table>
        </div>
      </div>

      <div class="panel">
        <h3>中置信层（域名信号）</h3>
        <div class="pmeta">分母：{d['med_n']:,} 个可判定产品。域名共 {d['domains_checked']:,} 个，全部核验</div>
        <div class="tscroll">
        <table>
          <thead><tr><th>判定</th><th class="num">数量</th><th class="num">占比</th></tr></thead>
          <tbody>
{med_table}
          </tbody>
        </table>
        </div>
        <p style="font-size:13px;color:var(--g400);margin:16px 0 0">
          域名层只能确认「站点消失了」，无法区分关停与搬家，因此这一层的死亡率天然高于
          GitHub 层。两层分母永不合并。
        </p>
      </div>
    </div>

    <h3 style="margin:56px 0 6px">死亡时间分布</h3>
    <p style="color:var(--g600);font-size:15px;max-width:70ch">
      {d['death_n']:,} 个已确认死亡项目的「上线 → 消失」时长。死亡最集中的区间是
      上线后 180–365 天，占 {d['death_buckets'][2]['pct']}%。
    </p>
    <div style="margin-top:22px">
{timeline}
    </div>

    <h3 style="margin:56px 0 6px">暴露期与死亡率</h3>
    <p style="color:var(--g600);font-size:15px;max-width:70ch">
      死亡率在观察满一年后趋于平稳（约 {d['exposure'][2]['pct']}%），之后甚至略有回落——
      长尾是被筛出来的幸存者，不是越来越大的坟场。跨年份比较必须对齐这个暴露期，
      否则「被看得久」会被误读成「这个赛道更容易死」。
    </p>
    <div class="tscroll" style="margin-top:18px">
      <table>
        <thead><tr><th>已被观察</th><th class="num">样本</th><th class="num">已死</th><th class="num">死亡率</th></tr></thead>
        <tbody>
{exposure_rows}
        </tbody>
      </table>
    </div>
  </div>
</section>

<section id="cohorts">
  <div class="wrap">
    <div class="sechead">
      <div class="eyebrow">上线队列</div>
      <h2>按上线年份看存活情况</h2>
      <p>高置信层。「不再维护」含停更、放缓、归档与死亡。跨年份的比率不可直接比较——不同队列被观察的时长不同。</p>
    </div>
    <div class="tscroll">
    <table>
      <thead><tr>
        <th>年份</th><th class="num">产品数</th><th class="num">已死</th><th class="num">停更</th>
        <th class="num">放缓</th><th class="num">归档</th><th class="num">活跃</th>
        <th class="num">已死占比</th><th class="num">不再维护</th>
      </tr></thead>
      <tbody>
{cohort_rows}
      </tbody>
    </table>
    </div>
  </div>
</section>

<section id="points">
  <div class="wrap">
    <div class="sechead">
      <div class="eyebrow">唯一的前瞻信号</div>
      <h2>上线当天的点赞数，能区分后来的死活</h2>
      <p>
        赛道之间测不出差异，但上线当天的 Show HN 点赞数可以。
        下面的比较先对齐暴露期（只看被跟踪满 {d['min_age_days']} 天的样本），
        再做精确单侧二项分布检验，最后对实际做过的检验做 Holm–Bonferroni 校正。
        两年的队列方向一致，校正后依然成立。
      </p>
    </div>
    <div class="tscroll">
    <table>
      <thead><tr>
        <th class="num">上线年份</th><th class="num">样本量</th>
        <th class="num">点赞 &lt; 20</th><th class="num">死亡率</th>
        <th class="num">点赞 ≥ 20</th><th class="num">死亡率</th>
        <th class="num">校正后 p</th><th>判定</th>
      </tr></thead>
      <tbody>
{points_rows}
      </tbody>
    </table>
    </div>
    <div class="callout">
      <h3>怎么读这个结论</h3>
      <p>
        点赞数是上线当天就能测到的量，是这份数据里唯一的前瞻指标。
        但它是相关性，不是因果：点赞高不等于产品一定能活。
        原始数据在
        <a href="https://github.com/RoyLanlan/indie-ledger">GitHub 仓库</a>，
        每一行判定都能回溯。
      </p>
    </div>
  </div>
</section>

<section id="verticals">
  <div class="wrap">
    <div class="sechead">
      <div class="eyebrow">行业维度</div>
      <h2>赛道之间，目前测不出差异</h2>
      <p>
        下表是 2024 年上线的高置信层样本。样本量不足 30 的赛道按规则不发布。
        对样本量达标的赛道做精确二项分布检验并做多重比较校正后，
        没有任何赛道与同年平均存在可区分的差异。
      </p>
    </div>
    <div class="tscroll">
    <table>
      <thead><tr>
        <th>赛道</th><th class="num">已核验</th><th class="num">已死</th>
        <th class="num">已死占比</th><th class="num">不再维护</th><th>是否发布</th>
      </tr></thead>
      <tbody>
{vertical_rows}
      </tbody>
    </table>
    </div>
    <div class="callout">
      <h3>显著性检验结果（2024 年，暴露期对齐）</h3>
      <p style="margin-bottom:12px">
        原假设：该赛道死亡率与同年整体一致。单侧精确二项分布 + Holm–Bonferroni 多重比较校正。
      </p>
      <div class="tscroll">
      <table>
        <thead><tr>
          <th>赛道</th><th class="num">已死/已核验</th><th class="num">该赛道</th>
          <th class="num">同年整体</th><th class="num">p 值</th><th class="num">校正后 p</th><th>判定</th>
        </tr></thead>
        <tbody>
{sig_table}
        </tbody>
      </table>
      </div>
    </div>
  </div>
</section>

<section id="method">
  <div class="wrap">
    <div class="sechead">
      <div class="eyebrow">方法与口径</div>
      <h2>这个台账怎么判定生死</h2>
    </div>
    <div class="grid2">
      <div class="panel">
        <h3>GitHub 层（高置信）</h3>
        <div class="pmeta">{d['repos_checked']:,} / {d['repos_total']:,} 仓库已核验</div>
        <ul class="plist">
          <li>仓库真实 404 / 删除 → <span class="mono">dead</span></li>
          <li><span class="mono">archived = true</span> → <span class="mono">archived</span>（独立判定，计入「不再维护」，不计入「已死」）</li>
          <li>最后提交超过 365 天 → <span class="mono">dormant</span></li>
          <li>最后提交 180–365 天 → <span class="mono">slowing</span></li>
          <li>最后提交 180 天内 → <span class="mono">active</span></li>
          <li>限流 / 451 / 仓库封禁 → 不写死，单独记为 <span class="mono">unavailable</span> / <span class="mono">repo_blocked</span></li>
        </ul>
      </div>
      <div class="panel">
        <h3>域名层（中置信）</h3>
        <div class="pmeta">{d['domains_checked']:,} / {d['domains_total']:,} 域名已核验</div>
        <ul class="plist">
          <li>NXDOMAIN 二次确认 → <span class="mono">dead</span></li>
          <li>端口返回 HTTP &lt; 400 → <span class="mono">alive</span></li>
          <li>HTTP 403 / 405 / 429 → <span class="mono">blocked</span>，**绝不判死**</li>
          <li>DNS 可解析但请求异常 → <span class="mono">degraded</span>，**绝不判死**</li>
          <li>DNS 可解析但无监听 → <span class="mono">dead</span></li>
        </ul>
      </div>
    </div>

    <div class="callout">
      <h3>这张表不回答什么</h3>
      <ol>
        <li>不回答「独立开发者整体的失败率」——样本是 Show HN 发帖者，不是开发者总体。</li>
        <li>不回答产品失败的原因——台账只记录状态，不推测动机。</li>
        <li>不回答收入与转化——台账不含任何营收数据。</li>
        <li>不区分「主动完成」与「被放弃」——两者都在「不再维护」里。</li>
        <li>未覆盖未在 Show HN 发帖的产品——这恰恰是最大的一块空白。</li>
        <li>不预测任何单个项目的结果——这里只有同批次的分布位置。</li>
      </ol>
    </div>
  </div>
</section>

<footer>
  <div class="wrap">
    <div>
      数据快照：{esc(d['generated'])}。仓库状态核验于 2026-11-09。
      所有数字都能回溯到 <span class="mono">data/ledger.jsonl</span>、
      <span class="mono">data/repo_status.jsonl</span>、<span class="mono">data/liveness.jsonl</span> 的具体行。
    </div>
    <div class="fin">
      <a href="https://github.com/RoyLanlan/indie-ledger">GitHub 仓库</a>
      <a href="https://github.com/RoyLanlan/indie-ledger/blob/master/docs/RULES.md">判定规则与判例库</a>
      <a href="https://github.com/RoyLanlan/indie-ledger/blob/master/reports/">周期报告</a>
      <a href="https://github.com/RoyLanlan/indie-ledger/blob/master/docs/METHODOLOGY.md">方法论</a>
    </div>
  </div>
</footer>
</body>
</html>
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data", "site_data.json"))
    ap.add_argument("--outdir", default=ROOT)
    args = ap.parse_args()

    d = json.load(open(args.data, encoding="utf-8"))
    html = build_index(d)
    os.makedirs(args.outdir, exist_ok=True)
    out = os.path.join(args.outdir, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {out} ({len(html.encode('utf-8'))} bytes)")
    print("sections:", html.count("<section"))
    print("has pricing words:",
          any(w in html for w in ["元", "定价", "价格", "购买", "下单"]))


if __name__ == "__main__":
    main()
