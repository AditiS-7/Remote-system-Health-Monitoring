#!/usr/bin/env python3
"""
analyze_results.py - results analysis and graphs for the D2 experiments (Person 2).

Reads every results/<condition>/trial<N>/ folder written by run_experiments.py:
    agent_logs/agentN.csv   sender log   (report_id, node_id, status, ts_send, ...)
    recv_log.csv            receive log  (report_id, ts_recv)  from the tcpdump capture
    evidence.jsonl          controller / flow / queue snapshots taken during the trial

Reports are matched by report_id.  delay = ts_recv - ts_send (same VM clock).

Writes into results/analysis/ (default):
    per_trial.csv     one row per trial
    summary.csv       one row per condition (mean and sample SD of the per-trial values)
    critical_delay.svg, critical_vs_normal.svg, rule_timing.svg   (plus .png if matplotlib is installed)
    analysis.md       the numbers in a form that can be pasted into the report

Usage (from the repo root, no root needed, standard library only):
    python3 experiments/analyze_results.py
    python3 experiments/analyze_results.py --results results --out results/analysis
"""
import argparse
import csv
import glob
import json
import os
import re
import statistics as st

CONDITIONS = [
    ("A", "No congestion"),
    ("B", "Congestion, static"),
    ("C", "Congestion, dynamic"),
    ("D", "Recovery under congestion"),
]


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def mean(v):
    return st.mean(v) if v else float("nan")


def sd(v):
    return st.stdev(v) if len(v) > 1 else 0.0


def analyse_trial(folder):
    recv = {r["report_id"]: float(r["ts_recv"]) for r in read_csv(os.path.join(folder, "recv_log.csv"))}
    sent = []
    for p in sorted(glob.glob(os.path.join(folder, "agent_logs", "*.csv"))):
        sent += read_csv(p)
    out = {}
    groups = {
        "crit": lambda r: r["status"] == "critical",
        "normal_all": lambda r: r["status"] != "critical",
        "normal_agent2": lambda r: r["status"] != "critical" and r["node_id"] == "agent2",
    }
    for name, pick in groups.items():
        rows = [r for r in sent if pick(r)]
        delays = [(recv[r["report_id"]] - float(r["ts_send"])) * 1000 for r in rows if r["report_id"] in recv]
        out[f"{name}_sent"] = len(rows)
        out[f"{name}_recv"] = len(delays)
        out[f"{name}_loss_pct"] = round(100.0 * (len(rows) - len(delays)) / len(rows), 2) if rows else 0.0
        out[f"{name}_avg_ms"] = round(mean(delays), 2) if delays else ""
        out[f"{name}_max_ms"] = round(max(delays), 2) if delays else ""
    out.update(evidence_metrics(folder, sent))
    return out


def evidence_metrics(folder, sent):
    """Flow / queue counters and rule timing from evidence.jsonl (1 s snapshots)."""
    res = {"priority_flow_seen": False, "max_queue1_packets": 0,
           "rule_install_s": "", "rule_removed_after_recovery_s": ""}
    path = os.path.join(folder, "evidence.jsonl")
    if not os.path.exists(path):
        return res
    snaps = [json.loads(line) for line in open(path) if line.strip()]
    for s in snaps:
        if "priority=100" in s.get("flows", ""):
            res["priority_flow_seen"] = True
        for m in re.finditer(r"queue 1: bytes=\d+, pkts=(\d+)", s.get("queues", "")):
            res["max_queue1_packets"] = max(res["max_queue1_packets"], int(m.group(1)))
    crit = sorted(float(r["ts_send"]) for r in sent if r["status"] == "critical")
    active = [s for s in snaps if s["controller"].get("prioritized")]
    if crit and active:
        first_rule = min(v["since"] for v in active[0]["controller"]["prioritized"].values())
        res["rule_install_s"] = round(first_rule - crit[0], 2)
        # recovery = first non-critical report from the node after its first critical one
        node = next(r["node_id"] for r in sent if r["status"] == "critical")
        later = sorted(float(r["ts_send"]) for r in sent
                       if r["node_id"] == node and r["status"] != "critical" and float(r["ts_send"]) > crit[0])
        if later:
            empty = [s["ts"] for s in snaps if s["ts"] > active[0]["ts"]
                     and not s["controller"].get("prioritized") and s["ts"] >= later[0]]
            if empty:
                res["rule_removed_after_recovery_s"] = round(empty[0] - later[0], 2)
    return res


def num(v):
    return float(v) if v not in ("", None) else None


