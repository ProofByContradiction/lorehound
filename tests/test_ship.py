"""Tests for the Traveller ship builder's data + compute layer (builders/ship.py).

Pure-logic (CI): pins the cost parser, the harvested-table parsers, and the Core-MVP
construction maths against grounded Mongoose 2022 High Guard values.
"""

import unittest

from lorehound.builders.model import ShipBuild
from lorehound.builders.ship import (
    Computer,
    DriveStep,
    HullConfig,
    PowerPlant,
    ShipData,
    _mcr,
    build_ship_data,
    compute_ship,
    j_drive_tons,
    m_drive_tons,
    parse_bridges,
    parse_configs,
    parse_power_plants,
    parse_thrust,
    power_required,
    ship_data_from_tables,
    ship_flow,
)
from lorehound.chargen.engine import FAITHFUL, ChargenSession
from lorehound.markdown_tables import MarkdownTable


def _t(title, rows, page=1):
    return MarkdownTable(page=page, title=title, rows=rows)


class TestCostParser(unittest.TestCase):
    def test_mcr(self):
        self.assertEqual(_mcr("MCr0.4"), 0.4)
        self.assertEqual(_mcr("Cr50000"), 0.05)
        self.assertEqual(_mcr("Cr30000"), 0.03)
        self.assertEqual(_mcr("MCr1.5"), 1.5)
        self.assertEqual(_mcr("—"), 0.0)
        self.assertEqual(_mcr(""), 0.0)


class TestParsers(unittest.TestCase):
    def test_configs(self):
        t = _t("HULL CONFIGURATION", [
            ["Hull Configuration", "Streamlined?", "Armour Volume Modifier", "Hull Points", "Hull Cost"],
            ["Standard", "Partial", "—", "—", "—"],
            ["Streamlined", "Yes", "+20%", "—", "+20%"],
            ["Dispersed Structure", "No", "—", "—", "-50%"],
        ])
        cfgs = {c.name: c.cost_modifier for c in parse_configs(t)}
        self.assertEqual(cfgs["Standard"], 0.0)
        self.assertAlmostEqual(cfgs["Streamlined"], 0.20)
        self.assertAlmostEqual(cfgs["Dispersed Structure"], -0.50)

    def test_thrust_potential_transposed(self):
        t = _t("Thrust Potential", [
            ["Manoeuvre Drive Rating", "0", "1", "2", "3"],
            ["% of Hull", "0.5%", "1%", "2%", "3%"],
            ["Manoeuvre TL", "9", "9", "10", "10"],
        ])
        steps = parse_thrust([t])
        self.assertAlmostEqual(steps[2].percent_hull, 0.02)
        self.assertEqual(steps[2].tl, 10)

    def test_power_plants(self):
        t = _t("POWER PLANT TYPE", [
            ["Power Plant Type", "Power per Ton", "Cost per Ton"],
            ["Fusion (TL8)", "10", "MCr0.5"],
            ["Fission (TL6)", "8", "MCr0.4"],
        ])
        pps = {p.name: (p.power_per_ton, p.cost_per_ton) for p in parse_power_plants(t)}
        self.assertEqual(pps["Fusion (TL8)"], (10.0, 0.5))

    def test_bridges_brackets(self):
        t = _t("Bridges", [
            ["Bridges Size of Ship", "Size of Bridge"],
            ["50 tons or less", "3 tons"],
            ["100–200 tons", "10 tons"],
        ])
        data = ShipData(game="T", bridges=parse_bridges(t))
        self.assertEqual(data.bridge_tons(40), 3)
        self.assertEqual(data.bridge_tons(200), 10)


