#!/usr/bin/env python3
"""Regenerate readable P2-08 publication SVGs without changing frozen data."""
from __future__ import annotations
import argparse, csv, html, math
from pathlib import Path

TREATMENTS=("C2","C5","PREWARM")
STAGES=tuple(f"T{i}_T{i+1}" for i in range(8))
COLORS={"C2":"#2F7FB8","C5":"#D62728","PREWARM":"#2CA02C"}
STAGE_COLORS=["#4E79A7","#A0CBE8","#F28E2B","#FFBE7D","#59A14F","#8CD17D","#B6992D","#E15759"]

def require(v,m):
    if not v: raise RuntimeError(m)
def rows(path): return list(csv.DictReader(Path(path).open(encoding="utf-8-sig")))
def num(x):
    try: return float(x)
    except (TypeError,ValueError): return None
def svg_start(w,h,title):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
            '<rect width="100%" height="100%" fill="white"/>',
            f'<text x="{w/2}" y="42" text-anchor="middle" font-family="Times New Roman,serif" font-size="27" font-weight="bold">{html.escape(title)}</text>']
def write(path,parts): Path(path).write_text("\n".join(parts+["</svg>"]),encoding="utf-8")

def rmst_chart(out,summary):
    w,h=1050,650; L,R,T,B=125,70,92,155; cw=w-L-R; ch=h-T-B; ymax=300
    data={r["treatment"]:r for r in summary}; parts=svg_start(w,h,"Exploratory Three-Arm Recovery Comparison")
    for tick in range(0,301,50):
        y=T+ch-tick/ymax*ch; parts += [f'<line x1="{L}" y1="{y:.1f}" x2="{L+cw}" y2="{y:.1f}" stroke="#D8D8D8"/>',f'<text x="{L-14}" y="{y+5:.1f}" text-anchor="end" font-family="Arial,sans-serif" font-size="15">{tick}</text>']
    for i,t in enumerate(TREATMENTS):
        r=data[t]; v=float(r["km_rmst300_seconds"]); x=L+100+i*270; bw=145; bh=v/ymax*ch; y=T+ch-bh
        obs=int(r["observed_recoveries"]); cens=int(r["right_censored"])
        parts += [f'<rect x="{x}" y="{y:.1f}" width="{bw}" height="{bh:.1f}" rx="2" fill="{COLORS[t]}" stroke="#222" stroke-width="1.4"/>',
                  f'<text x="{x+bw/2}" y="{y-14:.1f}" text-anchor="middle" font-family="Arial,sans-serif" font-size="19" font-weight="bold">{v:.3f} s</text>',
                  f'<text x="{x+bw/2}" y="{T+ch+34}" text-anchor="middle" font-family="Arial,sans-serif" font-size="20" font-weight="bold">{t}</text>',
                  f'<text x="{x+bw/2}" y="{T+ch+58}" text-anchor="middle" font-family="Arial,sans-serif" font-size="14">{obs} recovered, {cens} censored</text>']
    parts += [f'<text x="32" y="{T+ch/2}" transform="rotate(-90 32 {T+ch/2})" text-anchor="middle" font-family="Arial,sans-serif" font-size="17">KM RMST through 300 s (seconds; lower is faster)</text>',
              f'<text x="{w/2}" y="570" text-anchor="middle" font-family="Arial,sans-serif" font-size="15">C5 - C2 = +7.621 s; PREWARM - C2 = +1.887 s; PREWARM - C5 = -5.734 s</text>',
              f'<text x="{w/2}" y="602" text-anchor="middle" font-family="Arial,sans-serif" font-size="14">Three randomized blocks; exploratory analysis. No superiority claim supported; equivalence was not tested.</text>']
    write(out/"P2-08-Figure-1-RMST300-three-arm-R02.svg",parts)

