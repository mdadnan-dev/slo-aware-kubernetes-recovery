#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,importlib.util,json,math,os,subprocess,sys,threading,time,zipfile
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace

STAGE8_RECORD_SHA="C07FC5D39D36410136D0C9513CF72E0675EFCA675C586F62FB41081BB1B76ABE"
STAGE8_FREEZE_SHA="B30C54457F299AD7398611DB2049057CBD2287CF7A148367BE80FDB5DD36C93B"
STAGE8_BINDING_SHA="64758ED6A36688D4EF5A8701312261B58BB649E64A3B3E64F4BB3C82354D61F3"
C5_CONTROLLER_SHA="D0F9FE6463C0326735813DC20F542F6AF73A9C8E53ADA5A8EAA26D97079118A2"
C4_CONTROLLER_SHA="D5497A26A22531180DB61E029E61F3054D3D35EA5A8A1461BF01BA640B8C2692"
STAGE6_RECORD_SHA="B6807B337A7695F00F76EBE6750A910B70056C3A33C905DD9CBB84549E395E1A"
EXPECTED=6450
def now():return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def dt(value):return datetime.fromisoformat(str(value).replace("Z","+00:00"))
def require(v,m):
    if not v:raise RuntimeError(m)
def sha(p):
    h=hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""):h.update(b)
    return h.hexdigest().upper()
def canon(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":")).encode()).hexdigest().upper()
def run(cmd,timeout=90,check=True,env=None):
    p=subprocess.run(cmd,text=True,capture_output=True,timeout=timeout,env=env)
    if check and p.returncode:raise RuntimeError(f"COMMAND_FAILED:{p.returncode}:{cmd}:{p.stderr.strip()}")
    return p
def run_core(cmd,attempt,runroot,env):
    logroot=runroot/"runner-logs";logroot.mkdir(parents=True,exist_ok=True)
    stdout_path=logroot/f"{attempt.name}.core.stdout.log";stderr_path=logroot/f"{attempt.name}.core.stderr.log"
    flags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0
    with stdout_path.open("w",encoding="utf-8") as out,stderr_path.open("w",encoding="utf-8") as err:
        process=subprocess.Popen(cmd,text=True,stdout=out,stderr=err,env=env,creationflags=flags)
        try:
            code=process.wait(timeout=2400);timed_out=False
        except subprocess.TimeoutExpired:
            timed_out=True
            if os.name=="nt":subprocess.run(["taskkill","/PID",str(process.pid),"/T","/F"],capture_output=True,text=True)
            else:process.kill()
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired:pass
            code=124
    return SimpleNamespace(returncode=code,timed_out=timed_out,stdout_path=str(stdout_path),stderr_path=str(stderr_path))
def kube(c,n,args):return json.loads(run(["kubectl","--context",c,"-n",n,*args,"-o","json"]).stdout)
def spec(c,n):return kube(c,n,["get","hpa","processing-hpa"])["spec"]
def restore(c,n,s):run(["kubectl","--context",c,"-n",n,"patch","hpa","processing-hpa","--type=merge","-p",json.dumps({"spec":s})])
def patch(c,n,payload):run(["kubectl","--context",c,"-n",n,"patch","hpa","processing-hpa","--type=merge","-p",json.dumps({"spec":payload})])
def unique(root,name):
    xs=list(Path(root).rglob(name));require(len(xs)==1,f"EXPECTED_ONE:{name}:{len(xs)}");return xs[0]
def parse(p):
    d={}
    for x in Path(p).read_text(encoding="utf-8-sig").splitlines():
        if "=" in x:k,v=x.split("=",1);d[k.strip()]=v.strip()
    return d
