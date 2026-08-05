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


def test_compute_input_hash_ignores_max_tokens():
    """A truncated request must be re-issuable at a higher ceiling without
    invalidating the manifest it belongs to."""
    base = {"model": "m", "system": "S", "messages": [{"role": "user", "content": "x"}]}
    small = bs.compute_input_hash({**base, "max_tokens": 8192})
    large = bs.compute_input_hash({**base, "max_tokens": 32768})
    absent = bs.compute_input_hash(base)
    assert small == large == absent


def test_hash_excluded_params_stays_narrow():
    """The exclusion set is a hole in what input_hash certifies. Only params that
    cannot change a token the model emits belong in it — anything that steers
    generation must stay hashed, or a resume could assemble a result a re-run
    would not reproduce (the determinism invariant in CLAUDE.md)."""
    steering = {"model", "system", "messages", "thinking",
                "temperature", "top_p", "top_k", "stop_sequences"}
    assert bs.HASH_EXCLUDED_PARAMS & steering == frozenset()
    assert bs.HASH_EXCLUDED_PARAMS == frozenset({"max_tokens"})


def test_is_current_schema_rejects_older_and_missing_versions():
    assert bs.is_current_schema(bs.new_sidecar("2026-05-14", "claude-x")) is True
    assert bs.is_current_schema({"schema_version": 1}) is False
    assert bs.is_current_schema({}) is False


def test_new_sidecar_shape():
    sc = bs.new_sidecar("2026-05-14", "claude-x")
    assert sc["schema_version"] == 2
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


def test_add_attempt_and_current_batch_id():
    sc = bs.new_sidecar("2026-05-14", "m")
    bs.add_attempt(sc, "msgbatch_A", "2026-06-11T21:50:00Z")
    assert bs.current_batch_id(sc) == "msgbatch_A"
    assert sc["attempts"][-1]["terminal_status"] is None


def test_record_terminal_increments_once_per_transition():
    sc = bs.new_sidecar("2026-05-14", "m")
    bs.add_attempt(sc, "msgbatch_A", "2026-06-11T21:50:00Z")
    # First observation of a failure transitions null -> expired: count.
    assert bs.record_terminal(sc, "expired", "2026-06-11T23:20:00Z") is True
    assert sc["retry_count"] == 1
    # Re-observing the same terminal on the same attempt must NOT double count.
    assert bs.record_terminal(sc, "expired", "2026-06-12T00:00:00Z") is False
    assert sc["retry_count"] == 1


def test_record_terminal_three_failures_across_attempts():
    sc = bs.new_sidecar("2026-05-14", "m")
    for i in range(3):
        bs.add_attempt(sc, f"msgbatch_{i}", "2026-06-11T21:50:00Z")
        assert bs.record_terminal(sc, "expired", "2026-06-11T23:20:00Z") is True
    assert sc["retry_count"] == 3
    assert bs.should_hard_fail(sc) is True


def test_should_hard_fail_below_threshold():
    sc = bs.new_sidecar("2026-05-14", "m")
    bs.add_attempt(sc, "msgbatch_A", "2026-06-11T21:50:00Z")
    bs.record_terminal(sc, "canceled", "2026-06-11T23:20:00Z")
    assert bs.should_hard_fail(sc) is False


def test_age_days_from_last_attempt():
    sc = bs.new_sidecar("2026-05-14", "m")
    bs.add_attempt(sc, "msgbatch_A", "2026-06-10T00:00:00Z")
    assert bs.age_days(sc, "2026-06-12T00:00:00Z") == 2.0


def test_is_stuck_uses_threshold():
    sc = bs.new_sidecar("2026-05-14", "m")
    bs.add_attempt(sc, "msgbatch_A", "2026-06-10T00:00:00Z")
    assert bs.is_stuck(sc, "2026-06-12T01:00:00Z") is True   # >2d
    assert bs.is_stuck(sc, "2026-06-11T00:00:00Z") is False  # 1d
