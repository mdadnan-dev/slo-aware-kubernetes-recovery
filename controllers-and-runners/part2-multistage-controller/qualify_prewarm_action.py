"""Guarded qualification for T4_T5_BOUNDED_PREWARM_MIN_2.

The live path briefly patches processing-hpa.spec.minReplicas from 1 to 2,
verifies HPA/deployment state, then restores the exact original HPA spec in a
finally block. It does not run workload traffic or modify application code.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, subprocess, sys, time
from pathlib import Path

ACTION="T4_T5_BOUNDED_PREWARM_MIN_2"

def utc(): return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z")
def canon(v): return json.dumps(v,sort_keys=True,separators=(",",":"))
def digest(v): return hashlib.sha256(canon(v).encode()).hexdigest().upper()

def run(cmd, check=True):
    p=subprocess.run(cmd,text=True,capture_output=True)
    if check and p.returncode:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr}")
    return p

def kubectl(context,namespace,*args):
    return run(["kubectl","--context",context,"-n",namespace,*args])

def get_json(context,namespace,kind,name):
    return json.loads(kubectl(context,namespace,"get",kind,name,"-o","json").stdout)

def cpu_target(spec):
    for m in spec.get("metrics",[]):
        r=m.get("resource") or {}
        if m.get("type")=="Resource" and r.get("name")=="cpu":
            return int(((r.get("target") or {}).get("averageUtilization")))
    return None

def patch_spec(context,namespace,spec):
    payload=json.dumps({"spec":spec},separators=(",",":"))
    kubectl(context,namespace,"patch","hpa","processing-hpa","--type=merge","-p",payload)

def snapshot(context,namespace):
    h=get_json(context,namespace,"hpa","processing-hpa")
    d=get_json(context,namespace,"deployment","processing")
    return {"captured_at_utc":utc(),"hpa_spec":h["spec"],"hpa_status":h.get("status",{}),
            "deployment_status":d.get("status",{})}

def self_test():
    s={"minReplicas":1,"maxReplicas":5,"metrics":[{"type":"Resource","resource":{"name":"cpu","target":{"type":"Utilization","averageUtilization":55}}}]}
    assert cpu_target(s)==55
    changed=json.loads(json.dumps(s)); changed["minReplicas"]=2
    assert s["minReplicas"]==1 and changed["minReplicas"]==2
    assert digest(s)==digest(json.loads(canon(s)))
    print("P2_05_QUALIFICATION_SELF_TEST=PASS")
    print("DEFAULT_MODE=NO_LIVE_MUTATION")
    return 0

def live(a):
    if a.acknowledge_mutation!=ACTION:
        raise RuntimeError("live acknowledgement does not match selected action")
    out=Path(a.output); out.mkdir(parents=True,exist_ok=True)
    ledger=[]; original=None; original_hash=None; passed=False; failure=""
    try:
        before=snapshot(a.context,a.namespace); original=before["hpa_spec"]
        original_hash=digest(original)
        (out/"before.json").write_text(json.dumps(before,indent=2)+"\n",encoding="utf-8")
        if int(original.get("minReplicas",-1))!=1 or int(original.get("maxReplicas",-1))!=5 or cpu_target(original)!=55:
            raise RuntimeError("BASELINE_HPA_NOT_1_5_55")
        hs=before["hpa_status"]; ds=before["deployment_status"]
        if int(hs.get("currentReplicas",0))!=1 or int(hs.get("desiredReplicas",0))!=1:
            raise RuntimeError("BASELINE_HPA_NOT_IDLE_ONE_REPLICA")
        if int(ds.get("availableReplicas",0))<1:
            raise RuntimeError("BASELINE_PROCESSING_NOT_AVAILABLE")
        ledger.append({"at_utc":utc(),"event":"BASELINE_VERIFIED","original_spec_sha256":original_hash})
        changed=json.loads(json.dumps(original)); changed["minReplicas"]=2
        patch_spec(a.context,a.namespace,changed)
        ledger.append({"at_utc":utc(),"event":"PREWARM_PATCH_APPLIED","minReplicas":2})
        deadline=time.monotonic()+a.timeout_seconds; qualified=None
        while time.monotonic()<deadline:
            state=snapshot(a.context,a.namespace)
            hs=state["hpa_status"]; ds=state["deployment_status"]
            if (int(state["hpa_spec"].get("minReplicas",0))==2 and
                int(hs.get("currentReplicas",0))>=2 and int(hs.get("desiredReplicas",0))>=2 and
                int(ds.get("availableReplicas",0))>=2):
                qualified=state; break
            time.sleep(2)
        if qualified is None: raise RuntimeError("PREWARM_DID_NOT_REACH_TWO_AVAILABLE_REPLICAS")
        (out/"prewarm-qualified.json").write_text(json.dumps(qualified,indent=2)+"\n",encoding="utf-8")
        ledger.append({"at_utc":utc(),"event":"PREWARM_QUALIFIED","availableReplicas":qualified["deployment_status"].get("availableReplicas")})
        passed=True
    except Exception as e:
        failure=f"{type(e).__name__}:{e}"
    finally:
        if original is not None:
            try:
                patch_spec(a.context,a.namespace,original)
                deadline=time.monotonic()+a.timeout_seconds; restored=None
                while time.monotonic()<deadline:
                    state=snapshot(a.context,a.namespace)
                    if digest(state["hpa_spec"])==original_hash:
                        restored=state; break
                    time.sleep(2)
                if restored is None: raise RuntimeError("EXACT_HPA_SPEC_RESTORATION_TIMEOUT")
                (out/"restored.json").write_text(json.dumps(restored,indent=2)+"\n",encoding="utf-8")
                ledger.append({"at_utc":utc(),"event":"EXACT_HPA_SPEC_RESTORED","restored_spec_sha256":digest(restored["hpa_spec"])})
            except Exception as e:
                passed=False; failure=(failure+";" if failure else "")+f"RESTORE_FAILURE:{type(e).__name__}:{e}"
        (out/"ledger.json").write_text(json.dumps(ledger,indent=2)+"\n",encoding="utf-8")
    result={"schema":"NET470-PART2-PREWARM-QUALIFICATION-V1","recorded_at_utc":utc(),"action":ACTION,
            "status":"VERIFIED_PASS" if passed and not failure else "FAILED","live_workload_executed":False,
            "application_modified":False,"workload_protocol_modified":False,"original_spec_sha256":original_hash,"failure":failure}
    (out/"qualification-summary.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(f"P2_05_PREWARM_QUALIFICATION={result['status']}")
    print("LIVE_WORKLOAD_EXECUTED=NO")
    print("APPLICATION_MODIFIED=NO")
    print("WORKLOAD_PROTOCOL_MODIFIED=NO")
    print(f"ORIGINAL_HPA_SPEC_SHA256={original_hash or ''}")
    print(f"FAILURE={failure}")
    return 0 if result["status"]=="VERIFIED_PASS" else 1

def main():
    p=argparse.ArgumentParser(); p.add_argument("--self-test",action="store_true")
    p.add_argument("--live",action="store_true"); p.add_argument("--acknowledge-mutation",default="")
    p.add_argument("--context",default="net470-v3"); p.add_argument("--namespace",default="net470-v3")
    p.add_argument("--output",default="analysis/prewarm-qualification"); p.add_argument("--timeout-seconds",type=int,default=180)
    a=p.parse_args()
    if a.self_test: return self_test()
    if not a.live:
        print("P2_05_QUALIFICATION=DRY_RUN_ONLY")
        print("LIVE_MUTATION_PERFORMED=NO")
        return 0
    return live(a)
if __name__=="__main__": raise SystemExit(main())

