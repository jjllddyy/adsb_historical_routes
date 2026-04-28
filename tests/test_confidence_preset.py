"""Tests for ConfidencePreset and CLI override resolution."""

import argparse
import pytest


class TestPresets:
    def test_all_three_presets_exist(self, ahr):
        assert "strict" in ahr.PRESETS
        assert "balanced" in ahr.PRESETS
        assert "permissive" in ahr.PRESETS

    def test_strict_values(self, ahr):
        p = ahr.PRESETS["strict"]
        assert p.airport_radius_km == 20
        assert p.max_join_gap_hours == 2.0
        assert p.max_join_distance_km == 100
        assert p.route_time_tolerance == 0.25
        assert p.route_time_rescue is False
        assert p.direction_aware_rescue is False

    def test_balanced_values(self, ahr):
        p = ahr.PRESETS["balanced"]
        assert p.airport_radius_km == 50
        assert p.max_join_gap_hours == 3.0
        assert p.max_join_distance_km == 200
        assert p.route_time_tolerance == 0.40
        assert p.route_time_rescue is True
        assert p.direction_aware_rescue is True

    def test_permissive_values(self, ahr):
        p = ahr.PRESETS["permissive"]
        assert p.airport_radius_km == 100
        assert p.max_join_gap_hours == 4.0
        assert p.max_join_distance_km == 500
        assert p.route_time_tolerance == 0.60
        assert p.route_time_rescue is True
        assert p.direction_aware_rescue is True

    def test_preset_is_frozen(self, ahr):
        # Changing a preset value should raise (frozen dataclass)
        p = ahr.PRESETS["balanced"]
        with pytest.raises(Exception):
            p.airport_radius_km = 999


class TestResolvePreset:
    def _ns(self, **kwargs):
        defaults = dict(
            confidence="balanced",
            airport_radius_km=None,
            max_join_gap_hours=None,
            max_join_distance_km=None,
            route_time_tolerance=None,
            route_time_rescue=None,
        )
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_no_overrides_returns_named_preset(self, ahr):
        p = ahr.resolve_preset(self._ns(confidence="strict"))
        assert p.name == "strict"
        assert p.airport_radius_km == 20

    def test_single_override_changes_name_to_custom(self, ahr):
        p = ahr.resolve_preset(self._ns(confidence="balanced", airport_radius_km=75))
        assert p.name == "custom"
        assert p.airport_radius_km == 75
        assert p.max_join_gap_hours == 3.0  # other fields unchanged

    def test_route_time_rescue_override_off(self, ahr):
        p = ahr.resolve_preset(self._ns(confidence="balanced", route_time_rescue="off"))
        assert p.route_time_rescue is False
        assert p.name == "custom"

    def test_route_time_rescue_override_on(self, ahr):
        p = ahr.resolve_preset(self._ns(confidence="strict", route_time_rescue="on"))
        assert p.route_time_rescue is True
        assert p.name == "custom"
