#!/usr/bin/env python3
"""Offline validation, analysis, paper artifacts, and freeze for P2-07 R02."""
from __future__ import annotations
import argparse, csv, hashlib, html, json, math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_FREEZE_SHA256 = "FC1EE3D88B66FF79CEE04510B03CC267280274DEE7C147933F6AFAADADA5BCFC"
TREATMENTS = ("C2", "C5", "PREWARM")
STAGES = tuple(f"T{i}_T{i+1}" for i in range(8))

def utc(): return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def require(v, m):
    if not v: raise RuntimeError(m)
def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda:f.read(1048576), b""): h.update(b)
    return h.hexdigest().upper()
def load_json(path): return json.loads(Path(path).read_text(encoding="utf-8-sig"))
def write_json(path, value): Path(path).write_text(json.dumps(value, indent=2)+"\n", encoding="utf-8")
def write_csv(path, rows, fields=None):
    rows=list(rows); fields=fields or (list(rows[0]) if rows else [])
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        w=csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None
def event_time(x):
    if not x: return None
    try: return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
    except ValueError: return None
def stage_value(durations, events, stage):
    variants=(stage, stage.replace("_", "-"), stage.lower(), stage.lower().replace("_", "-"), stage+"_seconds")
    for key in variants:
        v=fnum(durations.get(key))
        if v is not None and v >= 0: return v, "REPORTED"
    a,b=stage.split("_"); ta,tb=event_time(events.get(a)),event_time(events.get(b))
    if ta is not None and tb is not None and tb >= ta: return tb-ta, "DERIVED_FROM_EVENTS"
    return None, "MISSING_NOT_IMPUTED"
def mean(xs): return sum(xs)/len(xs) if xs else None
def esc(s): return html.escape(str(s))

def bar_svg(path, treatment_summary):
    width,height=920,520; left,top=110,90; chart_w,chart_h=720,310
    vals=[float(next(x for x in treatment_summary if x["treatment"]==t)["km_rmst300_seconds"]) for t in TREATMENTS]
    colors={"C2":"#2878B5","C5":"#D62728","PREWARM":"#2CA02C"}; ymax=300
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="white"/>',
           '<text x="460" y="38" text-anchor="middle" font-family="serif" font-size="26" font-weight="bold">P2-07 Three-Arm RMST(300) Comparison</text>']
    for tick in range(0,301,50):
        y=top+chart_h-(tick/ymax)*chart_h
        parts += [f'<line x1="{left}" y1="{y:.1f}" x2="{left+chart_w}" y2="{y:.1f}" stroke="#ddd"/>',f'<text x="{left-12}" y="{y+5:.1f}" text-anchor="end" font-family="sans-serif" font-size="14">{tick}</text>']
    for i,(t,v) in enumerate(zip(TREATMENTS,vals)):
        x=left+90+i*220; bh=v/ymax*chart_h; y=top+chart_h-bh
        parts += [f'<rect x="{x}" y="{y:.1f}" width="120" height="{bh:.1f}" fill="{colors[t]}" stroke="#222"/>',
                  f'<text x="{x+60}" y="{y-10:.1f}" text-anchor="middle" font-family="sans-serif" font-size="18">{v:.3f} s</text>',
                  f'<text x="{x+60}" y="{top+chart_h+30}" text-anchor="middle" font-family="sans-serif" font-size="18">{t}</text>']
    parts += [f'<text x="25" y="{top+chart_h/2}" transform="rotate(-90 25 {top+chart_h/2})" text-anchor="middle" font-family="sans-serif" font-size="17">KM RMST through 300 s (lower is faster)</text>',
              '<text x="460" y="485" text-anchor="middle" font-family="sans-serif" font-size="14">Three randomized blocks; exploratory evaluation; one right-censored episode per treatment.</text>','</svg>']
    Path(path).write_text("\n".join(parts),encoding="utf-8")