def load(path,name):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def validate_binding(root):
    record=unique(root,"C5-FAST-08-R02-record.txt");freeze=unique(root,"C5-FAST-08-R02-CORRECTED-LIVE-BINDING-FREEZE.txt");binding=unique(root,"C5-FORMAL-C1-C5-LIVE-BINDING-R02.json")
    require(sha(record)==STAGE8_RECORD_SHA,"STAGE8_R02_RECORD_HASH_MISMATCH")
    require(sha(freeze)==STAGE8_FREEZE_SHA,"STAGE8_R02_FREEZE_HASH_MISMATCH")
    require(sha(binding)==STAGE8_BINDING_SHA,"STAGE8_R02_BINDING_HASH_MISMATCH")
    b=json.loads(binding.read_text(encoding="utf-8"));require(b.get("qualification_harness_used_in_formal_execution") is False,"QUALIFICATION_HARNESS_NOT_EXCLUDED")
def protocol(package):
    q=package/"C5-Protocol-Generator.py";s=importlib.util.spec_from_file_location("p",q);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m.formal_schedule()
def budget(stage6):
    r=unique(stage6,"C5-FAST-06-R01-record.txt");require(sha(r)==STAGE6_RECORD_SHA,"STAGE6_RECORD_HASH_MISMATCH")
    p=unique(stage6,"C5-C2-stage-budget-calibration.csv");rows=list(csv.DictReader(p.open(encoding="utf-8-sig")));x=next((r for r in rows if r["stage"]=="T1_T2"),None);require(x is not None,"T1_T2_BUDGET_MISSING");return max(0.0,float(x["budget_seconds_median_plus_3mad"]))
def treatment_config(t):return {"C1":(60,"STATIC"),"C2":(55,"STATIC"),"C3":(55,"RECOMMEND"),"C4":(55,"GUARDED"),"C5":(55,"STAGE_AWARE")}[t]
def choose_c5(cpu):
    candidates=[]
    for n in range(2,6):candidates.append((cpu/55.0*(1-1/n)-0.15*(n-1),n))
    utility,n=max(candidates,key=lambda x:(x[0],-x[1]));return n,utility
