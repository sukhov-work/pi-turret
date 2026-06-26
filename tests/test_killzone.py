"""Kill-zone geometry."""
import pytest

from config import KillZoneConfig
from aim.killzone import distance_to_center_px, is_in_kill_zone

RECT = KillZoneConfig(shape="rect", cx_px=576, cy_px=576, half_w_px=100, half_h_px=50)
CIRCLE = KillZoneConfig(shape="circle", cx_px=576, cy_px=576, radius_px=100)


def test_rect_inside():
    assert is_in_kill_zone(576, 576, RECT)
    assert is_in_kill_zone(676, 626, RECT)        # on the corner boundary


def test_rect_outside():
    assert not is_in_kill_zone(677, 576, RECT)    # past half_w
    assert not is_in_kill_zone(576, 627, RECT)    # past half_h


def test_circle_inside_and_outside():
    assert is_in_kill_zone(576, 576, CIRCLE)
    assert is_in_kill_zone(576 + 100, 576, CIRCLE)    # on the radius
    assert not is_in_kill_zone(576 + 101, 576, CIRCLE)


def test_distance_to_center():
    assert distance_to_center_px(576, 576, RECT) == 0.0
    assert distance_to_center_px(576 + 30, 576 + 40, RECT) == pytest.approx(50.0)
