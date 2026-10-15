"""The detector is tested on synthetic scenes whose right answer is known by construction."""
import numpy as np
import pytest

from warm_areas import (PRESETS, building_baseline, classify_excess, detect_warm_areas,
                        severity_at)

H, W = 120, 160


def scene(sky=True, seed=1):
    """Wall at 9 degC (+/-0.15 noise); optionally a very cold sky across the top 35%."""
    rng = np.random.default_rng(seed)
    t = 9.0 + rng.normal(0, 0.15, (H, W))
    if sky:
        t[:42, :] = -30.0 + rng.normal(0, 0.5, (42, W))
    return t


def near(spot, row, col, tol=6):
    return abs(spot.location[0] - row) <= tol and abs(spot.location[1] - col) <= tol


def test_warm_window_found_with_cold_sky_in_frame():
    t = scene()
    t[60:70, 70:82] += 3.0                                   # window 3 degC warmer than the wall
    spots = detect_warm_areas(t, "medium")
    assert len(spots) == 1 and near(spots[0], 65, 76)
    # a point reports its hottest pixel, so the patch's noise adds a few tenths to the +3.0 step
    assert 2.9 < spots[0].excess < 3.8 and spots[0].severity == "medium"


def test_the_old_detector_would_have_missed_it():
    """Regression guard: the same scene defeats 'mean + 2 std' because the sky inflates std."""
    from thermal_analyzer import ThermalAnalyzer
    t = scene()
    t[60:70, 70:82] += 3.0
    assert ThermalAnalyzer(sensitivity="medium").detect_hot_spots(t) == []
    assert len(detect_warm_areas(t, "medium")) == 1


def test_uniform_wall_with_noise_gives_no_points():
    for seed in range(5):
        assert detect_warm_areas(scene(seed=seed), "medium") == []


def test_building_edge_against_sky_is_not_a_hot_spot():
    t = scene()
    t[42:46, :] = 5.0                                         # blurred roof edge, colder than wall
    assert detect_warm_areas(t, "high") == []


def test_chimney_standing_in_the_sky_is_not_a_hot_spot():
    t = scene()
    t[20:42, 100:106] = 9.0                                   # thin wall-temperature chimney up into the sky
    assert detect_warm_areas(t, "high") == []


def test_single_hot_pixel_and_tiny_specks_are_ignored():
    t = scene()
    t[70, 50] += 15.0
    t[80:82, 90:92] += 4.0                                    # 2x2 speck (4 px but smoothing flattens it)
    assert detect_warm_areas(t, "low") == []


def test_cold_ground_does_not_hide_warm_areas():
    t = scene()
    t[95:, :] = -8.0                                          # cold foreground
    t[60:66, 20:40] += 2.0
    spots = detect_warm_areas(t, "medium")
    assert len(spots) == 1 and near(spots[0], 63, 30, tol=8)


def test_strongest_first_and_capped():
    t = scene()
    for i in range(6):
        t[50 + 10 * (i // 3): 56 + 10 * (i // 3), 10 + 50 * (i % 3): 24 + 50 * (i % 3)] += 2.0 + i * 0.5
    spots = detect_warm_areas(t, "medium", max_spots=4)
    assert len(spots) == 4
    assert [s.excess for s in spots] == sorted((s.excess for s in spots), reverse=True)


def test_sensitivity_levels_are_ordered():
    t = scene()
    t[60:68, 20:36] += 1.1          # subtle
    t[60:68, 80:96] += 1.8          # moderate
    t[60:68, 130:146] += 4.5        # obvious
    counts = [len(detect_warm_areas(t, s)) for s in ("low", "medium", "high")]
    assert counts == sorted(counts) and counts[0] == 1 and counts[2] == 3


def test_points_are_never_placed_on_the_outer_frame():
    t = scene()
    t[60:70, 0:20] += 3.0           # warm area running off the left edge
    spots = detect_warm_areas(t, "medium")
    assert spots and all(s.location[1] >= 2 and s.location[0] >= 2 for s in spots)


def test_degenerate_inputs_do_not_crash():
    assert detect_warm_areas(np.full((H, W), np.nan)) == []
    assert detect_warm_areas(np.full((H, W), 20.0)) == []
    assert detect_warm_areas(np.zeros((3, 3))) == []
    t = scene(); t[10, 10] = np.nan; t[60:70, 70:82] += 3.0
    assert len(detect_warm_areas(t)) == 1


def test_severity_scale_and_probe_agree():
    assert [classify_excess(x) for x in (0.5, 2.0, 4.0, 8.0, 20)] == ["low", "medium", "high", "critical", "critical"]
    t = scene(); t[60:70, 70:82] += 5.0
    excess, sev = severity_at(t, 65, 76)
    assert 4.7 < excess < 5.3 and sev == "high"
    assert severity_at(t, 100, 5)[1] == "low"                 # plain wall
    base, sigma, ground = building_baseline(t)
    assert 8.9 < base < 9.1 and sigma < 0.5 and not ground[:30].any()


def test_unknown_sensitivity_falls_back_to_medium():
    t = scene(); t[60:70, 70:82] += 3.0
    assert len(detect_warm_areas(t, "bogus")) == len(detect_warm_areas(t, "medium"))
    assert set(PRESETS) == {"low", "medium", "high"}
