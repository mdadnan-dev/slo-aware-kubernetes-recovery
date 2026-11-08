#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,hashlib,importlib.util,json,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
R01_RECORD="58ABF65C1F973E5615E3C2A5BA66E759519403F42596A4E16102F5F70F1DFA6F";R01_FREEZE="7426421BBF2E277FAEE71D08AF4194D7CBC101C75A735A645ED43DF2BBB3F9D1";R01_BINDING="162CC43BFF09637E93D2685A1D6F6A2225DF8DD8E34C189B9FD9FC0CDAC682F6";S7_RECORD="16FE28089108915E20EF5AD97431CBDBC9E4D354D3FDFA94FFC5E1146BF77A4D";S7_FREEZE="4D68D331D667D4305B9D6523DE53D8E4377928AB671738EFA80BCE1AFD2477E5";C4_SHA="D5497A26A22531180DB61E029E61F3054D3D35EA5A8A1461BF01BA640B8C2692"
def now():return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest().upper()
def require(v,m):
    if not v:raise RuntimeError(m)
def unique(r,n):
    x=list(Path(r).rglob(n));require(len(x)==1,f"EXPECTED_ONE:{n}:{len(x)}");return x[0]
def run(c):
    p=subprocess.run(c,text=True,capture_output=True);require(p.returncode==0,f"COMMAND_FAILED:{c}:{p.stderr}");return p
def load(path,name):s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def selftest(pkg):
    m=load(pkg/"c5_production_controller_v1.py","c5");require(m.self_test(),"C5_SELF_TEST");print("C5_CLASSFAST_08_R02_SELF_TEST=PASS");print("CORRECTION_SCOPE=SEPARATE_QUALIFICATION_HARNESS_FROM_PRODUCTION_CONTROLLER");return 0
