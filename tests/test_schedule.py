# SPDX-License-Identifier: Apache-2.0
"""Cycle detection, critical path, and capacity-constrained scheduling."""

import unittest
from decimal import Decimal

from planner.schedule import ScheduleError, schedule_tasks
from tests.factories import dep, task


class CycleTests(unittest.TestCase):
    def test_cycle_names_both_tasks(self):
        graph = [
            task("A", 1, deps=[dep("B")]),
            task("B", 1, deps=[dep("A")]),
        ]
        with self.assertRaises(ScheduleError) as caught:
            schedule_tasks(graph, {"worker": 1}, 2)
        message = str(caught.exception)
        self.assertIn("A", message)
        self.assertIn("B", message)
        self.assertIn("cycle", message)

    def test_self_cycle(self):
        with self.assertRaises(ScheduleError):
            schedule_tasks([task("A", 1, deps=[dep("A")])], {"worker": 1}, 1)


class CriticalPathTests(unittest.TestCase):
    def test_known_announcement_graph(self):
        """Design-doc graph: T3 has 0.5h slack, critical path is 5.5h."""
        graph = [
            task("T1", 1, 2, 3, assignee="research"),
            task("T2", 1, 1.5, 2, assignee="social", deps=[dep("T1")]),
            task("T3", 1, 2, 3, assignee="creative", deps=[dep("T1")]),
            task("T4", 0.5, 1, 1.5, assignee="accuracy", deps=[dep("T2")]),
            task("T5", 0.25, 0.5, 0.75, assignee="staff", deps=[dep("T3"), dep("T4")]),
            task("M1", 0, assignee="human", deps=[dep("T5")]),
            task("T7", 0.25, 0.5, 0.75, assignee="social", deps=[dep("T5"), dep("M1")]),
        ]
        capacity = {name: 1 for name in ("research", "social", "creative", "accuracy", "staff", "human")}
        result = schedule_tasks(graph, capacity, 3)

        self.assertEqual(result.critical_path, ["T1", "T2", "T4", "T5", "M1", "T7"])
        self.assertEqual(result.critical_tasks, ["T1", "T2", "T4", "T5", "M1", "T7"])
        self.assertEqual(result.cpm_duration, Decimal("5.5"))
        self.assertEqual(result.makespan, Decimal("5.5"))
        self.assertEqual(result.by_id["T1"].expected, Decimal(2))
        self.assertEqual(result.by_id["T2"].expected, Decimal("1.5"))
        self.assertEqual(result.by_id["T3"].slack, Decimal("0.5"))
        self.assertEqual(result.by_id["T3"].earliest_start, Decimal(2))
        self.assertEqual(result.by_id["T3"].earliest_finish, Decimal(4))
        self.assertEqual(result.by_id["T3"].latest_start, Decimal("2.5"))
        self.assertFalse(result.by_id["T3"].critical)
        self.assertEqual(result.by_id["T5"].earliest_start, Decimal("4.5"))
        self.assertEqual(result.by_id["M1"].start, Decimal(5))
        self.assertEqual(result.by_id["T7"].start, Decimal(5))
        self.assertEqual(result.by_id["T7"].finish, Decimal("5.5"))
        # Different agents, cap 3: the resource schedule matches CPM.
        for timing in result.by_id.values():
            self.assertEqual(timing.start, timing.earliest_start)
            self.assertEqual(timing.finish, timing.earliest_finish)

    def test_finish_to_finish(self):
        graph = [
            task("A", 5, assignee="a"),
            task("B", 3, assignee="b", deps=[dep("A", "FF")]),
        ]
        result = schedule_tasks(graph, {"a": 1, "b": 1}, 2)
        self.assertEqual(result.by_id["B"].earliest_start, Decimal(2))
        self.assertEqual(result.by_id["B"].earliest_finish, Decimal(5))
        self.assertEqual(result.by_id["A"].slack, Decimal(0))
        self.assertEqual(result.by_id["B"].slack, Decimal(0))

    def test_start_to_start_lag(self):
        graph = [
            task("A", 4, assignee="a"),
            task("B", 4, assignee="b", deps=[dep("A", "SS", 1)]),
        ]
        result = schedule_tasks(graph, {"a": 1, "b": 1}, 2)
        self.assertEqual(result.by_id["B"].earliest_start, Decimal(1))
        self.assertEqual(result.by_id["B"].start, Decimal(1))
        self.assertEqual(result.makespan, Decimal(5))
        self.assertEqual(result.cpm_std_dev, Decimal(0))


class CapacityTests(unittest.TestCase):
    def test_same_agent_runs_the_critical_task_first(self):
        graph = [task("A", 2), task("B", 5)]
        result = schedule_tasks(graph, {"worker": 1}, 3)
        self.assertEqual(result.by_id["B"].slack, Decimal(0))
        self.assertEqual(result.by_id["A"].slack, Decimal(3))
        self.assertEqual(result.by_id["B"].start, Decimal(0))
        self.assertEqual(result.by_id["A"].start, Decimal(5))
        self.assertEqual(result.makespan, Decimal(7))

    def test_least_slack_beats_an_independent_task(self):
        # A and C are critical. B has slack and must wait, even though it
        # could have filled the agent after A if priority ignored slack.
        graph = [
            task("A", 3),
            task("B", 3),
            task("C", 1, deps=[dep("A")]),
        ]
        result = schedule_tasks(graph, {"worker": 1}, 3)
        self.assertEqual(result.by_id["A"].start, Decimal(0))
        self.assertEqual(result.by_id["C"].start, Decimal(3))
        self.assertEqual(result.by_id["B"].start, Decimal(4))
        self.assertEqual(result.by_id["B"].slack, Decimal(1))
        self.assertEqual(result.makespan, Decimal(7))

    def test_global_cap_serializes_different_agents(self):
        graph = [task("A", 4, assignee="a"), task("B", 4, assignee="b")]
        tight = schedule_tasks(graph, {"a": 1, "b": 1}, 1)
        self.assertEqual(tight.by_id["A"].start, Decimal(0))
        self.assertEqual(tight.by_id["B"].start, Decimal(4))
        self.assertEqual(tight.makespan, Decimal(8))
        wide = schedule_tasks(graph, {"a": 1, "b": 1}, 2)
        self.assertEqual(wide.by_id["A"].start, Decimal(0))
        self.assertEqual(wide.by_id["B"].start, Decimal(0))
        self.assertEqual(wide.makespan, Decimal(4))

    def test_agent_capacity_two_runs_in_parallel(self):
        graph = [task("A", 4), task("B", 4)]
        result = schedule_tasks(graph, {"worker": 2}, 2)
        self.assertEqual(result.by_id["A"].start, Decimal(0))
        self.assertEqual(result.by_id["B"].start, Decimal(0))

    def test_milestone_does_not_consume_capacity(self):
        graph = [
            task("A", 2, assignee="a"),
            task("M", 0, assignee="human"),
        ]
        result = schedule_tasks(graph, {"a": 1, "human": 1}, 1)
        self.assertEqual(result.by_id["A"].start, Decimal(0))
        self.assertEqual(result.by_id["M"].start, Decimal(0))
        self.assertEqual(result.makespan, Decimal(2))


if __name__ == "__main__":
    unittest.main()
