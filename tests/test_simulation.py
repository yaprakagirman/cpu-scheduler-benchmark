import unittest

from scheduler_benchmark import simulate


class SimulationTests(unittest.TestCase):
    def test_empty_workload_returns_zero_metrics(self):
        result = simulate([], "FCFS", quantum=4, overhead=0)

        self.assertEqual(result["total_time"], 0)
        self.assertEqual(result["busy_time"], 0)
        self.assertEqual(result["throughput"], 0.0)
        self.assertEqual(result["done"], [])

    def test_fcfs_completes_deterministic_workload(self):
        workload = [("P1", 3, 0), ("P2", 2, 1)]
        result = simulate(workload, "FCFS", quantum=4, overhead=0)

        self.assertEqual(result["total_time"], 5)
        self.assertEqual(result["busy_time"], 5)
        self.assertEqual(result["avg_wait"], 1.0)
        self.assertEqual(result["avg_resp"], 1.0)
        self.assertEqual([p.name for p in result["done"]], ["P1", "P2"])

    def test_round_robin_completes_all_processes(self):
        workload = [("P1", 5, 0), ("P2", 3, 0), ("P3", 2, 1)]
        result = simulate(workload, "RR", quantum=2, overhead=0)

        self.assertEqual(result["busy_time"], 10)
        self.assertEqual(len(result["done"]), 3)
        self.assertEqual({p.name for p in result["done"]}, {"P1", "P2", "P3"})
        self.assertTrue(any(p.preemptions > 0 for p in result["done"]))


if __name__ == "__main__":
    unittest.main()