def paired_chart(out,pairs,comparisons):
    w,h=1120,700; L,R,T,B=115,55,120,205; cw=w-L-R; ch=h-T-B
    keys=("c5_minus_c2_seconds","prewarm_minus_c2_seconds","prewarm_minus_c5_seconds")
    labels={keys[0]:"C5 - C2",keys[1]:"PREWARM - C2",keys[2]:"PREWARM - C5"}; colors={keys[0]:"#D62728",keys[1]:"#2CA02C",keys[2]:"#8E63BE"}
    vals=[float(r[k]) for r in pairs for k in keys]; lo=math.floor((min(vals)-10)/10)*10; hi=math.ceil((max(vals)+10)/10)*10; lo=min(lo,-40); hi=max(hi,30)
    def yy(v): return T+(hi-v)/(hi-lo)*ch
    parts=svg_start(w,h,"Paired RMST(300) Differences by Randomized Block")
    for tick in range(int(lo),int(hi)+1,10):
        y=yy(tick); zero=tick==0; parts += [f'<line x1="{L}" y1="{y:.1f}" x2="{L+cw}" y2="{y:.1f}" stroke="{"#222" if zero else "#D8D8D8"}" stroke-width="{"1.8" if zero else "1"}" stroke-dasharray="{"7 5" if zero else "0"}"/>',f'<text x="{L-13}" y="{y+5:.1f}" text-anchor="end" font-family="Arial,sans-serif" font-size="14">{tick:+d}</text>']
    offsets={keys[0]:-32,keys[1]:0,keys[2]:32}
    for b in range(1,4):
        cx=L+160+(b-1)*300; parts.append(f'<text x="{cx}" y="{T+ch+38}" text-anchor="middle" font-family="Arial,sans-serif" font-size="18" font-weight="bold">Block {b}</text>')
        r=next(x for x in pairs if int(x["block"])==b)
        for k in keys:
            v=float(r[k]); x=cx+offsets[k]; y=yy(v); label_y=y-13 if v>=0 else y+25
            parts += [f'<circle cx="{x}" cy="{y:.1f}" r="9" fill="{colors[k]}" stroke="#111" stroke-width="1.4"/>',f'<text x="{x}" y="{label_y:.1f}" text-anchor="middle" font-family="Arial,sans-serif" font-size="14" font-weight="bold">{v:+.3f}</text>']
    for j,k in enumerate(keys):
        x=150+j*290; parts += [f'<circle cx="{x}" cy="570" r="8" fill="{colors[k]}" stroke="#111"/>',f'<text x="{x+18}" y="576" font-family="Arial,sans-serif" font-size="15">{labels[k]}</text>']
    means={r["contrast"]:float(r["mean_paired_difference_seconds"]) for r in comparisons}; pvals={r["contrast"]:r["exact_sign_flip_p_exploratory"] for r in comparisons}
    parts += [f'<text x="{w/2}" y="617" text-anchor="middle" font-family="Arial,sans-serif" font-size="14">Mean differences: C5-C2 {means["C5_MINUS_C2"]:+.3f} s (p={pvals["C5_MINUS_C2"]}); PREWARM-C2 {means["PREWARM_MINUS_C2"]:+.3f} s (p={pvals["PREWARM_MINUS_C2"]}); PREWARM-C5 {means["PREWARM_MINUS_C5"]:+.3f} s (p={pvals["PREWARM_MINUS_C5"]})</text>',
              f'<text x="{w/2}" y="650" text-anchor="middle" font-family="Arial,sans-serif" font-size="14">Negative values favor the first-named treatment. Three pairs are exploratory and cannot establish superiority or equivalence.</text>',
              f'<text x="32" y="{T+ch/2}" transform="rotate(-90 32 {T+ch/2})" text-anchor="middle" font-family="Arial,sans-serif" font-size="17">Paired RMST difference (seconds)</text>']
    write(out/"P2-08-Figure-2-paired-block-differences-R02.svg",parts)