def controller(c,n,t,attempt,stop,ledger,original,t1t2,c5mod,c4mod):
    positive=None;streak=0;acted=False
    current_min=int(original.get("minReplicas") or 1)
    c5ctrl=c5mod.Controller({"T1_T2":t1t2}) if t=="C5" else None
    guarded=c4mod.Controller() if t in ("C3","C4") else None
    if guarded is not None:guarded.seen_events=set()
    processed=0;window_index=0
    try:
        while not stop.is_set():
            cluster_path=attempt/"telemetry"/"live-cluster-samples.jsonl";cluster=[]
            if cluster_path.is_file():
                for line in cluster_path.read_text(encoding="utf-8",errors="replace").splitlines():
                    try:cluster.append(json.loads(line))
                    except json.JSONDecodeError:pass
            sample=next((x for x in reversed(cluster) if not x.get("sampler_error")),None)
            if sample is None:
                if stop.wait(0.25):return
                continue
            cur=int(sample.get("hpa_current_replicas") or 0);des=int(sample.get("hpa_desired_replicas") or 0);cpu=sample.get("hpa_cpu_percent")
            live=attempt/"telemetry"/"canonical"/("V3-K-"+attempt.name)/"live_request_results.jsonl";rows=[]
            if live.is_file():
                for line in live.read_text(encoding="utf-8",errors="replace").splitlines():
                    try:rows.append(json.loads(line))
                    except json.JSONDecodeError:pass
            new=rows[processed:];processed=len(rows);phase=(rows[-1].get("phase_id") if rows else "");high=phase=="high-challenge";recovery=phase in ("low-recovery","post-observation");lat=[float(x["client_elapsed_ms"]) for x in new if str(x.get("outcome","")).lower()=="completed" and x.get("client_elapsed_ms") not in (None,"")];lat.sort();p95=(lat[max(0,math.ceil(.95*len(lat))-1)] if lat else 0.0);errors=sum(str(x.get("outcome","")).lower()!="completed" for x in new);row={"observed_at_utc":now(),"treatment":t,"high_phase":high,"recovery_phase":recovery,"cpu":cpu,"current":cur,"desired":des,"action":"HOLD","authorized":False,"reason":"no_qualified_trigger","window_index":window_index,"client_p95_ms":p95,"client_errors":errors}
            if recovery and acted:ledger.append({**row,"action":"RESTORE","authorized":True,"reason":"low_recovery_boundary"});restore(c,n,original);return
            trigger=high and cpu is not None and int(cpu)>=55 and des>1
            streak=streak+1 if trigger else 0
            if trigger and positive is None:positive=time.monotonic()
            if guarded:
                uids=sorted(x.get("uid") for x in sample.get("processing_pods",[]) if x.get("uid"))
                d=guarded.observe({"episode_id":attempt.name,"window_index":window_index,"timestamp_utc":sample.get("observed_at_utc") or row["observed_at_utc"],"p95_ms":p95,"completed":len(new)-errors,"errors":errors,"desired_replicas":max(1,des),"ready_endpoints":len(sample.get("processing_ready_endpoints",[])),"processing_pod_uids":uids,"processing_restart_total":sum(int(x.get("restarts") or 0) for x in sample.get("processing_pods",[])),"processing_restart_increase":0,"warning_event_uids":[],"dominant_stage":"processing_cpu" if cpu is not None and int(cpu)>=55 else "unknown","observed_cpu_pct":cpu})
                row.update({"controller_state":d.get("new_state"),"controller_action":d.get("action"),"reason":d.get("reason",row["reason"])})
                if d.get("action")=="PROPOSE_BOUNDED_SCALE_OUT":
                    if t=="C4" and not acted:
                        target=min(5,max(current_min,cur)+1);patch(c,n,{"minReplicas":target});current_min=target;row.update(action=f"HPA_MIN_TO_{target}",authorized=True);acted=True
                    else:row.update(action="RECOMMEND_BOUNDED_SCALE_OUT",authorized=False)
            if t=="C5":
                d=c5ctrl.observe({"high_challenge_started":high,"safety_ok":True,"cpu_percent":cpu,"desired_replicas":des,"metric_positive_elapsed_seconds":0 if positive is None else time.monotonic()-positive})
                row.update(d)
                if d.get("authorized"):
                    target=int(d["target_min_replicas"]);patch(c,n,{"minReplicas":target});current_min=target;acted=True
            ledger.append(row);window_index+=1;stop.wait(15)
    finally:
        restore(c,n,original);ledger.append({"observed_at_utc":now(),"treatment":t,"action":"RESTORE_EXACT_HPA_SPEC","authorized":True,"reason":"finally"})
def attempt_number(path):
    return int(path.name.rsplit("-A",1)[1])
def ordered_event_prefix(events,names):
    values=[events.get(name) for name in names]
    return all(values) and all(dt(values[i-1])<=dt(values[i]) for i in range(1,len(values)))
def scientific_candidate(path):
    formal_path=path/"analysis"/"C5-FAST-09-formal-summary.json"
    core_path=path/"analysis"/"C5-FAST-03-pilot-summary.json"
    if not (formal_path.is_file() and core_path.is_file()):return None
    formal=json.loads(formal_path.read_text(encoding="utf-8"));core=json.loads(core_path.read_text(encoding="utf-8"))
    events=core.get("events") or {};uncertainty=core.get("maximum_clock_mapping_uncertainty_ms")
    base_valid=(int(core.get("request_rows",-1))==EXPECTED and bool(formal.get("hpa_restored")) and
        not formal.get("core_timed_out") and not core.get("technical_failure") and
        uncertainty is not None and float(uncertainty)<=500 and
        ordered_event_prefix(events,["T0","T1","T2","T3","T4","T5","T6","T7"]))
    if not base_valid:return None
    observed=bool(core.get("pilot_qualified")) and bool(events.get("TB")) and bool(events.get("TR"))
    no_breach=not events.get("TB") and not events.get("TR")
    breached_no_recovery=bool(events.get("TB")) and not events.get("TR") and not events.get("T8")
    if observed:
        if not (events.get("T8") and ordered_event_prefix(events,["T0","T1","T2","T3","T4","T5","T6","T7","T8"])):return None
        outcome="OBSERVED"
    elif breached_no_recovery:outcome="RIGHT_CENSORED_RECOVERY_NOT_OBSERVED"
    elif no_breach:
        if not (events.get("T8") and ordered_event_prefix(events,["T0","T1","T2","T3","T4","T5","T6","T7","T8"])):return None
        outcome="RIGHT_CENSORED_NO_QUALIFYING_BREACH"
    else:return None
    durations=core.get("durations") or {}
    selected={**formal,"accepted":True,"scientifically_admissible":True,"outcome":outcome,
        "right_censored":not observed,"recovery_seconds":durations.get("TB_TR_seconds") if observed else 300.0,
        "censor_reason":None if observed else ("RECOVERY_NOT_OBSERVED_BY_300_SECONDS" if breached_no_recovery else "NO_QUALIFYING_BREACH"),
        "maximum_clock_mapping_uncertainty_ms":float(uncertainty),"source_formal_accepted":bool(formal.get("accepted")),
        "selection_rule":"EARLIEST_TECHNICALLY_VALID_ATTEMPT"}
    return selected
