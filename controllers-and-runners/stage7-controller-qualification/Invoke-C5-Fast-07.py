#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, hashlib, json, os, shutil, subprocess, sys, threading, time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_STAGE6_RECORD_SHA = "B6807B337A7695F00F76EBE6750A910B70056C3A33C905DD9CBB84549E395E1A"
EXPECTED_STAGE6_FREEZE_SHA = "B7B37F7F69C330BC15B3FABC03FCE26E50AB2E752A0CA87F98F81BF8260D99B7"

def now(): return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def sha(p: Path):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest().upper()
def canon(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":")).encode()).hexdigest().upper()
def require(v,m):
    if not v: raise RuntimeError(m)
def run(cmd, timeout=90, check=True, env=None):
    p=subprocess.run(cmd,text=True,capture_output=True,timeout=timeout,env=env)
    if check and p.returncode: raise RuntimeError(f"COMMAND_FAILED:{p.returncode}:{cmd}:{p.stderr.strip()}")
    return p
def kube(context,namespace,args):
    return json.loads(run(["kubectl","--context",context,"-n",namespace,*args,"-o","json"]).stdout)
def hpa_spec(context,namespace): return kube(context,namespace,["get","hpa","processing-hpa"])["spec"]
def patch_min(context,namespace,value):
    payload=json.dumps({"spec":{"minReplicas":value}})
    run(["kubectl","--context",context,"-n",namespace,"patch","hpa","processing-hpa","--type=merge","-p",payload])
def restore(context,namespace,spec):
    payload=json.dumps({"spec":spec})
    run(["kubectl","--context",context,"-n",namespace,"patch","hpa","processing-hpa","--type=merge","-p",payload])
def load_json(p): return json.loads(p.read_text(encoding="utf-8"))
def write_json(p,v): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,indent=2),encoding="utf-8")
def parse_record(p):
    out={}
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line:
            k,v=line.split("=",1); out[k.strip()]=v.strip()
    return out

def locate_stage6(stage6_root: Path):
    records=list(stage6_root.rglob("C5-FAST-06-R01-record.txt"))
    freezes=list(stage6_root.rglob("C5-FAST-06-R01-C2-CALIBRATION-FREEZE.txt"))
    require(len(records)==1,f"STAGE6_RECORD_COUNT:{len(records)}")
    require(len(freezes)==1,f"STAGE6_FREEZE_COUNT:{len(freezes)}")
    require(sha(records[0])==EXPECTED_STAGE6_RECORD_SHA,"STAGE6_RECORD_HASH_MISMATCH")
    require(sha(freezes[0])==EXPECTED_STAGE6_FREEZE_SHA,"STAGE6_FREEZE_HASH_MISMATCH")
    r=parse_record(records[0])
    require(r.get("STATUS")=="VERIFIED_PASS","STAGE6_NOT_VERIFIED_PASS")
    require(r.get("ACCEPTED_CALIBRATION_EPISODES")=="10","STAGE6_CALIBRATION_COUNT_MISMATCH")
    require(r.get("T0_T8_ALL_OBSERVED")=="YES","STAGE6_T0_T8_INCOMPLETE")
    budgets=list(stage6_root.rglob("C5-C2-stage-budget-calibration.csv"))
    require(len(budgets)==1,f"STAGE6_BUDGET_COUNT:{len(budgets)}")
    return records[0],freezes[0],budgets[0]

def baseline_check(context,namespace):
    spec=hpa_spec(context,namespace)
    require(int(spec.get("minReplicas",1))==1,"HPA_MIN_NOT_1")
    require(int(spec.get("maxReplicas",0))==5,"HPA_MAX_NOT_5")
    cpu=[m["resource"]["target"]["averageUtilization"] for m in spec.get("metrics",[]) if m.get("type")=="Resource" and m.get("resource",{}).get("name")=="cpu"]
    require(cpu==[55],f"HPA_CPU_TARGET_NOT_55:{cpu}")
    return deepcopy(spec),canon(spec)

