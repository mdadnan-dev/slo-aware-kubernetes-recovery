#!/usr/bin/env python3
"""Guarded two-arm pilot: C2 baseline versus bounded T4-T5 prewarm.

The frozen Stage-9 R07 episode core remains the single authoritative workload
and telemetry collector.  For the prewarm arm, this runner applies minReplicas
2 during the low-precondition phase, records two available replicas, restores
the exact original HPA specification at the low-recovery boundary and again in
finally, and analyzes stable SLO recovery without inventing post-T0 lifecycle
events for a pod intentionally created before T0.
"""
from __future__ import annotations
import argparse, csv, hashlib, importlib.util, json, os, subprocess, sys, threading, time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

EXPECTED = 6450
ACTION = "T4_T5_BOUNDED_PREWARM_MIN_2"
ACK = "RUN_P2_06_C2_AND_PREWARM_PILOT"
R07_CORE_SHA256 = "6410C56F18AB0DA8F8429CF5671B58C0B5DA7747A1528B95B98BFB98604788C5"

def utc(): return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def dt(v): return datetime.fromisoformat(str(v).replace("Z","+00:00"))
def canon(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",", ":")).encode()).hexdigest().upper()
def file_sha(p):
    h=hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""): h.update(b)
    return h.hexdigest().upper()
def require(v,m):
    if not v: raise RuntimeError(m)
def run(cmd,timeout=90,check=True,env=None):
    p=subprocess.run(cmd,text=True,capture_output=True,timeout=timeout,env=env,encoding="utf-8",errors="replace")
    if check and p.returncode: raise RuntimeError(f"COMMAND_FAILED:{p.returncode}:{cmd}:{p.stderr.strip()}")
    return p
def kube(c,n,args): return json.loads(run(["kubectl","--context",c,"-n",n,*args,"-o","json"]).stdout)
def hpa_spec(c,n): return kube(c,n,["get","hpa","processing-hpa"])["spec"]
def patch_spec(c,n,s): run(["kubectl","--context",c,"-n",n,"patch","hpa","processing-hpa","--type=merge","-p",json.dumps({"spec":s})])
def cpu_target(spec):
    xs=[m.get("resource",{}).get("target",{}).get("averageUtilization") for m in spec.get("metrics",[]) if m.get("type")=="Resource" and m.get("resource",{}).get("name")=="cpu"]
    require(len(xs)==1,"EXPECTED_ONE_CPU_METRIC"); return int(xs[0])
def write_json(p,v): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,indent=2)+"\n",encoding="utf-8")
def load_module(path,name):
    s=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
def unique(root,name):
    xs=list(Path(root).rglob(name)); require(len(xs)==1,f"EXPECTED_ONE_{name}:{len(xs)}"); return xs[0]
def t1t2_budget(stage6_root):
    record=unique(stage6_root,"C5-FAST-06-R01-record.txt")
    require(file_sha(record)=="B6807B337A7695F00F76EBE6750A910B70056C3A33C905DD9CBB84549E395E1A","STAGE6_RECORD_HASH_MISMATCH")
    rows=list(csv.DictReader(unique(stage6_root,"C5-C2-stage-budget-calibration.csv").open(encoding="utf-8-sig")))
    row=next((x for x in rows if x.get("stage")=="T1_T2"),None); require(row is not None,"T1_T2_BUDGET_MISSING")
    return max(0.0,float(row["budget_seconds_median_plus_3mad"]))
def read_json_lines(p):
    rows=[]
    if p.is_file():
        for line in p.read_text(encoding="utf-8",errors="replace").splitlines():
            try: rows.append(json.loads(line))
            except json.JSONDecodeError: pass
    return rows
def completed_count(attempt):
    p=attempt/"workload"/"request-results.csv"
    if not p.is_file(): return (0,0,0)
    rows=list(csv.DictReader(p.open(encoding="utf-8-sig")))
    done=sum(str(x.get("outcome","")).lower()=="completed" for x in rows)
    return len(rows),done,len(rows)-done
def snapshot(c,n):
    h=kube(c,n,["get","hpa","processing-hpa"]); d=kube(c,n,["get","deployment","processing"])
    return {"captured_at_utc":utc(),"hpa_spec":h["spec"],"hpa_status":h.get("status",{}),"deployment_status":d.get("status",{})}

