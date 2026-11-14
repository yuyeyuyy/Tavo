import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from app.core import security


def test_concurrent_rotations_keep_all_keys_and_existing_ciphertext(f, monkeypatch):
    account = f.add()
    original = f.services.secrets.for_connector(account["id"], "mock").get_secret_value()
    first_publish, release_first, second_started, second_publish = (Event() for _ in range(4))
    original_write = security.private_write
    counter, mutex = [0], Lock()

    def controlled_publish(path, data):
        if path == f.settings.keyring_file:
            with mutex:
                counter[0] += 1
                order = counter[0]
            if order == 1:
                first_publish.set()
                assert release_first.wait(5)
            else:
                second_publish.set()
        original_write(path, data)

    monkeypatch.setattr(security, "private_write", controlled_publish)

    def second_rotation():
        second_started.set()
        f.services.secrets.rotate()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(f.services.secrets.rotate)
        try:
            assert first_publish.wait(5)
            second = pool.submit(second_rotation)
            assert second_started.wait(5)
            # The second rotation cannot even publish while the first owns the DB writer lock.
            assert not second_publish.wait(0.1)
        finally:
            release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert second_publish.is_set()
    ring = json.loads(security.private_read(f.settings.keyring_file))
    assert len(ring["keys"]) == 3
    assert f.services.secrets.for_connector(account["id"], "mock").get_secret_value() == original
