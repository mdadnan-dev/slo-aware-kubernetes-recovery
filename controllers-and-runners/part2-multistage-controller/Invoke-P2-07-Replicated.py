#!/usr/bin/env python3
"""Three-block randomized C2, original C5, and multistage-prewarm evaluation."""
from __future__ import annotations
import argparse, csv, hashlib, importlib.util, itertools, json, math, sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ACK="RUN_P2_07_R02_THREE_ARM_EVALUATION"
SCHEDULE=[(1,1,"PREWARM"),(1,2,"C2"),(1,3,"C5"),(2,1,"C5"),(2,2,"PREWARM"),(2,3,"C2"),(3,1,"C2"),(3,2,"C5"),(3,3,"PREWARM")]

def utc(): return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def sha(p):
    h=hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""): h.update(b)
    return h.hexdigest().upper()
def require(v,m):
    if not v: raise RuntimeError(m)
def load_base(path):
    s=importlib.util.spec_from_file_location("p206base",path); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
def write_json(path,value): Path(path).write_text(json.dumps(value,indent=2)+"\n",encoding="utf-8")
def km_rmst(rows,horizon=300.0):
    obs=sorted((min(float(r["time"]),horizon),bool(r["observed"])) for r in rows)
    at_risk=len(obs); surv=1.0; area=0.0; prev=0.0
    for t in sorted({x[0] for x in obs if x[0]<horizon}):
        area+=surv*(t-prev)
        d=sum(1 for x,e in obs if x==t and e); removed=sum(1 for x,e in obs if x==t)
        if at_risk: surv*=1.0-d/at_risk
        at_risk-=removed; prev=t
    area+=surv*(horizon-prev)
    return round(area,6)
def sign_flip_p(diffs):
    observed=abs(sum(diffs)/len(diffs)); values=[]
    for signs in itertools.product((-1,1),repeat=len(diffs)):
        values.append(abs(sum(s*x for s,x in zip(signs,diffs))/len(diffs)))
    return sum(x>=observed-1e-12 for x in values)/len(values)
def selected(out,block,treatment):
    pattern=f"P2-07-R02-B{block:02d}-P*-{treatment}-A*/analysis/P2-06-pilot-summary.json"
    found=[]
    for p in (out/"episodes").glob(pattern) if (out/"episodes").exists() else []:
        try: s=json.loads(p.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError): continue
        if s.get("status")=="SCIENTIFICALLY_ADMISSIBLE": found.append((p,s))
    found.sort(key=lambda x:x[0].as_posix()); return found[0][1] if found else None
def self_test():
    require(len(SCHEDULE)==9,"SCHEDULE_SIZE")
    require(all(sum(b==x and t==y for b,p,t in SCHEDULE)==1 for x in range(1,4) for y in ("C2","C5","PREWARM")),"BALANCE")
    require(km_rmst([{"time":100,"observed":True}])==100.0,"KM_OBSERVED")
    require(km_rmst([{"time":300,"observed":False}])==300.0,"KM_CENSORED")
    require(sign_flip_p([-1,-1,-1])==0.25,"SIGN_FLIP")
    print("P2_07_R02_THREE_ARM_SELF_TEST=PASS"); print("RANDOMIZED_BLOCKS=3"); print("TREATMENTS=C2,C5,PREWARM"); print("PLANNED_EPISODES=9"); print("STRICT_300_SECOND_CENSORING=PASS"); print("DEFAULT_MODE=NO_LIVE_WORKLOAD"); return 0