def run_core(cmd,attempt,logs,env):
    logs.mkdir(parents=True,exist_ok=True)
    out=logs/f"{attempt.name}.core.stdout.log"; err=logs/f"{attempt.name}.core.stderr.log"
    flags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0
    with out.open("w",encoding="utf-8") as oh,err.open("w",encoding="utf-8") as eh:
        p=subprocess.Popen(cmd,text=True,stdout=oh,stderr=eh,env=env,creationflags=flags)
        try: code=p.wait(timeout=2400); timed=False
        except subprocess.TimeoutExpired:
            timed=True
            if os.name=="nt": subprocess.run(["taskkill","/PID",str(p.pid),"/T","/F"],capture_output=True,text=True)
            else: p.kill()
            try: p.wait(timeout=30)
            except subprocess.TimeoutExpired: pass
            code=124
    return SimpleNamespace(returncode=code,timed_out=timed,stdout=str(out),stderr=str(err))

def prewarm_watch(a,attempt,run_id,original,original_hash,stop,ledger):
    acted=False; restored=False; qualified=False
    try:
        live=attempt/"telemetry"/"canonical"/run_id/"live_request_results.jsonl"
        while not stop.wait(.25):
            rows=read_json_lines(live)
            phase=rows[-1].get("phase_id") if rows else None
            if phase=="low-precondition" and not acted:
                changed=deepcopy(original); changed["minReplicas"]=2
                patch_spec(a.context,a.namespace,changed); acted=True
                ledger.append({"at_utc":utc(),"event":"PREWARM_PATCH_APPLIED","action":ACTION,"minReplicas":2})
            if acted and not qualified:
                s=snapshot(a.context,a.namespace); hs=s["hpa_status"]; ds=s["deployment_status"]
                if int(hs.get("currentReplicas",0))>=2 and int(hs.get("desiredReplicas",0))>=2 and int(ds.get("availableReplicas",0))>=2:
                    qualified=True; write_json(attempt/"analysis"/"prewarm-qualified.json",s)
                    ledger.append({"at_utc":utc(),"event":"PREWARM_TWO_AVAILABLE","availableReplicas":ds.get("availableReplicas")})
            if phase in ("low-recovery","post-observation") and acted and not restored:
                patch_spec(a.context,a.namespace,original); restored=True
                ledger.append({"at_utc":utc(),"event":"LOW_RECOVERY_RESTORE","restored_spec_sha256":canon(hpa_spec(a.context,a.namespace))})
                return
    finally:
        try:
            patch_spec(a.context,a.namespace,original)
            ledger.append({"at_utc":utc(),"event":"FINALLY_RESTORE","restored_spec_sha256":canon(hpa_spec(a.context,a.namespace))})
        except Exception as e:
            ledger.append({"at_utc":utc(),"event":"RESTORE_FAILURE","failure":f"{type(e).__name__}:{e}"})

def c5_watch(a,attempt,run_id,original,stop,ledger):
    module_path=Path(a.stage9_package_root)/"c5_production_controller_v1.py"; controller=load_module(module_path,"p207c5").Controller({"T1_T2":a.c5_t1t2_budget})
    live=attempt/"telemetry"/"canonical"/run_id/"live_request_results.jsonl"; positive=None; last_call=0.0; acted=False
    try:
        while not stop.wait(.25):
            rows=read_json_lines(live); phase=rows[-1].get("phase_id") if rows else None
            if phase in ("low-recovery","post-observation"):
                if acted:
                    patch_spec(a.context,a.namespace,original); ledger.append({"at_utc":utc(),"event":"C5_LOW_RECOVERY_RESTORE","restored_spec_sha256":canon(hpa_spec(a.context,a.namespace))})
                return
            if phase!="high-challenge" or time.monotonic()-last_call<15: continue
            last_call=time.monotonic(); s=snapshot(a.context,a.namespace); hs=s["hpa_status"]
            cpu=None
            for m in hs.get("currentMetrics",[]):
                if m.get("type")=="Resource" and m.get("resource",{}).get("name")=="cpu": cpu=m.get("resource",{}).get("current",{}).get("averageUtilization")
            desired=int(hs.get("desiredReplicas",0)); trigger=cpu is not None and float(cpu)>=55 and desired>1
            if trigger and positive is None: positive=time.monotonic()
            if not trigger: positive=None
            decision=controller.observe({"high_challenge_started":True,"safety_ok":True,"cpu_percent":cpu,"desired_replicas":desired,"metric_positive_elapsed_seconds":0 if positive is None else time.monotonic()-positive})
            entry={"at_utc":utc(),"event":"C5_CONTROLLER_OBSERVATION","cpu_percent":cpu,"desired_replicas":desired,**decision}; ledger.append(entry)
            if decision.get("authorized"):
                changed=deepcopy(original); changed["minReplicas"]=int(decision["target_min_replicas"]); patch_spec(a.context,a.namespace,changed); acted=True
                ledger.append({"at_utc":utc(),"event":"C5_ACTION_APPLIED","action":decision["action"],"target_min_replicas":decision["target_min_replicas"]})
    except Exception as e:
        ledger.append({"at_utc":utc(),"event":"C5_CONTROLLER_FAILURE","failure":f"{type(e).__name__}:{e}"})
    finally:
        try:
            patch_spec(a.context,a.namespace,original); ledger.append({"at_utc":utc(),"event":"C5_FINALLY_RESTORE","restored_spec_sha256":canon(hpa_spec(a.context,a.namespace))})
        except Exception as e: ledger.append({"at_utc":utc(),"event":"RESTORE_FAILURE","failure":f"{type(e).__name__}:{e}"})

