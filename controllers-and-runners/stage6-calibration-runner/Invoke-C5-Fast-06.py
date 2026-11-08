#!/usr/bin/env python3
from __future__ import annotations

import argparse, ast, csv, hashlib, json, math, os, statistics, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

STAGE5_PROOF_SHA = "1BD7A487FD4A0C06DAEF7C504D239CCF6EA6B51367BCFC7E5D8469130CB8D620"
STAGE5_BINDING_SHA = "F8D59C55902AA59193C6AC63DAED61655B93258A37A6E88D2C40CCA43733FBDB"
EXPECTED = 6450
REQUIRED = 10

def now(): return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def dt(v): return datetime.fromisoformat(v.replace("Z", "+00:00"))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest().upper()
def require(v, m):
    if not v: raise RuntimeError(m)
def load_json(p): return json.loads(p.read_text(encoding="utf-8"))
def read_csv(p):
    with p.open(encoding="utf-8-sig", newline="") as h: return list(csv.DictReader(h))
def write_csv(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with p.open("w", encoding="utf-8", newline="") as h:
        w=csv.DictWriter(h, fieldnames=fields, lineterminator="\n"); w.writeheader(); w.writerows(rows)
def parse_pods(v):
    try: return ast.literal_eval(v) if v else []
    except Exception: return []

def normalize(attempt: Path) -> dict:
    summary=load_json(attempt/"analysis"/"C5-FAST-03-pilot-summary.json")
    events=load_json(attempt/"analysis"/"T0-T8-events.json")
    samples=sorted(read_csv(attempt/"telemetry"/"cluster-runtime.csv"), key=lambda x: dt(x["observed_at_utc"]))
    samples=[x for x in samples if x.get("observed_at_utc") and not x.get("sampler_error")]
    t0=dt(events["T0"])
    t1s=next((x for x in samples if dt(x["observed_at_utc"])>=t0 and x.get("hpa_cpu_percent") not in (None,"") and int(x["hpa_cpu_percent"])>=55),None)
    t2s=next((x for x in samples if dt(x["observed_at_utc"])>=t0 and int(x.get("hpa_desired_replicas") or 0)>1),None)
    require(t1s and t2s,"T1_OR_T2_OBSERVATION_MISSING")
    t2_native=events.get("T2")
    require(t2_native,"T2_NATIVE_MISSING")
    # The metrics field returned by a later collector poll can be stale even
    # after the HPA has acted. Bound T1 using only negative observations whose
    # collector timestamp is no later than the HPA's native action timestamp.
    # The HPA action itself proves threshold evaluation occurred by T2.
    negative_before_t2=[x for x in samples if dt(x["observed_at_utc"])<=dt(t2_native) and dt(x["observed_at_utc"])>=t0 and (x.get("hpa_cpu_percent") in (None,"") or int(x["hpa_cpu_percent"])<55)]
    lower=(negative_before_t2[-1]["observed_at_utc"] if negative_before_t2 else events["T0"])
    keys=["T0","T2","T3","T4","T5","T6","T7","T8"]
    require(all(events.get(k) for k in keys),"T0_T8_SOURCE_EVENT_MISSING")
    require(all(dt(events[a])<=dt(events[b]) for a,b in zip(keys,keys[1:])),"T2_T8_NATIVE_ORDER_FAILED")
    require(dt(lower)<=dt(t2_native),"T1_INTERVAL_INCOMPATIBLE")
    d={
      "T0_T1_seconds_lower_exclusive":round((dt(lower)-dt(events["T0"])).total_seconds(),6),
      "T0_T1_seconds_upper":round((dt(t2_native)-dt(events["T0"])).total_seconds(),6),
      "T1_T2_seconds_lower":0.0,
      "T1_T2_seconds_upper_exclusive":round((dt(t2_native)-dt(lower)).total_seconds(),6),
    }
    for a,b in zip(keys[1:-1],keys[2:]): d[f"{a}_{b}_seconds"]=round((dt(events[b])-dt(events[a])).total_seconds(),6)
    d["T0_T2_seconds"]=round((dt(t2_native)-dt(events["T0"])).total_seconds(),6)
    d["T0_T8_seconds"]=round((dt(events["T8"])-dt(events["T0"])).total_seconds(),6)
    d["T2_T8_decomposition_sum_seconds"]=round(sum(d[f"{a}_{b}_seconds"] for a,b in zip(keys[1:-1],keys[2:])),6)
    require(abs(d["T0_T2_seconds"]+d["T2_T8_decomposition_sum_seconds"]-d["T0_T8_seconds"])<1e-5,"DECOMPOSITION_FAILED")
    accepted=(int(summary.get("request_rows",-1))==EXPECTED and not summary.get("technical_failure") and summary.get("hpa_spec_restored") is True and float(summary.get("maximum_clock_mapping_uncertainty_ms",999999))<=500)
    result={
      "schema":"C5-FAST-06-C2-CALIBRATION-EPISODE-V1","status":"ACCEPTED" if accepted else "EXCLUDED",
      "recorded_at_utc":now(),"execution_attempt_id":summary.get("execution_attempt_id"),"workload_run_id":summary.get("workload_run_id"),
      "request_rows":summary.get("request_rows"),"completed_requests":summary.get("completed_requests"),"error_requests":summary.get("error_requests"),
      "events_source":events,"t1_interval":{"lower_bound_exclusive_utc":lower,"upper_bound_inclusive_utc":t2_native,"first_positive_collector_observation_utc":t1s["observed_at_utc"]},
      "durations":d,"timestamp_order_pass":True,"timestamp_method":"INTERVAL_CENSORED_T1_PLUS_NATIVE_T2_T8_CAUSAL_CHAIN",
      "maximum_clock_mapping_uncertainty_ms":summary.get("maximum_clock_mapping_uncertainty_ms"),"hpa_spec_restored":summary.get("hpa_spec_restored"),
      "accepted":accepted,"technical_failure":"" if accepted else (summary.get("technical_failure") or "CALIBRATION_ACCEPTANCE_FAILED")}
    (attempt/"analysis"/"C5-FAST-06-normalized-summary.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result

def median_mad(values):
    med=statistics.median(values); mad=statistics.median([abs(x-med) for x in values]); return med,mad,med+3*mad

def aggregate(runroot: Path, accepted: list[tuple[int,Path,dict]]):
    stage_names=["T0_T1","T1_T2","T2_T3","T3_T4","T4_T5","T5_T6","T6_T7","T7_T8"]
    rows=[]
    for episode,path,r in accepted:
        d=r["durations"]
        row={"episode_number":episode,"execution_attempt_id":r["execution_attempt_id"],"completed_requests":r["completed_requests"],"error_requests":r["error_requests"],"T0_T8_seconds":d["T0_T8_seconds"]}
        row.update({"T0_T1_seconds":d["T0_T1_seconds_upper"],"T1_T2_seconds":d["T1_T2_seconds_upper_exclusive"]})
        for n in stage_names[2:]: row[f"{n}_seconds"]=d[f"{n}_seconds"]
        rows.append(row)
    write_csv(runroot/"C5-C2-calibration-primary-dataset.csv",rows)
    budgets=[]
    for n in stage_names:
        vals=[float(r[f"{n}_seconds"]) for r in rows]; med,mad,budget=median_mad(vals)
        budgets.append({"stage":n,"n":len(vals),"median_seconds":round(med,6),"mad_seconds":round(mad,6),"budget_seconds_median_plus_3mad":round(budget,6),"minimum_seconds":round(min(vals),6),"maximum_seconds":round(max(vals),6)})
    write_csv(runroot/"C5-C2-stage-budget-calibration.csv",budgets)
    return rows,budgets

def accepted_attempts(runroot: Path):
    out=[]
    for p in sorted((runroot/"episodes").glob("C5-CAL-C2-E*-A*")) if (runroot/"episodes").exists() else []:
        q=p/"analysis"/"C5-FAST-06-normalized-summary.json"
        if q.is_file():
            r=load_json(q)
            if r.get("accepted"):
                ep=int(p.name.split("-E")[1].split("-")[0]); out.append((ep,p,r))
    unique={}
    for item in out: unique.setdefault(item[0],item)
    return [unique[k] for k in sorted(unique)]

def recover_existing_attempts(runroot: Path):
    episodes=runroot/"episodes"
    if not episodes.exists(): return
    for p in sorted(episodes.glob("C5-CAL-C2-E*-A*")):
        source=p/"analysis"/"C5-FAST-03-pilot-summary.json"
        normalized=p/"analysis"/"C5-FAST-06-normalized-summary.json"
        if source.is_file() and not normalized.is_file():
            try:
                result=normalize(p)
                print(f"CALIBRATION_ATTEMPT_OFFLINE_RECOVERED id={p.name} accepted={result.get('accepted')}",flush=True)
            except Exception as exc:
                print(f"CALIBRATION_ATTEMPT_OFFLINE_RECOVERY_SKIPPED id={p.name} failure={type(exc).__name__}:{exc}",flush=True)

def self_test():
    require(EXPECTED==6450 and REQUIRED==10,"CONSTANT_TEST")
    med,mad,b=median_mad([1,1,2,2,3]); require((med,mad,b)==(2,1,5),"MAD_TEST")
    t0=dt("2026-01-01T00:00:00Z"); t2=dt("2026-01-01T00:00:09Z")
    polls=[("2026-01-01T00:00:08Z",21),("2026-01-01T00:00:11Z",21),("2026-01-01T00:00:14Z",76)]
    eligible=[x for x in polls if t0<=dt(x[0])<=t2 and x[1]<55]
    require(eligible[-1][0]=="2026-01-01T00:00:08Z","STALE_POLL_BOUNDARY_TEST")
    print("C5_CLASSFAST_06_R02_SELF_TEST=PASS")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--self-test",action="store_true"); ap.add_argument("--root"); ap.add_argument("--package-root"); ap.add_argument("--run-root"); ap.add_argument("--max-episodes",type=int,default=1); ap.add_argument("--context",default="net470-v3"); ap.add_argument("--namespace",default="net470-v3"); a=ap.parse_args()
    if a.self_test: self_test(); return 0
    require(a.root and a.package_root and a.run_root,"MISSING_ARGUMENT")
    package=Path(a.package_root).resolve(); runroot=Path(a.run_root).resolve(); root=Path(a.root).resolve()
    proof=package/"evidence"/"C5-FAST-05-R01-VERIFIED-PASS.zip"; require(proof.is_file() and sha(proof)==STAGE5_PROOF_SHA,"STAGE5_PROOF_HASH_MISMATCH")
    runroot.mkdir(parents=True,exist_ok=True); (runroot/"episodes").mkdir(exist_ok=True)
    state=runroot/"resume-state.json"
    recover_existing_attempts(runroot)
    accepted=accepted_attempts(runroot); completed={x[0] for x in accepted}; executed=0
    while len(completed)<REQUIRED and executed<a.max_episodes:
        ep=next(i for i in range(1,REQUIRED+1) if i not in completed)
        existing=list((runroot/"episodes").glob(f"C5-CAL-C2-E{ep:02d}-A*")); attempt=len(existing)+1
        aid=f"C5-CAL-C2-E{ep:02d}-A{attempt:02d}"; rid=f"V3-K-C5-CAL-C2-E{ep:02d}-A{attempt:02d}"; out=runroot/"episodes"/aid
        print(f"CALIBRATION_EPISODE_START episode={ep} attempt={attempt} id={aid}",flush=True)
        env=os.environ.copy(); env["C5_STAGE6_EPISODE_ID"]=aid; env["C5_STAGE6_RUN_ID"]=rid
        cmd=[sys.executable,str(package/"C5-Stage6-Episode-Core.py"),"--root",str(root),"--package-root",str(package),"--output",str(out),"--context",a.context,"--namespace",a.namespace]
        proc=subprocess.run(cmd,env=env,text=True); executed+=1
        try: result=normalize(out)
        except Exception as exc:
            result={"accepted":False,"technical_failure":f"{type(exc).__name__}:{exc}"}
            (out/"analysis").mkdir(parents=True,exist_ok=True); (out/"analysis"/"C5-FAST-06-normalization-failure.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        print(f"CALIBRATION_EPISODE_END id={aid} accepted={result.get('accepted')} core_exit={proc.returncode} failure={result.get('technical_failure','')}",flush=True)
        accepted=accepted_attempts(runroot); completed={x[0] for x in accepted}
        state.write_text(json.dumps({"updated_at_utc":now(),"accepted_episodes":sorted(completed),"accepted_count":len(completed),"remaining":REQUIRED-len(completed)},indent=2),encoding="utf-8")
        if not result.get("accepted"): raise RuntimeError(f"CALIBRATION_EPISODE_PRESERVED_NOT_ACCEPTED:{aid}:{result.get('technical_failure','')}")
    accepted=accepted_attempts(runroot)
    if len(accepted)<REQUIRED:
        print("=== C5 FAST TRACK STAGE 6 PROGRESS ==="); print("STEP=C5-FAST-06-R01\nSTATUS=IN_PROGRESS"); print(f"COMPLETED_ACCEPTED_CALIBRATION_EPISODES={len(accepted)}"); print(f"REMAINING_CALIBRATION_EPISODES={REQUIRED-len(accepted)}"); print("NEXT_STEP=RERUN_C5_FAST_06_R01_TO_RESUME\nFAILURE="); return 0
    rows,budgets=aggregate(runroot,accepted)
    record={"STEP":"C5-FAST-06-R01","STATUS":"VERIFIED_PASS","RECORDED_AT_UTC":now(),"PROJECT_SCOPE":"C5_C2_T0_T8_STAGE_BUDGET_CALIBRATION","STAGE5_PROOF_SHA256":STAGE5_PROOF_SHA,"STAGE5_BINDING_SHA256":STAGE5_BINDING_SHA,"CALIBRATION_TREATMENT":"C2_TUNED_STATIC_HPA","ACCEPTED_CALIBRATION_EPISODES":10,"TOTAL_SCHEDULED_REQUESTS":64500,"TOTAL_COMPLETED_REQUESTS":sum(int(x[2]["completed_requests"]) for x in accepted),"TOTAL_ERROR_REQUESTS":sum(int(x[2]["error_requests"]) for x in accepted),"T0_T8_ALL_OBSERVED":"YES","TIMESTAMP_METHOD":"INTERVAL_CENSORED_T1_PLUS_NATIVE_T2_T8_CAUSAL_CHAIN","BUDGET_ESTIMATOR":"MEDIAN_PLUS_3_MAD","HPA_RESTORED_AFTER_EVERY_EPISODE":"YES","GUIDE11_DATASET_MODIFIED":"NO","WORKLOAD_PROTOCOL_MODIFIED":"NO","REQUEST_RETRIES_ADDED":"NO","LIVE_WORKLOAD_EXECUTED_BY_THIS_STEP":"YES","C5_STAGE6_COMPLETE":"YES","C5_ACTIONS_AUTHORIZED":"NO_PENDING_STAGE7_ACTION_PILOTS","C5_FORMAL_EXECUTION_AUTHORIZED":"NO","NEXT_STEP":"C5_FAST_07_C5_ACTION_AND_ROLLBACK_QUALIFICATION_PILOTS","FAILURE":""}
    rec=runroot/"C5-FAST-06-R01-record.txt"; rec.write_text("=== C5 FAST TRACK STAGE 6 SUMMARY ===\n"+"\n".join(f"{k}={v}" for k,v in record.items())+"\n",encoding="utf-8")
    freeze=runroot/"C5-FAST-06-R01-C2-CALIBRATION-FREEZE.txt"; freeze.write_text(rec.read_text(encoding="utf-8")+f"PRIMARY_DATASET_SHA256={sha(runroot/'C5-C2-calibration-primary-dataset.csv')}\nSTAGE_BUDGETS_SHA256={sha(runroot/'C5-C2-stage-budget-calibration.csv')}\nRECORD_SHA256={sha(rec)}\n",encoding="utf-8")
    print(rec.read_text(encoding="utf-8"),end=""); print(f"RECORD_PATH={rec}\nRECORD_SHA256={sha(rec)}\nFREEZE_PATH={freeze}\nFREEZE_SHA256={sha(freeze)}")
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc: print(f"C5_CLASSFAST_06_R01_FAILURE={exc}",file=sys.stderr); raise