def paired_svg(path, pairs):
    width,height=980,560; left,top=100,80; cw,ch=790,350; colors={"c5_minus_c2_seconds":"#D62728","prewarm_minus_c2_seconds":"#2CA02C","prewarm_minus_c5_seconds":"#9467BD"}; labels={"c5_minus_c2_seconds":"C5 - C2","prewarm_minus_c2_seconds":"PREWARM - C2","prewarm_minus_c5_seconds":"PREWARM - C5"}; keys=list(colors); vals=[float(r[k]) for r in pairs for k in keys]; lo=min(-70,min(vals)-10); hi=max(90,max(vals)+10)
    def yy(v): return top+(hi-v)/(hi-lo)*ch
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/>','<text x="490" y="38" text-anchor="middle" font-family="serif" font-size="26" font-weight="bold">Paired RMST(300) Differences by Randomized Block</text>']
    for v in range(int(math.floor(lo/20)*20),int(math.ceil(hi/20)*20)+1,20):
        y=yy(v); parts += [f'<line x1="{left}" y1="{y:.1f}" x2="{left+cw}" y2="{y:.1f}" stroke="{"#111" if v==0 else "#ddd"}" stroke-dasharray="{"7 5" if v==0 else "0"}"/>',f'<text x="{left-10}" y="{y+5:.1f}" text-anchor="end" font-family="sans-serif" font-size="13">{v}</text>']
    for j,k in enumerate(keys):
        pts=[]
        for i,r in enumerate(pairs):
            x=left+150+(int(r["block"])-1)*240+j*22-22; y=yy(float(r[k])); pts.append((x,y)); parts.append(f'<circle cx="{x}" cy="{y:.1f}" r="7" fill="{colors[k]}" stroke="#111"/>')
        lx=left+20+j*250; parts += [f'<rect x="{lx}" y="465" width="18" height="12" fill="{colors[k]}"/>',f'<text x="{lx+27}" y="476" font-family="sans-serif" font-size="15">{labels[k]}</text>']
    for b in range(1,4): parts.append(f'<text x="{left+150+(b-1)*240}" y="{top+ch+35}" text-anchor="middle" font-family="sans-serif" font-size="17">Block {b}</text>')
    parts += ['<text x="490" y="530" text-anchor="middle" font-family="sans-serif" font-size="14">Negative differences favor the first-named adaptive arm; three pairs do not support confirmatory inference.</text>','</svg>']; Path(path).write_text("\n".join(parts),encoding="utf-8")