def analyze(attempt,treatment,original_hash,core_result,ledger):
    core_path=attempt/"analysis"/"C5-FAST-03-pilot-summary.json"
    core=json.loads(core_path.read_text(encoding="utf-8")) if core_path.is_file() else {}
    rows,done,errors=completed_count(attempt); events=core.get("events") or {}; durations=core.get("durations") or {}
    uncertainty=core.get("maximum_clock_mapping_uncertainty_ms")
    hpa_ok=canon(hpa_spec(ARGS.context,ARGS.namespace))==original_hash
    action_applied=any(x.get("event")=="PREWARM_PATCH_APPLIED" for x in ledger)
    action_qualified=any(x.get("event")=="PREWARM_TWO_AVAILABLE" for x in ledger)
    action_time=next((x.get("at_utc") for x in ledger if x.get("event")=="PREWARM_TWO_AVAILABLE"),None)
    t0=events.get("T0")
    action_before_t0=bool(action_time and t0 and action_time<=t0)
    tb,tr=events.get("TB"),events.get("TR"); late_recovery=round((dt(tr)-dt(tb)).total_seconds(),6) if tb and tr else None
    observed=bool(tb and tr and late_recovery<=300.0)
    no_breach=not tb
    censored=bool(tb and (not tr or late_recovery>300.0))
    recovery=late_recovery if observed else (300.0 if (no_breach or censored) else None)
    base=(not core_result.timed_out and rows==EXPECTED and hpa_ok and uncertainty is not None and float(uncertainty)<=500 and not core.get("technical_failure"))
    c5_failed=any(x.get("event") in ("C5_CONTROLLER_FAILURE","RESTORE_FAILURE") for x in ledger)
    c5_observed=any(x.get("event")=="C5_CONTROLLER_OBSERVATION" for x in ledger)
    action_ok=(treatment=="C2") or (treatment=="C5" and c5_observed and not c5_failed) or (treatment=="PREWARM" and action_applied and action_qualified and action_before_t0)
    admissible=bool(base and action_ok and (observed or no_breach or censored))
    lifecycle_note=("PREWARM_POD_CREATED_BEFORE_T0; T3-T7 MAY_BE STRUCTURALLY NON_APPLICABLE AND ARE NOT IMPUTED" if treatment=="PREWARM" else "POST_T0_SCALING_LIFECYCLE_REPORTED_WHEN OBSERVED; MISSING EVENTS ARE NOT IMPUTED")
    summary={"schema":"NET470-PART2-PILOT-EPISODE-V2","recorded_at_utc":utc(),"treatment":treatment,"attempt":attempt.name,"status":"SCIENTIFICALLY_ADMISSIBLE" if admissible else "PRESERVED_NOT_ADMISSIBLE","scheduled_requests":EXPECTED,"request_rows":rows,"completed_requests":done,"error_requests":errors,"events":events,"durations":durations,"stable_recovery_observed_within_300_seconds":observed,"stable_recovery_observed":observed,"late_recovery_seconds_if_any":late_recovery,"right_censored":not observed,"censor_reason":None if observed else ("RECOVERY_AFTER_300_SECONDS" if late_recovery and late_recovery>300 else ("RECOVERY_NOT_OBSERVED_BY_300_SECONDS" if tb else "NO_QUALIFYING_BREACH")),"recovery_seconds_or_rmst300_contribution":recovery,"maximum_clock_mapping_uncertainty_ms":uncertainty,"prewarm_action_applied":action_applied,"prewarm_two_available":action_qualified,"prewarm_qualified_at_utc":action_time,"prewarm_before_t0":action_before_t0,"c5_action_count":sum(x.get("event")=="C5_ACTION_APPLIED" for x in ledger),"hpa_exactly_restored":hpa_ok,"core_exit_code":core_result.returncode,"core_timed_out":core_result.timed_out,"core_pilot_qualified":core.get("pilot_qualified"),"t0_t8_disclosure":lifecycle_note,"workload_protocol_modified":False,"request_retries_added":False,"stage9_modified":False,"stage10_modified":False,"failure":"" if admissible else "PILOT_ACCEPTANCE_FAILED"}
    write_json(attempt/"analysis"/"P2-06-pilot-summary.json",summary); write_json(attempt/"analysis"/"P2-06-action-ledger.json",ledger)
    return summary