class TestComputeGrounded(unittest.TestCase):
    """The compute maths against the worked example the whole design was validated on."""

    def _data(self):
        return ShipData(
            game="T",
            configs=[HullConfig("Standard", 0.0), HullConfig("Streamlined", 0.20)],
            thrust={2: DriveStep(2, 0.02, 10)},
            jump={2: DriveStep(2, 0.05, 11)},
            power_plants=[PowerPlant("Fusion (TL8)", 10.0, 0.5)],
            bridges=[(50, 3), (99, 6), (200, 10)],
        )

    def test_drive_tonnage(self):
        self.assertEqual(m_drive_tons(200, DriveStep(2, 0.02, 10)), 4)       # 2% of 200
        self.assertEqual(j_drive_tons(200, DriveStep(2, 0.05, 11)), 15)      # 5% of 200 + 5

    def test_j_drive_minimum(self):
        self.assertEqual(j_drive_tons(100, DriveStep(1, 0.025, 9)), 10)      # 2.5t+5 → min 10

    def test_power_required(self):
        self.assertEqual(power_required(200, 2, 2), 120)                     # 40 + 40 + 40

    def test_full_ship_matches_grounded_numbers(self):
        r = compute_ship(self._data(), hull_tons=200, config="Streamlined",
                         thrust=2, jump=2, power_plant="Fusion (TL8)", staterooms=3)
        by = {L.label.split(" —")[0].split(" ×")[0].split(" (")[0].strip(): L for L in r.lines}
        self.assertAlmostEqual(by["Hull"].cost, 12.0)          # 200 × Cr50k × 1.2
        self.assertEqual((by["M-Drive"].tons, by["M-Drive"].cost), (4, 8.0))    # 2% of 200, ×MCr2
        self.assertEqual((by["J-Drive"].tons, by["J-Drive"].cost), (15, 22.5))  # 5%+5, ×MCr1.5
        self.assertEqual((by["Power Plant"].tons, by["Power Plant"].cost), (12, 6.0))  # 120/10
        self.assertEqual((by["Bridge"].tons, by["Bridge"].cost), (10, 1.0))     # MCr0.5 × 200/100
        self.assertEqual(by["Fuel"].tons, 42)                  # 10%×200×2 jump + ceil(10%×12) plant
        self.assertEqual((by["Staterooms"].tons, by["Staterooms"].cost), (12, 1.5))  # 3 × 4t / MCr0.5
        self.assertEqual(by["Cargo hold"].tons, 105)           # 200 − 95 used
        self.assertEqual(r.tonnage_used, 200)                  # cargo absorbs the remainder
        self.assertEqual(r.tonnage_free, 0)
        self.assertEqual(r.warnings, [])

    def test_min_crew_suggestion(self):
        # pilot + astrogator (jump) + 1 engineer per 35t of drives+power (min 1)
        from lorehound.builders.ship import min_crew
        self.assertEqual(min_crew(jump=2, drive_power_tons=31), 3)   # 1 + 1 + 1
        self.assertEqual(min_crew(jump=0, drive_power_tons=10), 2)   # 1 + 0 + 1
        self.assertEqual(min_crew(jump=2, drive_power_tons=80), 5)   # 1 + 1 + ceil(80/35)=3

    def test_over_tonnage_warns(self):
        # A 10t hull can't hold a 10t (minimum) jump drive plus a bridge.
        r = compute_ship(self._data(), hull_tons=10, config="Standard",
                         thrust=2, jump=2, power_plant="Fusion (TL8)")
        self.assertGreater(r.tonnage_used, 10)
        self.assertTrue(any("over tonnage" in w for w in r.warnings))


class TestShipDataAssembly(unittest.TestCase):
    def test_ship_data_from_tables_is_ok_with_core_tables(self):
        tables = [
            _t("HULL CONFIGURATION", [["Hull Configuration", "Hull Cost"],
                                      ["Standard", "—"], ["Streamlined", "+20%"]]),
            _t("Thrust Potential", [["Manoeuvre Drive Rating", "1", "2"],
                                    ["% of Hull", "1%", "2%"], ["Manoeuvre TL", "9", "10"]]),
            _t("Jump Potential", [["Rating", "1", "2"],
                                  ["% of Hull + 5 tons", "2.5%", "5%"], ["Jump TL", "9", "11"]]),
            _t("POWER PLANT TYPE", [["Power Plant Type", "Power per Ton", "Cost per Ton"],
                                    ["Fusion (TL8)", "10", "MCr0.5"]]),
        ]
        data = ship_data_from_tables(tables, "Traveller", "High Guard")
        self.assertTrue(data.ok)
        self.assertIn(2, data.thrust)
        self.assertIn(2, data.jump)