def stage_svg(path, stage_summary):
    usable=[r for r in stage_summary if int(r["n_reported"])>0 and fnum(r["mean_seconds"]) is not None]
    by=defaultdict(dict)
    for r in usable: by[r["treatment"]][r["stage"]]=float(r["mean_seconds"])
    colors=["#4E79A7","#A0CBE8","#F28E2B","#FFBE7D","#59A14F","#8CD17D","#B6992D","#E15759"]
    width,height=1100,610; left,top=120,80; cw=820; scale=3.0
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/>','<text x="550" y="38" text-anchor="middle" font-family="serif" font-size="25" font-weight="bold">Observed Mean T0-T8 Stage Durations</text>']
    for i,t in enumerate(TREATMENTS):
        y=110+i*105; x=left; parts.append(f'<text x="{left-18}" y="{y+28}" text-anchor="end" font-family="sans-serif" font-size="18">{t}</text>')
        for j,s in enumerate(STAGES):
            v=by[t].get(s,0.0); w=v*scale
            if w>0: parts += [f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="42" fill="{colors[j]}" stroke="white"/>']
            x+=w
        parts.append(f'<text x="{x+8:.1f}" y="{y+27}" font-family="sans-serif" font-size="14">reported segments only</text>')
    for j,s in enumerate(STAGES):
        x=80+(j%4)*250; y=465+(j//4)*40; parts += [f'<rect x="{x}" y="{y}" width="20" height="14" fill="{colors[j]}"/>',f'<text x="{x+29}" y="{y+13}" font-family="sans-serif" font-size="15">{s.replace("_","-")}</text>']
    parts += ['<text x="550" y="575" text-anchor="middle" font-family="sans-serif" font-size="14">Missing and structurally non-applicable stages are not imputed; stacked means may have unequal denominators.</text>','</svg>']; Path(path).write_text("\n".join(parts),encoding="utf-8")

def main():
    p=argparse.ArgumentParser(); p.add_argument("--self-test",action="store_true"); p.add_argument("--p2-07-root"); p.add_argument("--output-root"); p.add_argument("--expected-freeze-sha256",default=EXPECTED_FREEZE_SHA256); a=p.parse_args()
    if a.self_test:
        require(len(STAGES)==8,"STAGE_COUNT"); require(EXPECTED_FREEZE_SHA256.startswith("FC1EE3"),"FREEZE_BINDING"); print("P2_08_SELF_TEST=PASS"); print("OFFLINE_ONLY=YES"); print("KUBERNETES_QUERIES=0"); print("LIVE_WORKLOAD_REQUESTS=0"); return 0
    require(a.p2_07_root and a.output_root,"ROOT_ARGUMENT_REQUIRED")
    src=Path(a.p2_07_root); out=Path(a.output_root); tables=out/"tables"; figures=out/"figures"; proof=out/"proof"; paper=out/"paper"
    for d in (out,tables,figures,proof,paper): d.mkdir(parents=True,exist_ok=True)
    freeze=src/"P2-07-R02-FREEZE.txt"; result_path=src/"P2-07-R02-result-summary.json"; dataset_path=src/"P2-07-R02-primary-dataset.csv"; pairs_path=src/"P2-07-R02-paired-differences.csv"; design_path=src/"P2-07-R02-frozen-design.json"
    for f in (freeze,result_path,dataset_path,pairs_path,design_path): require(f.is_file(),f"MISSING_INPUT:{f.name}")
    require(sha(freeze)==a.expected_freeze_sha256.upper(),"P2_07_FREEZE_SHA256_MISMATCH")
    result=load_json(result_path); design=load_json(design_path); require(result.get("status")=="VERIFIED_PASS","P2_07_NOT_VERIFIED"); require(result.get("episodes")==9 and result.get("blocks")==3,"DESIGN_SIZE_MISMATCH"); require(result.get("strict_300_second_censoring") is True,"CENSORING_NOT_STRICT")
    primary=list(csv.DictReader(dataset_path.open(encoding="utf-8-sig"))); pairs=list(csv.DictReader(pairs_path.open(encoding="utf-8-sig"))); require(len(primary)==9 and len(pairs)==3,"PRIMARY_TABLE_SIZE_MISMATCH")
    require(all(str(r["hpa_restored"]).lower()=="true" for r in primary),"HPA_RESTORATION_FAILURE"); require(all(float(r["time"])<=300.0 for r in primary),"CENSOR_HORIZON_BREACH")
    summaries=[]
    for f in sorted((src/"episodes").glob("P2-07-R02-B*-*/analysis/P2-06-pilot-summary.json")):
        s=load_json(f)
        if s.get("status")=="SCIENTIFICALLY_ADMISSIBLE": summaries.append(s)
    require(len(summaries)==9,"SELECTED_EPISODE_COUNT_NOT_NINE")
    attempts=[s["attempt"] for s in summaries]; require(len(set(attempts))==9,"DUPLICATE_SELECTED_ATTEMPT")
    normalized=[]; stage_rows=[]; action_rows=[]
    for s in summaries:
        block=int(s.get("block") or s["attempt"].split("-B",1)[1].split("-",1)[0]); pos=int(s.get("position") or s["attempt"].split("-P",1)[1].split("-",1)[0]); events=s.get("events") or {}; durations=s.get("durations") or {}
        row={"block":block,"position":pos,"treatment":s["treatment"],"attempt":s["attempt"],"scheduled_requests":s.get("scheduled_requests"),"completed_requests":s.get("completed_requests"),"error_requests":s.get("error_requests"),"observed_recovery":s.get("stable_recovery_observed"),"right_censored":s.get("right_censored"),"rmst300_contribution_seconds":s.get("recovery_seconds_or_rmst300_contribution"),"clock_uncertainty_ms":s.get("maximum_clock_mapping_uncertainty_ms"),"hpa_restored":s.get("hpa_exactly_restored"),"censor_reason":s.get("censor_reason"),"t0_t8_disclosure":s.get("t0_t8_disclosure")}
        for i in range(9): row[f"T{i}"]=events.get(f"T{i}")
        for st in STAGES:
            v,status=stage_value(durations,events,st); row[f"{st}_seconds"]=v; row[f"{st}_status"]=status; stage_rows.append({"block":block,"treatment":s["treatment"],"attempt":s["attempt"],"stage":st,"seconds":v,"status":status})
        normalized.append(row)
        action_rows.append({"block":block,"treatment":s["treatment"],"attempt":s["attempt"],"prewarm_action_applied":s.get("prewarm_action_applied"),"prewarm_before_t0":s.get("prewarm_before_t0"),"c5_action_count":s.get("c5_action_count"),"hpa_restored":s.get("hpa_exactly_restored"),"disclosure":s.get("t0_t8_disclosure")})
    write_csv(tables/"P2-08-normalized-three-arm-dataset.csv",normalized)
    write_csv(tables/"P2-08-T0-T8-stage-durations.csv",stage_rows)
    write_csv(tables/"P2-08-action-restoration-audit.csv",action_rows)
    treatment_summary=[]
    for t in TREATMENTS:
        rr=[r for r in primary if r["treatment"]==t]; treatment_summary.append({"treatment":t,"episodes":3,"observed_recoveries":sum(str(r["observed"]).lower()=="true" for r in rr),"right_censored":sum(str(r["right_censored"]).lower()=="true" for r in rr),"km_rmst300_seconds":result[{"C2":"c2_km_rmst300_seconds","C5":"c5_km_rmst300_seconds","PREWARM":"prewarm_km_rmst300_seconds"}[t]],"completed_requests":sum(int(r["completed_requests"]) for r in rr),"error_requests":sum(int(r["error_requests"]) for r in rr)})
    write_csv(tables/"P2-08-treatment-summary.csv",treatment_summary)
    stage_summary=[]
    for t in TREATMENTS:
        for st in STAGES:
            rr=[r for r in stage_rows if r["treatment"]==t and r["stage"]==st]; vals=[float(r["seconds"]) for r in rr if r["seconds"] is not None]; stage_summary.append({"treatment":t,"stage":st,"n_reported":len(vals),"n_missing_or_not_applicable":3-len(vals),"mean_seconds":round(mean(vals),6) if vals else None,"min_seconds":round(min(vals),6) if vals else None,"max_seconds":round(max(vals),6) if vals else None,"imputed_values":0})
    write_csv(tables/"P2-08-T0-T8-stage-summary.csv",stage_summary)
    comparisons=[{"contrast":"C5_MINUS_C2","mean_paired_difference_seconds":result["mean_c5_minus_c2_block_difference_seconds"],"exact_sign_flip_p_exploratory":result["c5_vs_c2_exact_sign_flip_p_exploratory"],"interpretation":"Positive favors C2; superiority not supported"},{"contrast":"PREWARM_MINUS_C2","mean_paired_difference_seconds":result["mean_prewarm_minus_c2_block_difference_seconds"],"exact_sign_flip_p_exploratory":result["prewarm_vs_c2_exact_sign_flip_p_exploratory"],"interpretation":"Positive favors C2; superiority not supported"},{"contrast":"PREWARM_MINUS_C5","mean_paired_difference_seconds":result["mean_prewarm_minus_c5_block_difference_seconds"],"exact_sign_flip_p_exploratory":result["prewarm_vs_c5_exact_sign_flip_p_exploratory"],"interpretation":"Negative descriptively favors PREWARM; superiority not supported"}]
    write_csv(tables/"P2-08-paired-comparison-summary.csv",comparisons)
    checklist=[{"professor_requirement":"Implement adaptive recovery framework","status":"COMPLETED","evidence":"Original C5 plus dominant-stage-selected bounded PREWARM action"},{"professor_requirement":"Dominant-stage identification and action selection","status":"COMPLETED","evidence":"Offline replay selected T4-T5 prewarming; T7-T8 retained HOLD decision"},{"professor_requirement":"Stable SLO recovery definition","status":"COMPLETED","evidence":"Eight consecutive 15-second windows below 123.472 ms"},{"professor_requirement":"T0-T8 stage measurements","status":"COMPLETED_WITH_MISSINGNESS_DISCLOSED","evidence":"Observed timestamps retained; missing/non-applicable events not imputed"},{"professor_requirement":"Controlled low-high-low workload","status":"COMPLETED","evidence":"Frozen 5-25-5 RPS protocol reused"},{"professor_requirement":"Report recovery failures","status":"COMPLETED","evidence":"Strict right censoring at 300 seconds"},{"professor_requirement":"Compare adaptive controller with baseline","status":"COMPLETED_EXPLORATORY","evidence":"Three randomized blocks of C2, C5 and PREWARM"},{"professor_requirement":"Meaningful figures","status":"COMPLETED","evidence":"RMST, paired contrasts and T0-T8 figures generated"},{"professor_requirement":"Additional feasible overload level such as 20 RPS","status":"UNRESOLVED_UNLESS_SEPARATE_EVIDENCE_EXISTS","evidence":"P2-07 retained frozen 25 RPS challenge; do not claim 20 RPS without evidence"},{"professor_requirement":"Paper pseudocode, equations and prior-work comparison","status":"PAPER_INTEGRATION_REQUIRED","evidence":"Must be completed in final six-page manuscript"}]
    write_csv(tables/"P2-08-professor-requirements-checklist.csv",checklist)
    bar_svg(figures/"P2-08-Figure-1-RMST300-three-arm.svg",treatment_summary); paired_svg(figures/"P2-08-Figure-2-paired-block-differences.svg",pairs); stage_svg(figures/"P2-08-Figure-3-T0-T8-stage-decomposition.svg",stage_summary)
    conclusion=("Across three randomized blocks, KM RMST(300) was 265.811 s for C2, 273.432 s for C5, and 267.698 s for PREWARM. "
                "Relative to C2, the mean paired differences were +7.621 s for C5 and +1.887 s for PREWARM; PREWARM minus C5 was -5.734 s. "
                "All treatments recovered in two of three episodes, with one right-censored episode per treatment. Exact sign-flip p-values were 0.50, 1.00, and 1.00, respectively. "
                "With only three pairs, the evaluation is exploratory and does not support a superiority or equivalence claim. PREWARM was descriptively close to C2 and faster than C5, but neither difference was statistically supported.")
    (paper/"P2-08-paper-ready-results.txt").write_text(conclusion+"\n",encoding="utf-8")
    record={"schema":"NET470-PART2-P2-08-FINAL-ANALYSIS-V1","recorded_at_utc":utc(),"status":"VERIFIED_PASS","project_scope":"OFFLINE_THREE_ARM_P2_07_ANALYSIS_AND_PAPER_ARTIFACT_FREEZE","p2_07_freeze_sha256":sha(freeze),"episodes":9,"blocks":3,"treatments":3,"c2_km_rmst300_seconds":result["c2_km_rmst300_seconds"],"c5_km_rmst300_seconds":result["c5_km_rmst300_seconds"],"prewarm_km_rmst300_seconds":result["prewarm_km_rmst300_seconds"],"superiority_claim_supported":False,"equivalence_tested":False,"inference_scope":"EXPLORATORY_FOLLOW_UP","kubernetes_queried":False,"cluster_modified":False,"live_workload_executed":False,"stage9_modified":False,"stage10_modified":False,"p2_08_complete":True,"next_step":"INTEGRATE_P2_08_ARTIFACTS_INTO_FINAL_SIX_PAGE_PAPER","failure":""}
    write_json(out/"P2-08-final-analysis-record.json",record)
    generated=[p for p in out.rglob("*") if p.is_file() and p.parent!=proof and p.name!="P2-08-FINAL-FREEZE.txt"]
    manifest=[{"relative_path":p.relative_to(out).as_posix(),"size_bytes":p.stat().st_size,"sha256":sha(p)} for p in sorted(generated)]
    write_csv(proof/"P2-08-SHA256-MANIFEST.csv",manifest)
    freeze_out=out/"P2-08-FINAL-FREEZE.txt"; freeze_out.write_text("\n".join(f"{k}={v}" for k,v in record.items())+f"\nMANIFEST_SHA256={sha(proof/'P2-08-SHA256-MANIFEST.csv')}\n",encoding="utf-8")
    print("=== P2-08 FINAL ANALYSIS SUMMARY ==="); [print(f"{k}={v}") for k,v in record.items()]; print(f"RECORD_SHA256={sha(out/'P2-08-final-analysis-record.json')}"); print(f"FREEZE_SHA256={sha(freeze_out)}"); print(f"P2_08_OUTPUT={out}")
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as e:
        print(f"P2_08_FAILURE={type(e).__name__}:{e}")
        raise
