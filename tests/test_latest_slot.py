"""LatestSlot: latest-value-wins single-slot buffer."""
import threading

from app.pipeline import LatestSlot


def test_starts_empty():
    assert LatestSlot().get() is None


def test_latest_wins():
    slot = LatestSlot()
    slot.put(1)
    slot.put(2)
    assert slot.get() == 2


def test_concurrent_writes_leave_a_valid_value():
    slot = LatestSlot()

    def writer(v):
        for _ in range(1000):
            slot.put(v)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert slot.get() in {0, 1, 2, 3}
