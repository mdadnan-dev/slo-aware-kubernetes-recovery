import unittest
from controller.multistage_controller import diagnose, stable_recovery, STAGES

B={s:10.0 for s in STAGES}

class Tests(unittest.TestCase):
    def test_no_excess_holds(self):
        d=diagnose({s:10 for s in STAGES},B)
        self.assertEqual(d.action,"HOLD"); self.assertIsNone(d.dominant_stage)
    def test_dominant_normalized_excess(self):
        d=diagnose({**{s:10 for s in STAGES},"T1_T2":20,"T7_T8":25},B)
        self.assertEqual(d.dominant_stage,"T7_T8")
        self.assertEqual(d.action,"EVALUATE_BOUNDED_CONCURRENCY_OR_QUEUE_CONTROL")
        self.assertFalse(d.authorized)
    def test_missing_not_imputed(self):
        d=diagnose({"T7_T8":None,"T5_T6":15},B)
        self.assertEqual(d.dominant_stage,"T5_T6")
    def test_tie_uses_fixed_stage_order(self):
        d=diagnose({"T1_T2":20,"T7_T8":20},B)
        self.assertEqual(d.dominant_stage,"T1_T2")
    def test_negative_rejected(self):
        with self.assertRaises(ValueError): diagnose({"T1_T2":-1},B)
    def test_stable_recovery(self):
        self.assertTrue(stable_recovery([140,120,121,122,123,120,121,122,123],123.472))
        self.assertFalse(stable_recovery([120,121,122,123,130,120,121,122],123.472))
        self.assertFalse(stable_recovery([120,121,122,123,120,121,122],123.472))

if __name__=="__main__": unittest.main()

