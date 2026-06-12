import json
import os

from pipeline import batch_state as bs


def test_canonical_json_is_order_insensitive():
    a = bs.canonical_json({"b": 1, "a": [3, 2]})
    b = bs.canonical_json({"a": [3, 2], "b": 1})
    assert a == b


def test_compute_input_hash_stable_and_prefixed():
    params = {"model": "m", "max_tokens": 8192, "system": "S",
              "messages": [{"role": "user", "content": "x"}]}
    h1 = bs.compute_input_hash(params)
    h2 = bs.compute_input_hash(dict(reversed(list(params.items()))))
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_compute_input_hash_changes_with_prompt():
    p1 = {"system": "A", "messages": []}
    p2 = {"system": "B", "messages": []}
    assert bs.compute_input_hash(p1) != bs.compute_input_hash(p2)


def test_new_sidecar_shape():
    sc = bs.new_sidecar("2026-05-14", "claude-x")
    assert sc["schema_version"] == 1
    assert sc["date"] == "2026-05-14"
    assert sc["model"] == "claude-x"
    assert sc["retry_count"] == 0
    assert sc["attempts"] == []
    assert sc["meetings"] == []


def test_save_load_delete_roundtrip(tmp_path):
    path = str(tmp_path / "2026-05-14.json")
    sc = bs.new_sidecar("2026-05-14", "claude-x")
    bs.save_sidecar(path, sc)
    assert os.path.exists(path)
    loaded = bs.load_sidecar(path)
    assert loaded == sc
    bs.delete_sidecar(path)
    assert not os.path.exists(path)
    assert bs.load_sidecar(path) is None


def test_sidecar_path_lives_outside_threads():
    p = bs.sidecar_path("2026-05-14")
    assert p == os.path.join("data", "pending-batches", "2026-05-14.json")
    assert "threads" not in p