def summarise(rows):
    out = []
    for cond, desc in CONDITIONS:
        r = [x for x in rows if x["condition"] == cond]
        if not r:
            continue
        row = {"condition": cond, "description": desc, "trials": len(r)}
        for key in ("crit_avg_ms", "normal_agent2_avg_ms", "normal_all_avg_ms", "crit_loss_pct",
                    "normal_all_loss_pct", "rule_install_s", "rule_removed_after_recovery_s"):
            v = [num(x[key]) for x in r if num(x[key]) is not None]
            row[key + "_mean"] = round(mean(v), 2) if v else ""
            row[key + "_sd"] = round(sd(v), 2) if v else ""
        row["crit_sent_total"] = sum(x["crit_sent"] for x in r)
        row["crit_recv_total"] = sum(x["crit_recv"] for x in r)
        row["max_queue1_packets"] = max(x["max_queue1_packets"] for x in r)
        out.append(row)
    return out


# ---------------------------------------------------------------- charts (stdlib SVG)
COLORS = ["#527da8", "#c0504d", "#4f9a5b", "#e0a030"]


def svg_bars(path, title, ylabel, groups, series_names, values, errs=None, dots=None):
    """values[g][s] = bar height; errs same shape; dots[g][s] = list of per-trial points."""
    W, H, L, R, T, B = 900, 480, 90, 30, 55, 70
    top = max([v + (errs[g][s] if errs else 0) for g, row in enumerate(values) for s, v in enumerate(row)] +
              [d for g in dots or [] for s in g for d in s] + [1e-9])
    step = 10 ** len(str(int(top))) / 10
    for m in (1, 2, 2.5, 5, 10):
        if top / (step * m) <= 6:
            step *= m
            break
    ymax = step * (int(top / step) + 1)
    pw, ph = W - L - R, H - T - B
    y = lambda v: T + ph * (1 - v / ymax)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="sans-serif">',
         '<rect width="100%" height="100%" fill="white"/>',
         f'<text x="{W/2}" y="28" text-anchor="middle" font-size="18">{title}</text>',
         f'<text x="22" y="{T+ph/2}" text-anchor="middle" font-size="13" transform="rotate(-90 22 {T+ph/2})">{ylabel}</text>']
    k = 0.0
    while k <= ymax + 1e-9:
        o.append(f'<line x1="{L}" y1="{y(k):.1f}" x2="{W-R}" y2="{y(k):.1f}" stroke="#ddd"/>'
                 f'<text x="{L-8}" y="{y(k)+4:.1f}" text-anchor="end" font-size="12">{k:g}</text>')
        k += step
    gw = pw / len(groups)
    bw = gw * 0.7 / len(series_names)
    for g, name in enumerate(groups):
        gx = L + g * gw + gw * 0.15
        o.append(f'<text x="{L+g*gw+gw/2:.1f}" y="{H-B+22}" text-anchor="middle" font-size="13">{name}</text>')
        for s in range(len(series_names)):
            v = values[g][s]
            x = gx + s * bw
            o.append(f'<rect x="{x:.1f}" y="{y(v):.1f}" width="{bw*0.9:.1f}" height="{max(y(0)-y(v),0.5):.1f}" fill="{COLORS[s%4]}"/>')
            cx = x + bw * 0.45
            if errs and errs[g][s]:
                o.append(f'<line x1="{cx:.1f}" y1="{y(v-errs[g][s]):.1f}" x2="{cx:.1f}" y2="{y(v+errs[g][s]):.1f}" stroke="#222" stroke-width="2"/>'
                         f'<line x1="{cx-6:.1f}" y1="{y(v+errs[g][s]):.1f}" x2="{cx+6:.1f}" y2="{y(v+errs[g][s]):.1f}" stroke="#222"/>'
                         f'<line x1="{cx-6:.1f}" y1="{y(max(v-errs[g][s],0)):.1f}" x2="{cx+6:.1f}" y2="{y(max(v-errs[g][s],0)):.1f}" stroke="#222"/>')
            for d in (dots[g][s] if dots else []):
                o.append(f'<circle cx="{cx:.1f}" cy="{y(d):.1f}" r="3.5" fill="white" stroke="#222"/>')
            o.append(f'<text x="{cx:.1f}" y="{y(v+(errs[g][s] if errs else 0))-8:.1f}" text-anchor="middle" font-size="12">{v:.1f}</text>')
    if len(series_names) > 1:
        for s, n in enumerate(series_names):
            o.append(f'<rect x="{L+s*170}" y="{H-24}" width="12" height="12" fill="{COLORS[s%4]}"/>'
                     f'<text x="{L+s*170+18}" y="{H-14}" font-size="12">{n}</text>')
    o.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(o))


def try_png(svg_path):
    """Convert to PNG when cairosvg is available; otherwise leave the SVG only."""
    try:
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=svg_path[:-4] + ".png", scale=2)
    except Exception:
        pass