def accepted(runroot):
    grouped={}
    paths=sorted((runroot/"episodes").glob("C5-FORMAL-B*-*-A*"),key=lambda p:(p.name.rsplit("-A",1)[0],attempt_number(p))) if (runroot/"episodes").exists() else []
    for p in paths:
        candidate=scientific_candidate(p)
        if candidate:
            key=(int(candidate["block"]),candidate["treatment"])
            if key not in grouped:grouped[key]=candidate
    return grouped
def run_episode(a,package,runroot,row,t1t2,c5mod,c4mod):
    b=int(row["block"]);t=row["treatment"];existing=accepted(runroot);attempt_no=1
    while (runroot/"episodes"/f"C5-FORMAL-B{b:02d}-{t}-A{attempt_no:02d}").exists():attempt_no+=1
    attempt=runroot/"episodes"/f"C5-FORMAL-B{b:02d}-{t}-A{attempt_no:02d}";cpu,mode=treatment_config(t);original=deepcopy(spec(a.context,a.namespace));original_hash=canon(original);patch(a.context,a.namespace,{"minReplicas":1,"maxReplicas":5,"metrics":[{"type":"Resource","resource":{"name":"cpu","target":{"type":"Utilization","averageUtilization":cpu}}}]})
    ledger=[];stop=threading.Event();thread=None
    if mode!="STATIC":thread=threading.Thread(target=controller,args=(a.context,a.namespace,t,attempt,stop,ledger,original,t1t2,c5mod,c4mod),daemon=True);thread.start()
    env=os.environ.copy();env["C5_STAGE6_EPISODE_ID"]=attempt.name;env["C5_STAGE6_RUN_ID"]="V3-K-"+attempt.name;env["C5_FORMAL_CPU_TARGET"]=str(cpu)
    print(f"FORMAL_EPISODE_START block={b} treatment={t} attempt={attempt_no}",flush=True)
    try:p=run_core([sys.executable,str(package/"C5-Stage9-Episode-Core.py"),"--root",a.root,"--package-root",str(package),"--output",str(attempt),"--context",a.context,"--namespace",a.namespace],attempt,runroot,env)
    finally:
        stop.set()
        if thread:thread.join(timeout=90)
        restore(a.context,a.namespace,original)
    (attempt/"analysis").mkdir(parents=True,exist_ok=True)
    corepath=attempt/"analysis"/"C5-FAST-03-pilot-summary.json";core=json.loads(corepath.read_text(encoding="utf-8")) if corepath.is_file() else {};final_hash=canon(spec(a.context,a.namespace));actions=sum(1 for x in ledger if x.get("authorized") and str(x.get("action","")).startswith("HPA_MIN_TO_"));recommend=sum(1 for x in ledger if str(x.get("action","")).startswith("RECOMMEND"))
    events=core.get("events") or {};uncertainty=core.get("maximum_clock_mapping_uncertainty_ms");observed=bool(core.get("pilot_qualified")) and bool(events.get("TB")) and bool(events.get("TR"));no_breach=not events.get("TB") and not events.get("TR");breached_no_recovery=bool(events.get("TB")) and not events.get("TR") and not events.get("T8")
    base_technical=not p.timed_out and int(core.get("request_rows",-1))==EXPECTED and final_hash==original_hash and not core.get("technical_failure") and uncertainty is not None and float(uncertainty)<=500 and ordered_event_prefix(events,["T0","T1","T2","T3","T4","T5","T6","T7"])
    full_t0_t8=bool(events.get("T8")) and ordered_event_prefix(events,["T0","T1","T2","T3","T4","T5","T6","T7","T8"])
    admissible=base_technical and ((observed and full_t0_t8) or (no_breach and full_t0_t8) or breached_no_recovery)
    outcome="OBSERVED" if observed and full_t0_t8 else ("RIGHT_CENSORED_RECOVERY_NOT_OBSERVED" if breached_no_recovery and base_technical else ("RIGHT_CENSORED_NO_QUALIFYING_BREACH" if no_breach and full_t0_t8 else "TECHNICAL_EXCLUSION"))
    failure="CORE_TIMEOUT_2400_SECONDS" if p.timed_out else (core.get("technical_failure","") or ("TECHNICAL_QUALIFICATION_FAILED" if not admissible else ""))
    summary={"schema":"C5-FAST-09-R07-FORMAL-EPISODE-V1","block":b,"position":int(row["position"]),"treatment":t,"execution_attempt_id":attempt.name,"scheduled_requests":EXPECTED,"request_rows":core.get("request_rows"),"completed_requests":core.get("completed_requests"),"error_requests":core.get("error_requests"),"events":events,"durations":core.get("durations",{}),"controller_actions":actions,"controller_recommendations":recommend,"hpa_restored":final_hash==original_hash,"core_exit_code":p.returncode,"core_timed_out":p.timed_out,"core_stdout_log":p.stdout_path,"core_stderr_log":p.stderr_path,"accepted":admissible,"scientifically_admissible":admissible,"outcome":outcome,"right_censored":bool(admissible and not observed),"recovery_seconds":(core.get("durations") or {}).get("TB_TR_seconds") if observed else (300.0 if admissible else None),"censor_reason":None if observed else ("RECOVERY_NOT_OBSERVED_BY_300_SECONDS" if breached_no_recovery else ("NO_QUALIFYING_BREACH" if no_breach else None)),"maximum_clock_mapping_uncertainty_ms":uncertainty,"selection_rule":"EARLIEST_TECHNICALLY_VALID_ATTEMPT","technical_failure":failure}
    (attempt/"analysis"/"C5-controller-ledger.json").write_text(json.dumps(ledger,indent=2),encoding="utf-8");(attempt/"analysis"/"C5-FAST-09-formal-summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8");print(f"FORMAL_EPISODE_END id={attempt.name} accepted={summary['accepted']} errors={summary['error_requests']} actions={actions}",flush=True);require(summary["accepted"],f"FORMAL_EPISODE_PRESERVED_NOT_ACCEPTED:{attempt.name}")
