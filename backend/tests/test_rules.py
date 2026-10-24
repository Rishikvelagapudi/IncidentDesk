import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis import Det, RuleEngine  # noqa: E402

SHAPE = (480, 640)


def run(frames, zone=None):
    eng, events = RuleEngine(zone), []
    for i, dets in enumerate(frames):
        events += eng.step(dets, i * 0.2, SHAPE)
    return {e["type"] for e in events}


def test_collision():
    frames = []
    for i in range(30):
        a = 20 + i * 14 if i < 20 else 20 + 19 * 14
        b = 600 - i * 14 if i < 20 else 600 - 19 * 14
        frames.append([Det(2, (a, 300, a + 80, 350), .9), Det(2, (b - 80, 305, b, 355), .9)])
    assert "collision" in run(frames)


def test_fall():
    upright = [[Det(0, (300, 200, 340, 300), .9)] for _ in range(12)]
    down = [[Det(0, (290, 270, 370, 300), .9)] for _ in range(4)]
    assert "fall" in run(upright + down)


def test_zone_entry():
    frames = [[Det(0, (500, 200, 540, 300), .9)] for _ in range(10)]
    assert "restricted_zone" in run(frames, zone=[0.6, 0.3, 1.0, 1.0])


def test_normal_traffic_is_quiet():
    frames = [[Det(2, (20 + i * 14, 300, 100 + i * 14, 350), .9), Det(2, (20 + i * 14, 400, 100 + i * 14, 450), .9)] for i in range(30)]
    assert run(frames) == set()


def test_congestion():
    frames = [[Det(2, (20 + 60 * k, 300, 70 + 60 * k, 340), .9) for k in range(9)] for _ in range(40)]
    assert "congestion" in run(frames)