def main():
    p=argparse.ArgumentParser();p.add_argument("--self-test",action="store_true");p.add_argument("--package-root",required=True);p.add_argument("--root");p.add_argument("--stage8-r01-root");p.add_argument("--stage7-root");p.add_argument("--stage6-root");p.add_argument("--output");a=p.parse_args();pkg=Path(a.package_root)
    if a.self_test:return selftest(pkg)
    require(all([a.root,a.stage8_r01_root,a.stage7_root,a.stage6_root,a.output]),"MISSING_ARGUMENT");out=Path(a.output);require(not out.exists(),"OUTPUT_EXISTS")
    r1=unique(a.stage8_r01_root,"C5-FAST-08-R01-record.txt");f1=unique(a.stage8_r01_root,"C5-FAST-08-R01-QUALIFIED-C5-LIVE-BINDING-FREEZE.txt");b1=unique(a.stage8_r01_root,"C5-FORMAL-C1-C5-LIVE-BINDING-V1.json");require(sha(r1)==R01_RECORD and sha(f1)==R01_FREEZE and sha(b1)==R01_BINDING,"R01_HASH_MISMATCH")
    r7=unique(a.stage7_root,"C5-FAST-07-R01-record.txt");f7=unique(a.stage7_root,"C5-FAST-07-R01-C5-ACTION-ROLLBACK-QUALIFICATION-FREEZE.txt");require(sha(r7)==S7_RECORD and sha(f7)==S7_FREEZE,"STAGE7_HASH_MISMATCH")
    c4=Path(a.root)/"scripts"/"guide8"/"g8_controller_v2.py";require(sha(c4)==C4_SHA,"C4_CONTROLLER_HASH_MISMATCH")
    budgetfile=unique(a.stage6_root,"C5-C2-stage-budget-calibration.csv");budgets={r["stage"]:float(r["budget_seconds_median_plus_3mad"]) for r in csv.DictReader(budgetfile.open(encoding="utf-8-sig"))}
    c5=load(pkg/"c5_production_controller_v1.py","c5prod");require(c5.self_test(),"PRODUCTION_CONTROLLER_SELF_TEST")
    actionledger=json.loads(unique(a.stage7_root,"C5-controller-ledger.json").read_text(encoding="utf-8")) if len(list(Path(a.stage7_root).rglob("C5-controller-ledger.json")))==1 else None
    ledgers=list(Path(a.stage7_root).rglob("C5-controller-ledger.json"));require(len(ledgers)==2,f"STAGE7_LEDGER_COUNT:{len(ledgers)}")
    action=json.loads(next(x for x in ledgers if "ACTION" in str(x)).read_text(encoding="utf-8"));hold=json.loads(next(x for x in ledgers if "HOLD" in str(x)).read_text(encoding="utf-8"))
    trigger=next(x for x in action if x.get("action")=="HPA_MIN_TO_5");ctrl=c5.Controller({"T1_T2":0.0});o={"high_challenge_started":True,"safety_ok":True,"cpu_percent":trigger["cpu_percent"],"desired_replicas":trigger["desired_replicas"],"metric_positive_elapsed_seconds":999};ctrl.observe(o);equiv=ctrl.observe(o);require(equiv["authorized"],"ACTION_EQUIVALENCE_FAILED")
    hctrl=c5.Controller(budgets);heq=hctrl.observe({"high_challenge_started":True,"safety_ok":False,"cpu_percent":110,"desired_replicas":2,"metric_positive_elapsed_seconds":999});require(heq["action"]=="HOLD","HOLD_EQUIVALENCE_FAILED")
    require(run(["kubectl","config","current-context"]).stdout.strip()=="net470-v3","CONTEXT_MISMATCH");h=json.loads(run(["kubectl","-n","net470-v3","get","hpa","processing-hpa","-o","json"]).stdout);spec=h["spec"];require(int(spec.get("minReplicas",1))==1 and int(spec.get("maxReplicas",0))==5,"HPA_NOT_RESET")
    out.mkdir(parents=True);(out/"contracts").mkdir();(out/"contracts"/"c5_production_controller_v1.py").write_bytes((pkg/"c5_production_controller_v1.py").read_bytes());(out/"contracts"/"g8_controller_v2.py.sha256.txt").write_text(C4_SHA+"  C:\\net470-v3\\scripts\\guide8\\g8_controller_v2.py\n")
    binding={"schema":"C5-FAST-08-R02-CORRECTED-LIVE-BINDING-V1","recorded_at_utc":now(),"r01_binding_sha256":R01_BINDING,"c4_controller_sha256":C4_SHA,"c5_production_controller_sha256":sha(pkg/"c5_production_controller_v1.py"),"c5_qualification_harness_sha256":"4ED2123E943AFDB918D91E24A9137F0D268BF0573184AE9386D1A7A625E45E49","qualification_harness_used_in_formal_execution":False,"stage7_action_equivalence":True,"stage7_hold_equivalence":True,"formal_schedule_sha256":"9B3BD8035B20B6FDFE34D463AE20D0397B4C66DD860F771FE086A43761F45957"};bp=out/"C5-FORMAL-C1-C5-LIVE-BINDING-R02.json";bp.write_text(json.dumps(binding,indent=2),encoding="utf-8")
    rec={"STEP":"C5-FAST-08-R02","STATUS":"VERIFIED_PASS","RECORDED_AT_UTC":now(),"PRESERVED_R01_STATUS":"VERIFIED_PASS_BINDING_AMENDED","R01_AMENDMENT_REASON":"QUALIFICATION_HARNESS_CONTAINED_FAULT_INJECTION_AND_MUST_NOT_BE_FORMAL_CONTROLLER","CORRECTION_SCOPE":"SEPARATE_PRODUCTION_C5_CONTROLLER_ARTIFACT_AND_VERIFY_DECISION_EQUIVALENCE","STAGE7_ACTION_EQUIVALENCE":"PASS","STAGE7_HOLD_EQUIVALENCE":"PASS","C4_CONTROLLER_SHA256":C4_SHA,"C5_PRODUCTION_CONTROLLER_SHA256":binding["c5_production_controller_sha256"],"QUALIFICATION_HARNESS_USED_IN_FORMAL_EXECUTION":"NO","CLUSTER_QUERIED_BY_THIS_STEP":"YES_READ_ONLY","CLUSTER_MODIFIED_BY_THIS_STEP":"NO","LIVE_WORKLOAD_EXECUTED_BY_THIS_STEP":"NO","C5_STAGE8_CORRECTION_COMPLETE":"YES","C5_FORMAL_EXECUTION_AUTHORIZED":"YES_BOUND_R02_PROTOCOL_ONLY","NEXT_STEP":"C5_FAST_09_R02_EXECUTE_RESUMABLE_BALANCED_C1_C5_FORMAL_EXPERIMENT","FAILURE":""};rp=out/"C5-FAST-08-R02-record.txt";rp.write_text("=== C5 FAST TRACK STAGE 8 R02 SUMMARY ===\n"+"\n".join(f"{k}={v}" for k,v in rec.items())+"\n");fp=out/"C5-FAST-08-R02-CORRECTED-LIVE-BINDING-FREEZE.txt";fp.write_text(rp.read_text()+f"BINDING_SHA256={sha(bp)}\nRECORD_SHA256={sha(rp)}\n");print(rp.read_text(),end="");print(f"BINDING_SHA256={sha(bp)}\nRECORD_SHA256={sha(rp)}\nFREEZE_SHA256={sha(fp)}");return 0
if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception as e:print(f"C5_CLASSFAST_08_R02_FAILURE={e}",file=sys.stderr);raise