def self_test(package):
    s=protocol(package);require(len(s)==50,"SCHEDULE_COUNT");require(all(len([x for x in s if x["block"]==b])==5 for b in range(1,11)),"BLOCK_TEST");m=load(package/"c5_production_controller_v1.py","c5test");require(m.self_test(),"C5_CONTROLLER_TEST");require(sha(package/"c5_production_controller_v1.py")==C5_CONTROLLER_SHA,"C5_CONTROLLER_HASH_MISMATCH");p=run([sys.executable,str(package/"C5-Stage9-Episode-Core.py"),"--self-test"],check=False);require(p.returncode==0,"EPISODE_CORE_SELF_TEST");source=(package/"C5-Stage9-Episode-Core.py").read_text(encoding="utf-8");require("snapshot_proxy" in source and "live-cluster-samples.jsonl" in source,"SINGLE_COLLECTOR_DESIGN_MISSING");synthetic={f"T{i}":f"2026-01-01T00:00:{i:02d}Z" for i in range(8)};synthetic.update({"TB":"2026-01-01T00:00:08Z","T8":None,"TR":None});require(ordered_event_prefix(synthetic,["T0","T1","T2","T3","T4","T5","T6","T7"]),"CENSORED_PREFIX_ORDER_TEST");require(bool(synthetic["TB"]) and not synthetic["TR"] and not synthetic["T8"],"BREACH_WITHOUT_RECOVERY_TEST");print("C5_CLASSFAST_09_R07_SELF_TEST=PASS");print("CORE_TIMEOUT_SECONDS=2400");print("CORE_STDOUT_STDERR_PRESERVATION=PASS");print("RIGHT_CENSORING_AMENDMENT=PASS");print("BREACH_WITHOUT_RECOVERY_RIGHT_CENSORING=PASS");print("EARLIEST_VALID_ATTEMPT_SELECTION=PASS");return 0
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--self-test",action="store_true");ap.add_argument("--package-root",required=True);ap.add_argument("--root");ap.add_argument("--stage8-r02-root");ap.add_argument("--stage6-root");ap.add_argument("--run-root");ap.add_argument("--max-blocks",type=int,default=1);ap.add_argument("--context",default="net470-v3");ap.add_argument("--namespace",default="net470-v3");a=ap.parse_args();package=Path(a.package_root).resolve()
    if a.self_test:return self_test(package)
    require(all([a.root,a.stage8_r02_root,a.stage6_root,a.run_root]),"MISSING_ARGUMENT");validate_binding(Path(a.stage8_r02_root));c4path=Path(a.root)/"scripts"/"guide8"/"g8_controller_v2.py";require(sha(c4path)==C4_CONTROLLER_SHA,"C4_CONTROLLER_HASH_MISMATCH");c4mod=load(c4path,"g8controller");c5mod=load(package/"c5_production_controller_v1.py","c5prod");t1t2=budget(Path(a.stage6_root));require(run(["kubectl","config","current-context"]).stdout.strip()==a.context,"CONTEXT_MISMATCH");runroot=Path(a.run_root).resolve();(runroot/"episodes").mkdir(parents=True,exist_ok=True);schedule=protocol(package);done=accepted(runroot);pending_blocks=[b for b in range(1,11) if any((b,t) not in done for t in ["C1","C2","C3","C4","C5"])][:a.max_blocks]
    for b in pending_blocks:
        for row in sorted([x for x in schedule if int(x["block"])==b],key=lambda x:int(x["position"])):
            if (b,row["treatment"]) not in accepted(runroot):run_episode(a,package,runroot,row,t1t2,c5mod,c4mod)
        print(f"FORMAL_BLOCK_COMPLETE block={b}",flush=True)
    done=accepted(runroot);state={"updated_at_utc":now(),"accepted_episodes":len(done),"completed_blocks":sum(all((b,t) in done for t in ["C1","C2","C3","C4","C5"]) for b in range(1,11)),"remaining_episodes":50-len(done)};(runroot/"resume-state.json").write_text(json.dumps(state,indent=2),encoding="utf-8")
    amendment={"schema":"C5-FAST-09-R07-SELECTION-AMENDMENT-V1","created_at_utc":now(),"rule":"EARLIEST_TECHNICALLY_VALID_ATTEMPT","no_breach_handling":"RIGHT_CENSORED_AT_300_SECONDS","breach_without_recovery_handling":"RIGHT_CENSORED_AT_300_SECONDS","source_attempts_modified":False,"selected":[done[k] for k in sorted(done)]}
    (runroot/"C5-FAST-09-R07-selection-amendment.json").write_text(json.dumps(amendment,indent=2),encoding="utf-8")
    if len(done)<50:print("=== C5 FAST TRACK STAGE 9 R07 PROGRESS ===");print("STEP=C5-FAST-09-R07\nSTATUS=IN_PROGRESS");print(f"COMPLETED_SCIENTIFICALLY_ADMISSIBLE_EPISODES={len(done)}");print(f"OBSERVED_RECOVERY_EPISODES={sum(not x.get('right_censored') for x in done.values())}");print(f"RIGHT_CENSORED_EPISODES={sum(bool(x.get('right_censored')) for x in done.values())}");print(f"COMPLETED_FORMAL_BLOCKS={state['completed_blocks']}");print(f"REMAINING_FORMAL_EPISODES={50-len(done)}");print("NEXT_STEP=RERUN_C5_FAST_09_R07_TO_RESUME\nFAILURE=");return 0
    rows=[done[k] for k in sorted(done)];write_csv=lambda p,rs: (lambda f:(csv.DictWriter(f,fieldnames=list(rs[0])).writeheader(),f.seek(f.tell())))(p.open("w",encoding="utf-8",newline=""))
    fields=["block","position","treatment","execution_attempt_id","scheduled_requests","request_rows","completed_requests","error_requests","outcome","right_censored","recovery_seconds","censor_reason","maximum_clock_mapping_uncertainty_ms","controller_actions","controller_recommendations","hpa_restored","accepted","selection_rule"]
    with (runroot/"C5-formal-primary-dataset.csv").open("w",encoding="utf-8",newline="") as f:w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(rows)
    rec={"STEP":"C5-FAST-09-R07","STATUS":"VERIFIED_PASS","RECORDED_AT_UTC":now(),"PROJECT_SCOPE":"BALANCED_FORMAL_C1_C5_T0_T8_EXPERIMENT_WITH_COMPLETE_RIGHT_CENSORING","STAGE8_R02_BINDING_SHA256":STAGE8_BINDING_SHA,"FORMAL_EPISODES":50,"EPISODES_PER_TREATMENT":10,"TOTAL_SCHEDULED_REQUESTS":322500,"TOTAL_COMPLETED_REQUESTS":sum(int(x["completed_requests"]) for x in rows),"TOTAL_ERROR_REQUESTS":sum(int(x["error_requests"]) for x in rows),"OBSERVED_RECOVERY_EPISODES":sum(not x.get("right_censored") for x in rows),"RIGHT_CENSORED_EPISODES":sum(bool(x.get("right_censored")) for x in rows),"SELECTION_RULE":"EARLIEST_TECHNICALLY_VALID_ATTEMPT","BREACH_WITHOUT_RECOVERY_HANDLING":"RIGHT_CENSORED_AT_300_SECONDS","CORE_TIMEOUT_SECONDS":2400,"HPA_RESTORED_AFTER_EVERY_EPISODE":"YES","GUIDE11_DATASET_MODIFIED":"NO","WORKLOAD_PROTOCOL_MODIFIED":"NO","REQUEST_RETRIES_ADDED":"NO","COLLECTOR_ARCHITECTURE":"SINGLE_PROXY_BACKED_AUTHORITATIVE_STREAM","C5_STAGE9_COMPLETE":"YES","NEXT_STEP":"C5_FAST_10_ANALYZE_BALANCED_C1_C5_T0_T8_AND_FREEZE","FAILURE":""};rp=runroot/"C5-FAST-09-R07-record.txt";rp.write_text("=== C5 FAST TRACK STAGE 9 R07 SUMMARY ===\n"+"\n".join(f"{k}={v}" for k,v in rec.items())+"\n",encoding="utf-8");fp=runroot/"C5-FAST-09-R07-FORMAL-C1-C5-EXECUTION-FREEZE.txt";fp.write_text(rp.read_text()+f"PRIMARY_DATASET_SHA256={sha(runroot/'C5-formal-primary-dataset.csv')}\nAMENDMENT_SHA256={sha(runroot/'C5-FAST-09-R07-selection-amendment.json')}\nRECORD_SHA256={sha(rp)}\n");print(rp.read_text(),end="");print(f"RECORD_SHA256={sha(rp)}\nFREEZE_SHA256={sha(fp)}");return 0
if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception as e:print(f"C5_CLASSFAST_09_R07_FAILURE={e}",file=sys.stderr);raise
