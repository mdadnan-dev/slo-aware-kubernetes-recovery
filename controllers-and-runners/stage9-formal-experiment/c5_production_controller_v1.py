"""Frozen production C5 stage-aware controller. No test-fault injection."""
from __future__ import annotations
import math

class Controller:
    def __init__(self, budgets: dict[str,float], utility_threshold: float=0.25):
        self.budgets=budgets; self.utility_threshold=utility_threshold; self.trigger_streak=0; self.metric_positive_at=None; self.action_taken=False
    @staticmethod
    def choose(cpu: float):
        options=[]
        for replicas in range(2,6):
            utility=(cpu/55.0)*(1.0-1.0/replicas)-0.15*(replicas-1)
            options.append((utility,-replicas,replicas))
        utility,_,replicas=max(options)
        return replicas,utility
    def observe(self, o: dict) -> dict:
        result={"action":"HOLD","authorized":False,"reason":"no_qualified_trigger","target_min_replicas":None,"utility":None}
        if not o.get("high_challenge_started"):
            result["reason"]="outside_actionable_high_challenge_stage"; return result
        if not o.get("safety_ok",True):
            result["reason"]="safety_guard_failed"; return result
        cpu=o.get("cpu_percent"); desired=int(o.get("desired_replicas") or 0)
        trigger=cpu is not None and float(cpu)>=55.0 and desired>1
        self.trigger_streak=self.trigger_streak+1 if trigger else 0
        if not trigger:
            result["reason"]="processing_cpu_support_absent"; return result
        elapsed=float(o.get("metric_positive_elapsed_seconds") or 0.0)
        budget=float(self.budgets.get("T1_T2",0.0))
        if self.trigger_streak<2:
            result["reason"]="confirmation_window_pending"; return result
        if elapsed<budget:
            result["reason"]="stage_budget_not_exceeded"; return result
        target,utility=self.choose(float(cpu)); result["utility"]=round(utility,6)
        if utility<self.utility_threshold:
            result["reason"]="utility_below_threshold"; return result
        if self.action_taken:
            result["reason"]="bounded_action_already_taken"; return result
        self.action_taken=True; result.update(action=f"HPA_MIN_TO_{target}",authorized=True,reason="stage_budget_utility_and_safety_qualified",target_min_replicas=target)
        return result

def self_test():
    c=Controller({"T1_T2":0.0}); a=c.observe({"high_challenge_started":True,"safety_ok":True,"cpu_percent":110,"desired_replicas":2,"metric_positive_elapsed_seconds":1}); assert a["action"]=="HOLD"
    b=c.observe({"high_challenge_started":True,"safety_ok":True,"cpu_percent":110,"desired_replicas":2,"metric_positive_elapsed_seconds":1}); assert b["authorized"] and b["target_min_replicas"] in range(2,6)
    h=Controller({"T1_T2":0.0}).observe({"high_challenge_started":True,"safety_ok":False,"cpu_percent":110,"desired_replicas":2}); assert h["action"]=="HOLD" and h["reason"]=="safety_guard_failed"
    return True