class TestBuildShipData(unittest.TestCase):
    def test_from_rules_markdown_tables(self):
        def mt(title, rows):
            return MarkdownTable(1, title, rows, source="HG")
        mts = [
            mt("Hull Configuration", [["Hull Configuration", "Hull Cost"],
                                      ["Standard", "—"], ["Streamlined", "+20%"]]),
            mt("Thrust Potential", [["Manoeuvre Drive Rating", "1", "2"],
                                    ["% of Hull", "1%", "2%"], ["Manoeuvre TL", "9", "10"]]),
            mt("Jump Potential", [["Rating", "1", "2"],
                                  ["% of Hull + 5 tons", "2.5%", "5%"], ["Jump TL", "9", "11"]]),
            mt("Power Plant Type", [["Power Plant Type", "Power per Ton", "Cost per Ton"],
                                    ["Fusion (TL8)", "10", "MCr0.5"]]),
        ]
        rules = type("R", (), {"markdown_tables": {"Traveller": mts}})()
        data = build_ship_data(rules, "Traveller")
        self.assertTrue(data.ok)
        self.assertEqual(data.source, "HG")


class TestShipFlow(unittest.TestCase):
    def _data(self):
        return ShipData(
            game="T", source="High Guard",
            configs=[HullConfig("Standard", 0.0), HullConfig("Streamlined", 0.20)],
            thrust={2: DriveStep(2, 0.02, 10)}, jump={2: DriveStep(2, 0.05, 11)},
            power_plants=[PowerPlant("Fusion (TL8)", 10.0, 0.5)],
            bridges=[(200, 10)], computers=[Computer("Computer/5", 7, 0.03)], sensors=[],
        )

    def _session(self):
        data = self._data()
        return ChargenSession(ship_flow, mode=FAITHFUL, draft=ShipBuild(game="T"),
                              data=data, draft_factory=lambda: ShipBuild(game="T"))

    def test_flow_walks_steps_and_computes(self):
        s = self._session()
        # no sensors in the catalogue → the sensor step is skipped; staterooms follows computer
        for step_id, value in [("hull", "200"), ("config", "Streamlined"), ("thrust", "2"),
                               ("jump", "2"), ("power", "Fusion (TL8)"), ("computer", "Computer/5"),
                               ("staterooms", "3")]:
            self.assertEqual(s.current.id, step_id)
            s.resolve(value)
        self.assertTrue(s.complete)
        d = s.draft
        self.assertEqual((d.hull_tons, d.config, d.thrust, d.jump), (200, "Streamlined", 2, 2))
        self.assertEqual(d.staterooms, 3)
        self.assertEqual(d.tonnage_used, 200)                  # fuel + staterooms + cargo → full
        self.assertAlmostEqual(d.total_cost, 51.03)            # 49.53 + MCr1.5 staterooms
        self.assertEqual(d.source, "High Guard")

    def test_back_returns_to_previous_step(self):
        s = self._session()
        s.resolve("200")
        self.assertEqual(s.current.id, "config")
        self.assertTrue(s.can_back)
        s.back()
        self.assertEqual(s.current.id, "hull")

    def test_empty_catalogue_completes_without_a_ship(self):
        s = ChargenSession(ship_flow, mode=FAITHFUL, draft=ShipBuild(game="T"),
                           data=ShipData(game="T"), draft_factory=lambda: ShipBuild(game="T"))
        self.assertTrue(s.complete)
        self.assertEqual(s.draft.hull_tons, 0)


if __name__ == "__main__":
    unittest.main()
