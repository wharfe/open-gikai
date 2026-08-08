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
    # v2 and v3 each hash a different param set than ours — v2 predates
    # temperature=0 on summary requests, v3 carries it (removed in #51) — so
    # their hashes mismatch just like v1's.
    assert bs.is_current_schema({"schema_version": 2}) is False
    assert bs.is_current_schema({"schema_version": 3}) is False
    assert bs.is_current_schema({}) is False


def test_new_sidecar_shape():
    sc = bs.new_sidecar("2026-05-14", "claude-x")
    assert sc["schema_version"] == bs.SCHEMA_VERSION
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


# --- Failure regimes (#65) -------------------------------------------------

def test_failure_policy_classifies_the_three_regimes():
    assert bs.failure_policy("canceled") == bs.RESUBMIT
    assert bs.failure_policy("missing_result") == bs.RESUBMIT
    assert bs.failure_policy("speech_gap") == bs.HOLD
    assert bs.failure_policy("raw_date_missing") == bs.HOLD
    assert bs.failure_policy("hash_mismatch") == bs.BLOCKED
    assert bs.failure_policy("retry_exhausted") == bs.BLOCKED


def test_unknown_reason_is_blocked_not_resubmit():
    """The safe default is the one that never charges and never deletes data.
    Defaulting to RESUBMIT would silently bill a batch for a failure nobody has
    reasoned about; defaulting to an exception would take the publish down with
    it (Collect runs under `set -e`)."""
    assert bs.failure_policy("something_nobody_wrote_yet") == bs.BLOCKED


def test_terminal_failures_are_all_resubmittable():
    """TERMINAL_FAILURES are batch-side outcomes; a fresh submission is a new
    roll. If one ever needs a different regime it must be moved deliberately."""
    for status in bs.TERMINAL_FAILURES:
        assert bs.failure_policy(status) == bs.RESUBMIT, status


def test_assemble_failed_is_not_a_policy_reason():
    """The catch-all was what let hash_mismatch be treated as retryable. The
    detailed reason must reach the policy instead, so the old spelling must not
    quietly resolve to anything."""
    assert "assemble_failed" not in bs.FAILURE_POLICY


def test_mark_blocked_keeps_the_first_since_and_reports_no_change():
    sc = bs.new_sidecar("2026-05-14", "claude-x")
    assert bs.mark_blocked(sc, "hash_mismatch", "2026-08-08T00:00:00Z",
                           "M1", "s_abc_00") is True
    assert sc["blocked"]["since"] == "2026-08-08T00:00:00Z"
    # Second morning, same finding: `since` must not advance, and the caller must
    # be told nothing changed so it does not run an empty `git commit` (which
    # reports itself as "the in-flight batch will be orphaned").
    assert bs.mark_blocked(sc, "hash_mismatch", "2026-08-09T00:00:00Z",
                           "M1", "s_abc_00") is False
    assert sc["blocked"]["since"] == "2026-08-08T00:00:00Z"


def test_mark_blocked_reports_a_change_when_the_reason_changes():
    sc = bs.new_sidecar("2026-05-14", "claude-x")
    bs.mark_blocked(sc, "hash_mismatch", "2026-08-08T00:00:00Z")
    assert bs.mark_blocked(sc, "retry_exhausted", "2026-08-09T00:00:00Z") is True


def test_clear_blocked_is_safe_on_a_sidecar_that_was_never_blocked():
    sc = bs.new_sidecar("2026-05-14", "claude-x")
    bs.clear_blocked(sc)
    assert "blocked" not in sc


def test_failure_policy_covers_every_reason_in_summarize():
    """The allowlist must be derived, not remembered.

    Three vocabularies reach the policy and they live in different files:
    _diagnostic's first argument (assembly's observations), the reasons
    summarize.py passes literally (results_expired, stale_schema,
    retry_exhausted), and TERMINAL_FAILURES. A table that covers only the first
    is an allowlist that lies — exactly the failure CLAUDE.md warns about with
    "a forbid-list test approves what it can't recognize".

    Break-to-check: delete any single FAILURE_POLICY entry and this must fail.
    """
    import ast
    import os

    src_path = os.path.join(os.path.dirname(__file__), "..", "summarize.py")
    with open(src_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    reasons = set(bs.TERMINAL_FAILURES)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name == "_diagnostic" and node.args:
            first = node.args[0]
            # Non-literal calls — _diagnostic(reason) where reason came from a
            # policy branch — are skipped, not rejected. Every such value
            # originates from a literal _diagnostic elsewhere or from
            # TERMINAL_FAILURES, so the derived set stays complete; rejecting
            # them outright would just forbid the indirection the collect loop
            # needs. The floor assertion below is what keeps skipping honest:
            # if the sweep ever stops finding literals it fails instead of
            # approving an empty set.
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                reasons.add(first.value)
        # The reasons summarize.py hands the policy directly.
        if name in ("_apply_failure_policy", "mark_blocked"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    reasons.add(arg.value)

    # Sanity floor: a sweep that finds nothing approves everything. These names
    # are the ones the collect loop cannot work without, and the count catches a
    # refactor that turned the rest into indirection.
    assert {"hash_mismatch", "missing_result", "speech_gap", "raw_missing",
            "raw_date_missing", "thread_build_failed"} <= reasons, (
        "the AST sweep lost reasons it used to find — it is not looking where "
        "it thinks, or _diagnostic calls stopped being literal"
    )
    missing = sorted(reasons - set(bs.FAILURE_POLICY))
    assert not missing, f"reasons with no explicit policy: {missing}"
