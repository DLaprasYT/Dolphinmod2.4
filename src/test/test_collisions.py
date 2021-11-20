"""Test the collisions module."""
from __future__ import annotations

import math
from typing import Tuple
from pathlib import Path
import pytest

from srctools import VMF, Vec, Property, Solid
from collisions import BBox, CollideType

tuple3 = Tuple[int, int, int]


def assert_bbox(
    bbox: BBox,
    mins: tuple3,
    maxes: tuple3,
    contents: CollideType,
    msg='',
) -> None:
    """Test the bbox matches the given values."""
    # Don't show in pytest tracebacks.
    __tracebackhide__ = True
    if msg:
        msg = ': ' + msg
    x1, y1, z1 = mins
    x2, y2, z2 = maxes
    if bbox.contents is not contents:
        pytest.fail(f'{bbox}.contents != {contents}{msg}')
    if (
        bbox.min_x != x1 or bbox.min_y != y1 or bbox.min_z != z1 or
        bbox.max_x != x2 or bbox.max_y != y2 or bbox.max_z != z2
    ):
        pytest.fail(f'{bbox}.mins != ({x1} {y1} {z1}) ({x2} {y2} {z2}){msg}')


def test_bbox_construction() -> None:
    bb = BBox(Vec(1, 2, 3), Vec(4, 5, 6))
    # Check assert_bbox() is correct.
    assert bb.min_x == 1
    assert bb.min_y == 2
    assert bb.min_z == 3
    assert bb.max_x == 4
    assert bb.max_y == 5
    assert bb.max_z == 6
    assert bb.contents is CollideType.SOLID
    assert_bbox(bb, (1, 2, 3), (4, 5, 6), CollideType.SOLID)

    assert_bbox(
        BBox(Vec(4, 2, 6), Vec(1, 5, 3), CollideType.FIZZLER | CollideType.ANTLINES),
        (1, 2, 3), (4, 5, 6),
        CollideType.FIZZLER | CollideType.ANTLINES,
    )
    assert_bbox(
        BBox((-50, 80, -60), (30, -40, 95), CollideType.GLASS),
        (-50, -40, -60), (30, 80, 95),
        CollideType.GLASS,
    )

    plane_x = BBox([80, 90, 10], [80, 250, 40], CollideType.GRATE)
    assert plane_x.is_plane
    assert plane_x.plane_normal == Vec(1, 0, 0)
    assert_bbox(plane_x, (80, 90, 10), (80, 250, 40), CollideType.GRATE)

    plane_y = BBox([80, 250, 10], [110, 250, 40], CollideType.GRATE)
    assert plane_y.is_plane
    assert plane_y.plane_normal == Vec(0, 1, 0)
    assert_bbox(plane_y, (80, 250, 10), (110, 250, 40), CollideType.GRATE)

    plane_z = BBox([80, 250, 40], [110, 90, 40], CollideType.GRATE)
    assert plane_z.is_plane
    assert plane_z.plane_normal == Vec(0, 0, 1)
    assert_bbox(plane_z, (80, 90, 40), (110, 250, 40), CollideType.GRATE)


def test_illegal_bbox() -> None:
    """A line or point segement is not allowed."""
    with pytest.raises(ValueError):
        BBox(Vec(1, 2, 3), Vec(1, 2, 3))
    with pytest.raises(ValueError):
        BBox(Vec(1, 2, 3), Vec(10, 2, 3))
    with pytest.raises(ValueError):
        BBox(Vec(1, 2, 3), Vec(1, 20, 3))
    with pytest.raises(ValueError):
        BBox(Vec(1, 2, 3), Vec(1, 2, 30))


def test_bbox_vecs() -> None:
    """Test that the vector properties don't return the same object."""
    bb = BBox((40, 60, 80), (120, 450, 730))
    assert bb.mins == Vec(40.0, 60.0, 80.0)
    assert bb.maxes == Vec(120.0, 450.0, 730.0)
    assert bb.mins is not bb.mins
    assert bb.maxes is not bb.maxes


def test_bbox_is_frozen() -> None:
    """Test modification is not possible."""
    bb = BBox((40, 60, 80), (120, 450, 730), CollideType.PHYSICS)
    with pytest.raises(AttributeError):
        bb.min_x = 100
    with pytest.raises(AttributeError):
        bb.min_y = 100
    with pytest.raises(AttributeError):
        bb.min_z = 100

    with pytest.raises(AttributeError):
        bb.max_x = 100
    with pytest.raises(AttributeError):
        bb.max_y = 100
    with pytest.raises(AttributeError):
        bb.max_z = 100

    with pytest.raises(AttributeError):
        bb.contents = CollideType.GRATE
    # Check all these assignments didn't actually do anything.
    assert_bbox(bb, (40, 60, 80), (120, 450, 730), CollideType.PHYSICS)


