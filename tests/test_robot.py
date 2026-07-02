"""Tests for the Traveller robot builder (builders/robot.py) — CI-safe, synthetic."""

import unittest

from lorehound.builders.model import RobotBuild
from lorehound.builders.robot import (
    Chassis,
    Locomotion,
    RobotData,
    RobotOption,
    RobotReport,
    _credits,
    build_robot_data,
    parse_chassis,
    parse_locomotion,
    parse_options,
    robot_flow,
)
from lorehound.chargen.engine import FAITHFUL, ChargenSession
from lorehound.markdown_tables import MarkdownTable


def _t(title, rows):
    return MarkdownTable(page=1, title=title, rows=rows, source="Robot Handbook")


class TestCreditsParser(unittest.TestCase):
    def test_credits(self):
        self.assertEqual(_credits("Cr1000"), 1000)
        self.assertEqual(_credits("2000"), 2000)
        self.assertEqual(_credits("MCr1.2"), 1_200_000)
        self.assertEqual(_credits("—"), 0)


class TestParsers(unittest.TestCase):
    def test_chassis_by_columns_despite_doubled_headers(self):
        t = _t("Size", [
            ["Robot S Size", "Size Base Slots", "Base Hits", "Attack Roll DM", "Equivalent Size", "Basic Cost"],
            ["1", "1", "1", "-4", "Rat", "Cr100"],
            ["5", "16", "20", "+0", "Human, Vargr", "Cr1000"],
        ])
        c = {ch.size: ch for ch in parse_chassis(t)}
        self.assertEqual((c[5].base_slots, c[5].base_hits, c[5].cost), (16, 20, 1000))
        self.assertIn("Human", c[5].equivalent)

    def test_locomotion_multiplier(self):
        t = _t("Locomotion", [
            ["Robot Locomotion", "TL", "Agility", "Traits", "Base Endurance", "Cost Multiplier"],
            ["None", "5", "—", "—", "216 hours", "x1"],
            ["Wheels", "5", "+0", "—", "72 hours", "x2"],
            ["Grav", "9", "+1", "Flyer", "24 hours", "x20"],
        ])
        locos = {loco.name: loco.cost_multiplier for loco in parse_locomotion(t)}
        self.assertEqual(locos, {"None": 1.0, "Wheels": 2.0, "Grav": 20.0})

    def test_options_named_with_slots_and_cost(self):
        t = _t("Physical Options", [
            ["Item", "Slots", "Notes", "TL", "Traits", "Skill", "Cost"],
            ["Autobar (enhanced)", "2", "Max DM+2", "10", "", "", "2000"],
            ["Medikit (basic)", "1", "Max DM+0", "8", "", "", "1000"],
        ])
        opts = {o.name: (o.slots, o.cost) for o in parse_options([t])}
        self.assertEqual(opts["Autobar (enhanced)"], (2, 2000))
        self.assertEqual(opts["Medikit (basic)"], (1, 1000))


class TestCompute(unittest.TestCase):
    def test_report_costs_and_slots(self):
        r = RobotReport(
            chassis=Chassis(5, 16, 20, 1000),
            locomotion=Locomotion("Wheels", 2.0),
            options=[RobotOption("Autobar", 2, 2000), RobotOption("Medikit", 1, 1000)],
        )
        self.assertEqual(r.base_cost, 2000)        # 1000 × 2
        self.assertEqual(r.options_cost, 3000)
        self.assertEqual(r.total_cost, 5000)
        self.assertEqual(r.slots_used, 3)
        self.assertEqual(r.slots_free, 13)


class TestFlow(unittest.TestCase):
    def _data(self):
        return RobotData(
            game="T", source="Robot Handbook",
            chassis=[Chassis(5, 16, 20, 1000, "Human"), Chassis(1, 1, 1, 100, "Rat")],
            locomotions=[Locomotion("Wheels", 2.0), Locomotion("Grav", 20.0)],
            options=[RobotOption("Autobar", 2, 2000), RobotOption("Medikit", 1, 1000)],
        )

    def _session(self, data=None):
        data = data or self._data()
        return ChargenSession(robot_flow, mode=FAITHFUL, draft=RobotBuild(game="T"),
                              data=data, draft_factory=lambda: RobotBuild(game="T"))

    def test_walk_and_compute(self):
        s = self._session()
        self.assertEqual(s.current.id, "chassis")
        s.resolve("5")
        self.assertEqual(s.current.id, "locomotion")
        s.resolve("Wheels")
        self.assertTrue(s.current.id.startswith("option-"))
        s.resolve("Autobar|2|2000")
        s.resolve("Medikit|1|1000")
        s.resolve("__done__")
        self.assertTrue(s.complete)
        d = s.draft
        self.assertEqual((d.size, d.locomotion, d.slots_total, d.base_hits), (5, "Wheels", 16, 20))
        self.assertEqual(d.slots_used, 3)
        self.assertEqual(d.base_cost, 2000)
        self.assertEqual(d.total_cost, 5000)

    def test_only_fitting_options_offered(self):
        data = self._data()
        data.chassis[0] = Chassis(5, 3, 20, 1000, "Human")   # only 3 slots
        s = self._session(data)
        s.resolve("5")
        s.resolve("Wheels")
        s.resolve("Autobar|2|2000")                          # 2 of 3 slots used, 1 free
        values = {o.value for o in s.current.options}
        self.assertNotIn("Autobar|2|2000", values)           # a 2-slot option no longer fits
        self.assertIn("Medikit|1|1000", values)              # the 1-slot option still fits
        self.assertIn("__done__", values)

    def test_empty_catalogue_completes(self):
        s = self._session(RobotData(game="T"))
        self.assertTrue(s.complete)
        self.assertEqual(s.draft.size, 0)


class TestBuildRobotData(unittest.TestCase):
    def test_from_rules_markdown_tables(self):
        mts = [
            _t("Size", [["Robot Size", "Base Slots", "Base Hits", "Basic Cost"],
                        ["5", "16", "20", "Cr1000"]]),
            _t("Locomotion", [["Robot Locomotion", "Cost Multiplier"], ["Wheels", "x2"]]),
            _t("Physical Options", [["Item", "Slots", "Cost"], ["Medikit", "1", "1000"]]),
        ]
        rules = type("R", (), {"markdown_tables": {"Traveller": mts}})()
        data = build_robot_data(rules, "Traveller")
        self.assertTrue(data.ok)
        self.assertEqual(data.source, "Robot Handbook")
        self.assertEqual(data.chassis[0].size, 5)


if __name__ == "__main__":
    unittest.main()