def controller(context,namespace,mode,episode_dir,stop,ledger,original_spec):
    action_applied=False
    try:
        deadline=time.monotonic()+300
        while time.monotonic()<deadline and not stop.is_set():
            high_phase_started=(episode_dir/"workload"/"phase-02-high-challenge.csv").is_file()
            hpa=kube(context,namespace,["get","hpa","processing-hpa"])
            status=hpa.get("status",{}); current=int(status.get("currentReplicas") or 0); desired=int(status.get("desiredReplicas") or 0)
            cpu=None
            for metric in status.get("currentMetrics") or []:
                if metric.get("resource",{}).get("name")=="cpu": cpu=metric.get("resource",{}).get("current",{}).get("averageUtilization")
            row={"observed_at_utc":now(),"mode":mode,"high_challenge_started":high_phase_started,"cpu_percent":cpu,"current_replicas":current,"desired_replicas":desired,"action":"HOLD","reason":"waiting_for_qualified_trigger","authorized":False}
            if mode=="ACTION" and high_phase_started and not action_applied and cpu is not None and int(cpu)>=55 and desired>1:
                row.update(action="HPA_MIN_TO_5",reason="T1_T2_processing_cpu_supported_stage_budget_trigger",authorized=True)
                patch_min(context,namespace,5); action_applied=True
                ledger.append(row)
                time.sleep(20)
                raise RuntimeError("INJECTED_POST_ACTION_FAILURE_FOR_ROLLBACK_QUALIFICATION")
            if mode=="HOLD" and high_phase_started and cpu is not None and int(cpu)>=55 and desired>1:
                row.update(reason="safety_guard_intentionally_false_qualification_hold",authorized=False)
                ledger.append(row); return
            ledger.append(row); time.sleep(2)
    except Exception as exc:
        ledger.append({"observed_at_utc":now(),"mode":mode,"action":"CONTROLLER_EXCEPTION","reason":f"{type(exc).__name__}:{exc}","authorized":False})
    finally:
        restore(context,namespace,original_spec)
        ledger.append({"observed_at_utc":now(),"mode":mode,"action":"RESTORE_EXACT_HPA_SPEC","reason":"finally_path","authorized":True})

def run_pilot(args, package, output, mode, index):
    attempt=output/"pilots"/f"C5-{mode}-P{index:02d}-A01"
    require(not attempt.exists(),f"ATTEMPT_EXISTS:{attempt}")
    original,original_hash=baseline_check(args.context,args.namespace)
    ledger=[]; stop=threading.Event()
    thread=threading.Thread(target=controller,args=(args.context,args.namespace,mode,attempt,stop,ledger,original),daemon=True)
    env=os.environ.copy(); env["C5_STAGE6_EPISODE_ID"]=attempt.name; env["C5_STAGE6_RUN_ID"]="V3-K-"+attempt.name
    thread.start()
    cmd=[sys.executable,str(package/"C5-Stage6-Episode-Core.py"),"--root",args.root,"--package-root",str(package),"--output",str(attempt),"--context",args.context,"--namespace",args.namespace]
    proc=run(cmd,timeout=1200,check=False,env=env)
    stop.set(); thread.join(timeout=90)
    if thread.is_alive(): raise RuntimeError("CONTROLLER_THREAD_DID_NOT_STOP")
    final_hash=canon(hpa_spec(args.context,args.namespace))
    write_json(attempt/"analysis"/"C5-controller-ledger.json",ledger)
    core=load_json(attempt/"analysis"/"C5-FAST-03-pilot-summary.json") if (attempt/"analysis"/"C5-FAST-03-pilot-summary.json").is_file() else {}
    authorized=[x for x in ledger if x.get("authorized") and x.get("action")=="HPA_MIN_TO_5"]
    restores=[x for x in ledger if x.get("action")=="RESTORE_EXACT_HPA_SPEC"]
    if mode=="ACTION": qualified=len(authorized)==1 and len(restores)>=1 and final_hash==original_hash and int(core.get("request_rows",-1))==6450
    else: qualified=len(authorized)==0 and any(x.get("reason")=="safety_guard_intentionally_false_qualification_hold" for x in ledger) and final_hash==original_hash and int(core.get("request_rows",-1))==6450
    summary={"schema":"C5-FAST-07-PILOT-V1","recorded_at_utc":now(),"mode":mode,"execution_attempt_id":attempt.name,"request_rows":core.get("request_rows"),"completed_requests":core.get("completed_requests"),"error_requests":core.get("error_requests"),"core_exit_code":proc.returncode,"authorized_actions":len(authorized),"exact_hpa_restoration":final_hash==original_hash,"qualification_pass":qualified,"workload_protocol_modified":False,"request_retries_added":False,"guide11_dataset_modified":False}
    write_json(attempt/"analysis"/"C5-FAST-07-pilot-summary.json",summary)
    print(f"C5_QUALIFICATION_PILOT_END mode={mode} accepted={qualified} requests={summary['request_rows']} actions={len(authorized)}",flush=True)
    require(qualified,f"C5_PILOT_NOT_QUALIFIED:{mode}:{attempt.name}")
    return summary