def make_charts(out, rows, summ):
    names = [f"{s['condition']}: {s['description']}" for s in summ]
    per = lambda c, k: [num(x[k]) for x in rows if x["condition"] == c and num(x[k]) is not None]
    p = os.path.join(out, "critical_delay.svg")
    svg_bars(p, "Critical report delay by condition (mean ± sample SD, dots = trials)", "delay (ms)", names,
             ["critical reports"], [[s["crit_avg_ms_mean"]] for s in summ], [[s["crit_avg_ms_sd"]] for s in summ],
             [[per(s["condition"], "crit_avg_ms")] for s in summ])
    try_png(p)
    p = os.path.join(out, "critical_vs_normal.svg")
    svg_bars(p, "Critical vs normal report delay (agent2)", "delay (ms)", names,
             ["critical reports", "normal reports"],
             [[s["crit_avg_ms_mean"], s["normal_agent2_avg_ms_mean"]] for s in summ],
             [[s["crit_avg_ms_sd"], s["normal_agent2_avg_ms_sd"]] for s in summ])
    try_png(p)
    rs = [s for s in summ if s["rule_install_s_mean"] != ""]
    if rs:
        p = os.path.join(out, "rule_timing.svg")
        svg_bars(p, "SDN rule timing (controller view, 1 s snapshots)", "seconds",
                 [f"{s['condition']}: {s['description']}" for s in rs],
                 ["first critical report -> rule installed", "recovery -> rule removed"],
                 [[s["rule_install_s_mean"], s["rule_removed_after_recovery_s_mean"] or 0] for s in rs],
                 [[s["rule_install_s_sd"], s["rule_removed_after_recovery_s_sd"] or 0] for s in rs])
        try_png(p)


def write_md(path, summ, rows):
    by = {s["condition"]: s for s in summ}
    L = ["# Results summary (generated by experiments/analyze_results.py)", "",
         "Values are the mean and sample SD of the per-trial averages. Delay = receive time at the server "
         "(tcpdump) minus the agent's send time.", "",
         "| Condition | Trials | Critical sent/recv | Critical loss | Critical delay (ms) | Normal delay, agent2 (ms) | Rule install (s) | Rule removed after recovery (s) |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]
    f = lambda s, k: f"{s[k+'_mean']} ± {s[k+'_sd']}" if s[k + "_mean"] != "" else "n/a"
    for s in summ:
        L.append(f"| {s['condition']} ({s['description']}) | {s['trials']} | {s['crit_sent_total']}/{s['crit_recv_total']} | "
                 f"{s['crit_loss_pct_mean']}% | {f(s,'crit_avg_ms')} | {f(s,'normal_agent2_avg_ms')} | "
                 f"{f(s,'rule_install_s')} | {f(s,'rule_removed_after_recovery_s')} |")
    if "B" in by and "C" in by:
        b, c = by["B"]["crit_avg_ms_mean"], by["C"]["crit_avg_ms_mean"]
        L += ["", f"**B vs C:** critical delay falls from {b} ms (static) to {c} ms (dynamic), "
                  f"a reduction of {100*(b-c)/b:.1f}%. The two conditions use the same bottleneck and load."]
    if "C" in by:
        L.append(f"**Normal reports under congestion:** about {by['C']['normal_agent2_avg_ms_mean']} ms in C, "
                 "i.e. prioritisation helps the critical flow, not normal traffic.")
    L += ["", "Per-trial values are in per_trial.csv."]
    open(path, "w").write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = a.out or os.path.join(a.results, "analysis")
    os.makedirs(out, exist_ok=True)
    rows = []
    for cond, desc in CONDITIONS:
        for folder in sorted(glob.glob(os.path.join(a.results, cond, "trial*"))):
            if not os.path.exists(os.path.join(folder, "recv_log.csv")):
                continue
            r = {"condition": cond, "description": desc, "trial": int(re.sub(r"\D", "", os.path.basename(folder)))}
            r.update(analyse_trial(folder))
            rows.append(r)
    if not rows:
        raise SystemExit(f"no trial folders found under {a.results}/<condition>/trial<N>/")
    summ = summarise(rows)
    for name, data in (("per_trial.csv", rows), ("summary.csv", summ)):
        with open(os.path.join(out, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0].keys()), lineterminator="\n")
            w.writeheader()
            w.writerows(data)
    make_charts(out, rows, summ)
    write_md(os.path.join(out, "analysis.md"), summ, rows)
    print(f"{len(rows)} trials analysed -> {out}/")
    print(open(os.path.join(out, "analysis.md")).read())


if __name__ == "__main__":
    main()