def stage_chart(out,summary):
    w,h=1250,760; L,R,T,B=145,120,100,245; cw=w-L-R; ch=360; data={(r["treatment"],r["stage"]):r for r in summary}
    totals={t:sum(num(data.get((t,s),{}).get("mean_seconds")) or 0 for s in STAGES) for t in TREATMENTS}; xmax=max(10,math.ceil(max(totals.values())/25)*25); scale=cw/xmax
    parts=svg_start(w,h,"Observed Mean T0-T8 Recovery-Stage Durations")
    for tick in range(0,int(xmax)+1,25):
        x=L+tick*scale; parts += [f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{T+ch}" stroke="#E0E0E0"/>',f'<text x="{x:.1f}" y="{T+ch+28}" text-anchor="middle" font-family="Arial,sans-serif" font-size="14">{tick}</text>']
    for i,t in enumerate(TREATMENTS):
        y=T+45+i*110; x=L; missing=[]
        parts.append(f'<text x="{L-18}" y="{y+28}" text-anchor="end" font-family="Arial,sans-serif" font-size="19" font-weight="bold">{t}</text>')
        for j,s in enumerate(STAGES):
            r=data.get((t,s),{}); v=num(r.get("mean_seconds")); n=int(r.get("n_reported") or 0)
            if v is None or n==0: missing.append(s.replace("_","-")); continue
            bw=v*scale; parts.append(f'<rect x="{x:.1f}" y="{y}" width="{bw:.1f}" height="48" fill="{STAGE_COLORS[j]}" stroke="white" stroke-width="1.2"/>')
            if bw>=38: parts.append(f'<text x="{x+bw/2:.1f}" y="{y+30}" text-anchor="middle" font-family="Arial,sans-serif" font-size="13" font-weight="bold" fill="{"white" if j in (0,7) else "black"}">{v:.1f}</text>')
            x+=bw
        parts += [f'<text x="{x+10:.1f}" y="{y+22}" font-family="Arial,sans-serif" font-size="15" font-weight="bold">Sum {totals[t]:.1f} s</text>',f'<text x="{x+10:.1f}" y="{y+43}" font-family="Arial,sans-serif" font-size="12">Missing: {html.escape(", ".join(missing) if missing else "none")}</text>']
    parts += [f'<text x="{L+cw/2}" y="{T+ch+58}" text-anchor="middle" font-family="Arial,sans-serif" font-size="17">Sum of reported mean stage durations (seconds)</text>']
    for j,s in enumerate(STAGES):
        x=95+(j%4)*285; y=555+(j//4)*48; ns=[int(data.get((t,s),{}).get("n_reported") or 0) for t in TREATMENTS]
        parts += [f'<rect x="{x}" y="{y}" width="23" height="16" fill="{STAGE_COLORS[j]}"/>',f'<text x="{x+33}" y="{y+14}" font-family="Arial,sans-serif" font-size="14">{s.replace("_","-")} (n C2/C5/PW={ns[0]}/{ns[1]}/{ns[2]})</text>']
    parts += [f'<text x="{w/2}" y="690" text-anchor="middle" font-family="Arial,sans-serif" font-size="14">Numbers inside segments are observed means in seconds. Missing and structurally non-applicable stages are not imputed.</text>',f'<text x="{w/2}" y="718" text-anchor="middle" font-family="Arial,sans-serif" font-size="14">Stacked sums are descriptive because stage-specific means may use unequal denominators.</text>']
    write(out/"P2-08-Figure-3-T0-T8-stage-decomposition-R02.svg",parts)

def main():
    p=argparse.ArgumentParser(); p.add_argument("--p2-08-root"); p.add_argument("--p2-07-root"); p.add_argument("--output-root"); p.add_argument("--self-test",action="store_true"); a=p.parse_args()
    if a.self_test:
        require(len(STAGES)==8,"STAGE_COUNT"); print("P2_08_FIGURES_R02_SELF_TEST=PASS"); print("FROZEN_DATA_MODIFIED=NO"); return 0
    require(a.p2_08_root and a.p2_07_root and a.output_root,"ROOTS_REQUIRED")
    p208=Path(a.p2_08_root); p207=Path(a.p2_07_root); out=Path(a.output_root); out.mkdir(parents=True,exist_ok=True)
    treatment=rows(p208/"tables/P2-08-treatment-summary.csv"); stage=rows(p208/"tables/P2-08-T0-T8-stage-summary.csv"); comparisons=rows(p208/"tables/P2-08-paired-comparison-summary.csv"); pairs=rows(p207/"P2-07-R02-paired-differences.csv")
    require(len(treatment)==3 and len(stage)==24 and len(comparisons)==3 and len(pairs)==3,"INPUT_TABLE_SIZE_MISMATCH")
    rmst_chart(out,treatment); paired_chart(out,pairs,comparisons); stage_chart(out,stage)
    print("P2_08_FIGURES_R02=VERIFIED_PASS"); print("FIGURES_GENERATED=3"); print(f"OUTPUT={out}"); print("FROZEN_DATA_MODIFIED=NO")
if __name__=="__main__": main()