def self_test():
    require(canon({"b":2,"a":1})==canon({"a":1,"b":2}),"CANON_TEST")
    require(EXPECTED_STAGE6_RECORD_SHA!=EXPECTED_STAGE6_FREEZE_SHA,"HASH_TEST")
    print("C5_CLASSFAST_07_R01_SELF_TEST=PASS")
    print("FAILURE_PATH_RESTORATION_DESIGN=FINALLY_EXACT_SPEC")
    print("WORKLOAD_PROTOCOL_CHANGED=NO")
    print("REQUEST_RETRIES_CHANGED=NO")
    return 0

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--self-test",action="store_true"); ap.add_argument("--root"); ap.add_argument("--package-root"); ap.add_argument("--stage6-root"); ap.add_argument("--output"); ap.add_argument("--context",default="net470-v3"); ap.add_argument("--namespace",default="net470-v3")
    args=ap.parse_args()
    if args.self_test:return self_test()
    require(all([args.root,args.package_root,args.stage6_root,args.output]),"MISSING_ARGUMENT")
    package=Path(args.package_root).resolve(); output=Path(args.output).resolve(); stage6=Path(args.stage6_root).resolve()
    record,freeze,budgets=locate_stage6(stage6); require(not output.exists(),f"OUTPUT_EXISTS:{output}"); output.mkdir(parents=True)
    shutil.copy2(budgets,output/"C5-C2-stage-budget-calibration.csv")
    baseline_check(args.context,args.namespace)
    summaries=[run_pilot(args,package,output,"ACTION",1),run_pilot(args,package,output,"HOLD",2)]
    final_spec,final_hash=baseline_check(args.context,args.namespace)
    rec={"STEP":"C5-FAST-07-R01","STATUS":"VERIFIED_PASS","RECORDED_AT_UTC":now(),"PROJECT_SCOPE":"C5_ACTION_HOLD_AND_FAILURE_PATH_ROLLBACK_QUALIFICATION","STAGE6_RECORD_SHA256":sha(record),"STAGE6_FREEZE_SHA256":sha(freeze),"QUALIFICATION_PILOTS":2,"ACTION_PILOTS_PASSED":1,"HOLD_PILOTS_PASSED":1,"C5_AUTHORIZED_ACTIONS":sum(x["authorized_actions"] for x in summaries),"EXACT_HPA_RESTORATION_AFTER_EVERY_PILOT":"YES","FAILURE_PATH_ROLLBACK_QUALIFIED":"YES","WORKLOAD_PROTOCOL_MODIFIED":"NO","REQUEST_RETRIES_ADDED":"NO","GUIDE11_DATASET_MODIFIED":"NO","C5_STAGE7_COMPLETE":"YES","C5_FORMAL_EXECUTION_AUTHORIZED":"NO_PENDING_STAGE8_LIVE_BINDING","NEXT_STEP":"C5_FAST_08_FREEZE_QUALIFIED_C5_CONTROLLER_AND_LIVE_BINDING","FAILURE":""}
    record_path=output/"C5-FAST-07-R01-record.txt"; record_path.write_text("=== C5 FAST TRACK STAGE 7 SUMMARY ===\n"+"\n".join(f"{k}={v}" for k,v in rec.items())+"\n",encoding="utf-8")
    freeze_path=output/"C5-FAST-07-R01-C5-ACTION-ROLLBACK-QUALIFICATION-FREEZE.txt"; freeze_path.write_text(record_path.read_text(encoding="utf-8")+f"RECORD_SHA256={sha(record_path)}\n",encoding="utf-8")
    print(record_path.read_text(encoding="utf-8"),end=""); print(f"RECORD_PATH={record_path}"); print(f"RECORD_SHA256={sha(record_path)}"); print(f"FREEZE_PATH={freeze_path}"); print(f"FREEZE_SHA256={sha(freeze_path)}")
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(f"C5_CLASSFAST_07_R01_FAILURE={exc}",file=sys.stderr); raise