def run_arm(a,treatment,index):
    original=deepcopy(hpa_spec(a.context,a.namespace)); original_hash=canon(original)
    require(int(original.get("minReplicas",-1))==1 and int(original.get("maxReplicas",-1))==5 and cpu_target(original)==55,"BASELINE_HPA_NOT_1_5_55")
    dep=kube(a.context,a.namespace,["get","deployment","processing"]); require(int(dep.get("status",{}).get("availableReplicas",0))==1,"PROCESSING_BASELINE_NOT_ONE_AVAILABLE")
    attempt_no=1
    study_prefix=getattr(a,"study_prefix","P2-06")
    attempt_label=getattr(a,"attempt_label",f"{index:02d}")
    while True:
        attempt=Path(a.output_root)/"episodes"/f"{study_prefix}-{attempt_label}-{treatment}-A{attempt_no:02d}"
        if not attempt.exists(): break
        attempt_no+=1
    episode_id=f"C5-{study_prefix}-{attempt_label}-{treatment}-A{attempt_no:02d}"
    run_id=f"V3-K-{episode_id}"
    ledger=[{"at_utc":utc(),"event":"BASELINE_VERIFIED","original_spec_sha256":original_hash}]
    stop=threading.Event(); thread=None
    if treatment=="PREWARM":
        thread=threading.Thread(target=prewarm_watch,args=(a,attempt,run_id,original,original_hash,stop,ledger),daemon=True); thread.start()
    elif treatment=="C5":
        require(getattr(a,"stage6_root",None),"STAGE6_ROOT_REQUIRED_FOR_C5")
        module_path=Path(a.stage9_package_root)/"c5_production_controller_v1.py"; require(module_path.is_file(),"C5_CONTROLLER_MISSING"); require(file_sha(module_path)=="D0F9FE6463C0326735813DC20F542F6AF73A9C8E53ADA5A8EAA26D97079118A2","C5_CONTROLLER_HASH_MISMATCH")
        a.c5_t1t2_budget=t1t2_budget(a.stage6_root)
        thread=threading.Thread(target=c5_watch,args=(a,attempt,run_id,original,stop,ledger),daemon=True); thread.start()
    env=os.environ.copy(); env["C5_STAGE6_EPISODE_ID"]=episode_id; env["C5_STAGE6_RUN_ID"]=run_id; env["C5_FORMAL_CPU_TARGET"]="55"
    print(f"P2_06_EPISODE_START treatment={treatment} id={attempt.name}",flush=True)
    try:
        result=run_core([sys.executable,str(Path(a.stage9_package_root)/"C5-Stage9-Episode-Core.py"),"--root",a.root,"--package-root",a.stage9_package_root,"--output",str(attempt),"--context",a.context,"--namespace",a.namespace],attempt,Path(a.output_root)/"runner-logs",env)
    finally:
        stop.set()
        if thread: thread.join(timeout=90)
        patch_spec(a.context,a.namespace,original)
    s=analyze(attempt,treatment,original_hash,result,ledger)
    print(f"P2_06_EPISODE_END treatment={treatment} status={s['status']} recovery={s['recovery_seconds_or_rmst300_contribution']} errors={s['error_requests']}",flush=True)
    require(s["status"]=="SCIENTIFICALLY_ADMISSIBLE",f"PILOT_EPISODE_PRESERVED_NOT_ADMISSIBLE:{attempt.name}")
    return s