def main():
    p=argparse.ArgumentParser(); p.add_argument("--self-test",action="store_true"); p.add_argument("--live",action="store_true"); p.add_argument("--acknowledge",default="")
    p.add_argument("--root"); p.add_argument("--stage9-package-root"); p.add_argument("--stage6-root"); p.add_argument("--part2-root"); p.add_argument("--output-root"); p.add_argument("--context",default="net470-v3"); p.add_argument("--namespace",default="net470-v3"); p.add_argument("--max-blocks",type=int,default=1)
    a=p.parse_args()
    if a.self_test:return self_test()
    if not a.live:
        print("P2_07_REPLICATED=DRY_RUN_ONLY"); print("LIVE_WORKLOAD_EXECUTED=NO"); return 0
    require(a.acknowledge==ACK,"LIVE_ACKNOWLEDGEMENT_MISMATCH"); require(all([a.root,a.stage9_package_root,a.stage6_root,a.part2_root,a.output_root]),"MISSING_ARGUMENT")
    base_path=Path(a.part2_root)/"Invoke-P2-06-Pilot.py"; require(base_path.is_file(),"P2_06_BASE_RUNNER_MISSING")
    base=load_base(base_path); core=Path(a.stage9_package_root)/"C5-Stage9-Episode-Core.py"; require(core.is_file(),"R07_CORE_MISSING"); require(sha(core)==base.R07_CORE_SHA256,"R07_CORE_HASH_MISMATCH")
    out=Path(a.output_root); (out/"episodes").mkdir(parents=True,exist_ok=True)
    frozen={"schema":"NET470-PART2-P2-07-R02-DESIGN-V1","created_at_utc":utc(),"design":"THREE_RANDOMIZED_COMPLETE_BLOCKS_THREE_ARMS","treatments":{"C2":"TUNED_STATIC_HPA","C5":"ORIGINAL_T1_T2_STAGE_AWARE_CONTROLLER","PREWARM":"MULTISTAGE_SELECTED_T4_T5_BOUNDED_PREWARM"},"schedule":[{"block":b,"position":pos,"treatment":t} for b,pos,t in SCHEDULE],"primary_outcome":"STABLE_RECOVERY_TIME_OR_KM_RMST300","slo_threshold_ms":123.472,"window_seconds":15,"stable_windows":8,"censor_horizon_seconds":300,"pilot_p2_06_included":False,"interrupted_r01_attempt_included":False,"t0_t8_rule":"REPORT_OBSERVED; DO_NOT IMPUTE; MARK PRE-T0 LIFECYCLE STRUCTURALLY NON_APPLICABLE","confirmatory_claim_authorized":False}
    design_path=out/"P2-07-R02-frozen-design.json"
    if design_path.exists(): require(json.loads(design_path.read_text(encoding="utf-8"))["schedule"]==frozen["schedule"],"FROZEN_SCHEDULE_CHANGED")
    else: write_json(design_path,frozen)
    completed_before={(b,t):selected(out,b,t) for b in range(1,4) for t in ("C2","C5","PREWARM")}
    pending_blocks=[b for b in range(1,4) if any(completed_before[(b,t)] is None for t in ("C2","C5","PREWARM"))]
    run_blocks=set(pending_blocks[:max(0,a.max_blocks)])
    for block,pos,treatment in SCHEDULE:
        s=selected(out,block,treatment)
        if s:
            print(f"P2_07_RESUME_SELECTED block={block} treatment={treatment} attempt={s['attempt']}",flush=True)
        elif block in run_blocks:
            ns=SimpleNamespace(root=a.root,stage9_package_root=a.stage9_package_root,stage6_root=a.stage6_root,output_root=str(out),context=a.context,namespace=a.namespace,study_prefix="P2-07-R02",attempt_label=f"B{block:02d}-P{pos}")
            base.ARGS=ns; s=base.run_arm(ns,treatment,block*10+pos); s["block"]=block; s["position"]=pos
            write_json(out/"episodes"/s["attempt"]/"analysis"/"P2-06-pilot-summary.json",s)
    summaries=[]
    for block,pos,treatment in SCHEDULE:
        s=selected(out,block,treatment)
        if s: summaries.append(s)
    if len(summaries)<9:
        completed_blocks=sum(all(selected(out,b,t) is not None for t in ("C2","C5","PREWARM")) for b in range(1,4))
        progress={"schema":"NET470-PART2-P2-07-R02-PROGRESS-V1","recorded_at_utc":utc(),"status":"IN_PROGRESS","scientifically_admissible_episodes":len(summaries),"completed_blocks":completed_blocks,"remaining_episodes":9-len(summaries),"next_step":"RERUN_P2_07_R02_TO_RESUME","failure":""}
        write_json(out/"P2-07-R02-progress.json",progress)
        print("=== P2-07 R02 PROGRESS ==="); [print(f"{k}={v}") for k,v in progress.items()]; return 0
    rows=[]
    for s in summaries:
        rows.append({"block":int(s.get("block") or s["attempt"].split("-B",1)[1].split("-",1)[0]),"position":int(s.get("position") or s["attempt"].split("-P",1)[1].split("-",1)[0]),"treatment":s["treatment"],"attempt":s["attempt"],"request_rows":s["request_rows"],"completed_requests":s["completed_requests"],"error_requests":s["error_requests"],"observed":bool(s["stable_recovery_observed"]),"right_censored":bool(s["right_censored"]),"time":float(s["recovery_seconds_or_rmst300_contribution"]),"clock_uncertainty_ms":s["maximum_clock_mapping_uncertainty_ms"],"hpa_restored":bool(s["hpa_exactly_restored"]),"prewarm_before_t0":bool(s["prewarm_before_t0"])})
    dataset=out/"P2-07-R02-primary-dataset.csv"
    with dataset.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    pairs=[]
    for b in range(1,4):
        c=next(x for x in rows if x["block"]==b and x["treatment"]=="C2"); f=next(x for x in rows if x["block"]==b and x["treatment"]=="C5"); q=next(x for x in rows if x["block"]==b and x["treatment"]=="PREWARM")
        pairs.append({"block":b,"c2_seconds":c["time"],"c5_seconds":f["time"],"prewarm_seconds":q["time"],"c5_minus_c2_seconds":round(f["time"]-c["time"],6),"prewarm_minus_c2_seconds":round(q["time"]-c["time"],6),"prewarm_minus_c5_seconds":round(q["time"]-f["time"],6)})
    pair_path=out/"P2-07-R02-paired-differences.csv"
    with pair_path.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(pairs[0]));w.writeheader();w.writerows(pairs)
    c2=[{"time":x["time"],"observed":x["observed"]} for x in rows if x["treatment"]=="C2"]; c5=[{"time":x["time"],"observed":x["observed"]} for x in rows if x["treatment"]=="C5"]; pw=[{"time":x["time"],"observed":x["observed"]} for x in rows if x["treatment"]=="PREWARM"]
    d_c5=[x["c5_minus_c2_seconds"] for x in pairs]; d_pw=[x["prewarm_minus_c2_seconds"] for x in pairs]; d_pw_c5=[x["prewarm_minus_c5_seconds"] for x in pairs]
    c2_rmst=km_rmst(c2); c5_rmst=km_rmst(c5); pw_rmst=km_rmst(pw)
    result={"schema":"NET470-PART2-P2-07-R02-RESULT-V1","recorded_at_utc":utc(),"status":"VERIFIED_PASS","design":"THREE_RANDOMIZED_COMPLETE_BLOCKS_THREE_ARMS_EXPLORATORY","episodes":9,"blocks":3,"c2_observed_recoveries":sum(x["observed"] for x in c2),"c5_observed_recoveries":sum(x["observed"] for x in c5),"prewarm_observed_recoveries":sum(x["observed"] for x in pw),"c2_km_rmst300_seconds":c2_rmst,"c5_km_rmst300_seconds":c5_rmst,"prewarm_km_rmst300_seconds":pw_rmst,"c5_minus_c2_km_rmst300_seconds":round(c5_rmst-c2_rmst,6),"prewarm_minus_c2_km_rmst300_seconds":round(pw_rmst-c2_rmst,6),"prewarm_minus_c5_km_rmst300_seconds":round(pw_rmst-c5_rmst,6),"mean_c5_minus_c2_block_difference_seconds":round(sum(d_c5)/3,6),"mean_prewarm_minus_c2_block_difference_seconds":round(sum(d_pw)/3,6),"mean_prewarm_minus_c5_block_difference_seconds":round(sum(d_pw_c5)/3,6),"c5_vs_c2_exact_sign_flip_p_exploratory":sign_flip_p(d_c5),"prewarm_vs_c2_exact_sign_flip_p_exploratory":sign_flip_p(d_pw),"prewarm_vs_c5_exact_sign_flip_p_exploratory":sign_flip_p(d_pw_c5),"minimum_attainable_two_sided_p_value_with_three_pairs":0.25,"superiority_claim_supported":False,"interpretation":"NEGATIVE_DIFFERENCE_FAVORS FIRST-NAMED ADAPTIVE ARM; THREE PAIRS ARE EXPLORATORY","pilot_p2_06_included":False,"interrupted_r01_attempt_included":False,"strict_300_second_censoring":True,"stage9_modified":False,"stage10_modified":False,"failure":""}
    write_json(out/"P2-07-R02-result-summary.json",result)
    freeze=out/"P2-07-R02-FREEZE.txt"; freeze.write_text("\n".join(f"{k}={v}" for k,v in result.items())+f"\nDESIGN_SHA256={sha(design_path)}\nDATASET_SHA256={sha(dataset)}\nPAIRS_SHA256={sha(pair_path)}\n",encoding="utf-8")
    print("=== P2-07 R02 THREE-ARM SUMMARY ==="); [print(f"{k}={v}") for k,v in result.items()]; print(f"FREEZE_SHA256={sha(freeze)}")
    return 0
if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as e:
        print(f"P2_07_FAILURE={type(e).__name__}:{e}",file=sys.stderr); raise