def test_bbox_hash() -> None:
    """Test hashability of bboxes."""
    bb = BBox((40, 60, 80), (120, 450, 730), CollideType.PHYSICS)
    hash(bb)  # Check it can be hashed.

    # Check each value changes the hash.
    assert hash(bb) != hash(BBox((45, 40, 80), (120, 450, 730), CollideType.PHYSICS))
    assert hash(bb) != hash(BBox((40, 59, 80), (120, 450, 730), CollideType.PHYSICS))
    assert hash(bb) != hash(BBox((40, 60, 81), (120, 450, 730), CollideType.PHYSICS))
    assert hash(bb) != hash(BBox((40, 60, 80), (121, 450, 730), CollideType.PHYSICS))
    assert hash(bb) != hash(BBox((40, 60, 80), (120, 455, 730), CollideType.PHYSICS))
    assert hash(bb) != hash(BBox((40, 60, 80), (120, 450, 732), CollideType.PHYSICS))
    assert hash(bb) != hash(BBox((40, 60, 80), (120, 450, 730), CollideType.ANTLINES))


def reorder(coord: tuple3, order: str, x: int, y: int, z: int) -> tuple3:
    """Reorder the coords by these axes."""
    assoc = dict(zip('xyz', coord))
    return x + assoc[order[0]], y + assoc[order[1]], z + assoc[order[2]],


def test_reorder_helper() -> None:
    """Test the reorder helper."""
    assert reorder((1, 2, 3), 'xyz', 0, 0, 0) == (1, 2, 3)
    assert reorder((1, 2, 3), 'yzx', 0, 0, 0) == (2, 3, 1)
    assert reorder((1, 2, 3), 'zyx', 0, 0, 0) == (3, 2, 1)
    assert reorder((1, 2, 3), 'xzy', 0, 0, 0) == (1, 3, 2)
    assert reorder((-10, 30, 0), 'xyz', 8, 6, 12) == (-2, 36, 12)


def get_intersect_testcases() -> list:
    """Use a VMF to make it easier to generate the bounding boxes."""
    with Path(__file__, '../bbox_samples.vmf').open() as f:
        vmf = VMF.parse(Property.parse(f))

    def process(brush: Solid | None) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
        """Extract the bounding box from the brush."""
        if brush is None:
            return None
        bb_min, bb_max = brush.get_bbox()
        for vec in [bb_min, bb_max]:
            for ax in 'xyz':
                # If one thick, make zero thick so we can test planes.
                if abs(vec[ax]) == 63:
                    vec[ax] = math.copysign(64, vec[ax])
        return (tuple(map(int, bb_min)), tuple(map(int, bb_max)))

    for ent in vmf.entities:
        test = expected = None
        for solid in ent.solids:
            if solid.sides[0].mat.casefold() == 'tools/toolsskip':
                expected = solid
            if solid.sides[0].mat.casefold() == 'tools/toolstrigger':
                test = solid
        if test is None:
            raise ValueError(ent.id)
        yield (*process(test), process(expected))


@pytest.mark.parametrize('mins, maxs, success', list(get_intersect_testcases()))
@pytest.mark.parametrize('axes', ['xyz', 'yxz', 'zxy'])
@pytest.mark.parametrize('x', [-128, 0, 129])
@pytest.mark.parametrize('y', [-128, 0, 129])
@pytest.mark.parametrize('z', [-128, 0, 129])
def test_bbox_intersection(
    mins: tuple3, maxs: tuple3,
    x: int, y: int, z: int,
    success: tuple[tuple3, tuple3] | None, axes: str,
) -> None:
    """Test intersection founction for bounding boxes.

    We parameterise by swapping all the axes, and offsetting so it's in all the quadrants.
    """
    bbox1 = BBox((x-64, y-64, z-64), (x+64, y+64, z+64), CollideType.EVERYTHING)
    bbox2 = BBox(reorder(mins, axes, x, y, z), reorder(maxs, axes, x, y, z), CollideType.EVERYTHING)
    result = bbox1.intersect(bbox2)
    # assert result == bbox2.intersect(bbox1)  # Check order is irrelevant.
    if success is None:
        assert result is None
    else:
        exp_a, exp_b = success
        expected = BBox(reorder(exp_a, axes, x, y, z), reorder(exp_b, axes, x, y, z), CollideType.EVERYTHING)
        assert result == expected