def existing_admissible(output_root,treatment):
    candidates=[]
    for p in (Path(output_root)/"episodes").glob(f"*-{treatment}-A*/analysis/P2-06-pilot-summary.json") if (Path(output_root)/"episodes").exists() else []:
        try: s=json.loads(p.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError): continue
        if s.get("treatment")==treatment and s.get("status")=="SCIENTIFICALLY_ADMISSIBLE": candidates.append((p,s))
    candidates.sort(key=lambda x:x[0].as_posix())
    return candidates[0][1] if candidates else None

def self_test():
    synthetic=[{"outcome":"completed"} for _ in range(9)]+[{"outcome":"error"}]
    assert sum(x["outcome"]=="completed" for x in synthetic)==9
    s={"minReplicas":1,"maxReplicas":5,"metrics":[{"type":"Resource","resource":{"name":"cpu","target":{"type":"Utilization","averageUtilization":55}}}]}
    assert cpu_target(s)==55 and canon(s)==canon(json.loads(json.dumps(s)))
    assert ACTION=="T4_T5_BOUNDED_PREWARM_MIN_2"
    print("P2_06_PILOT_SELF_TEST=PASS"); print("FROZEN_WORKLOAD_REUSED=YES"); print("DEFAULT_MODE=NO_LIVE_WORKLOAD"); return 0

def main():
    global ARGS
    p=argparse.ArgumentParser(); p.add_argument("--self-test",action="store_true"); p.add_argument("--live",action="store_true"); p.add_argument("--acknowledge",default="")
    p.add_argument("--root"); p.add_argument("--stage9-package-root"); p.add_argument("--output-root"); p.add_argument("--context",default="net470-v3"); p.add_argument("--namespace",default="net470-v3"); p.add_argument("--study-prefix",default="P2-06")
    ARGS=p.parse_args()
    if ARGS.self_test: return self_test()
    if not ARGS.live:
        print("P2_06_PILOT=DRY_RUN_ONLY"); print("LIVE_WORKLOAD_EXECUTED=NO"); return 0
    require(ARGS.acknowledge==ACK,"LIVE_ACKNOWLEDGEMENT_MISMATCH"); require(all([ARGS.root,ARGS.stage9_package_root,ARGS.output_root]),"MISSING_REQUIRED_ARGUMENT")
    core_path=Path(ARGS.stage9_package_root,"C5-Stage9-Episode-Core.py")
    require(core_path.is_file(),"STAGE9_R07_CORE_NOT_FOUND")
    require(file_sha(core_path)==R07_CORE_SHA256,"STAGE9_R07_CORE_HASH_MISMATCH")
    out=Path(ARGS.output_root); out.mkdir(parents=True,exist_ok=True)
    summaries=[]
    for i,t in enumerate(("C2","PREWARM"),1):
        selected=existing_admissible(out,t)
        if selected:
            print(f"P2_06_RESUME_SELECTED treatment={t} attempt={selected['attempt']}",flush=True)
            summaries.append(selected)
        else:
            summaries.append(run_arm(ARGS,t,i))
    c2,pw=summaries
    comparison={"schema":"NET470-PART2-P2-06-COMPARISON-V1","recorded_at_utc":utc(),"status":"VERIFIED_PASS","design":"TWO_EPISODE_TECHNICAL_AND_DIRECTIONAL_PILOT_NOT_CONFIRMATORY_INFERENCE","c2_recovery_seconds_or_rmst300":c2["recovery_seconds_or_rmst300_contribution"],"prewarm_recovery_seconds_or_rmst300":pw["recovery_seconds_or_rmst300_contribution"],"prewarm_minus_c2_seconds":round(float(pw["recovery_seconds_or_rmst300_contribution"])-float(c2["recovery_seconds_or_rmst300_contribution"]),6),"interpretation":"NEGATIVE_FAVORS_PREWARM; ONE_PAIR_CANNOT_ESTABLISH_SUPERIORITY","live_workload_episodes":2,"workload_protocol_modified":False,"stage9_modified":False,"stage10_modified":False,"next_step":"REVIEW_PILOT_THEN_AUTHORIZE_REPLICATED_EVALUATION","failure":""}
    write_json(out/"P2-06-comparison-summary.json",comparison)
    print("=== P2-06 PILOT SUMMARY ==="); [print(f"{k}={v}") for k,v in comparison.items() if not isinstance(v,(dict,list))]
    return 0
if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as e:
        print(f"P2_06_FAILURE={type(e).__name__}:{e}",file=sys.stderr); raise
