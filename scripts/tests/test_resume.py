from pipeline import batch_state as bs
import summarize


def _prep(meeting_id, threads):
    """Mimic prepare_meeting_for_batch's output shape."""
    return {
        "meeting_id": meeting_id,
        "outcome": {"result": None, "resolution": None, "status": "ongoing"},
        "pending": [
            {
                "custom_id": t["custom_id"],
                "meeting": {"house": "参議院", "meeting": "外交防衛委員会"},
                "thread_info": t["thread_info"],
                "thread_speeches": t["speeches"],
            }
            for t in threads
        ],
    }


def test_build_manifest_meetings_captures_full_thread_info_and_hash():
    threads = [{
        "custom_id": "s_abc_00",
        "thread_info": {"topic": "T", "topicTag": "tag", "topicColor": "#111",
                        "summary": "s", "speechOrders": [1, 2]},
        "speeches": [{"speechOrder": 1, "speech": "a"}, {"speechOrder": 2, "speech": "b"}],
    }]
    prepared = [_prep("M1", threads)]
    manifest = summarize.build_manifest_meetings(prepared, model="claude-x")
    assert len(manifest) == 1
    m = manifest[0]
    assert m["meeting_id"] == "M1"
    t = m["threads"][0]
    assert t["custom_id"] == "s_abc_00"
    assert t["thread_idx"] == 0
    assert t["thread_info"]["topicTag"] == "tag"   # FULL thread_info, not a subset
    assert t["speechOrders"] == [1, 2]
    assert t["input_hash"].startswith("sha256:")


def _sidecar_with_one_thread(input_hash):
    return {
        # Must track bs.SCHEMA_VERSION: a sidecar labelled with an older schema is
        # refused outright, so hardcoding 1 here would silently stop exercising
        # every collect/repair path the moment the schema moves.
        "schema_version": bs.SCHEMA_VERSION,
        "date": "2026-05-14", "model": "claude-x",
        "retry_count": 0,
        "attempts": [{"batch_id": "b1", "submitted_at": "2026-06-11T21:50:00Z",
                      "terminal_status": None, "terminal_at": None}],
        "meetings": [{
            "meeting_id": "M1",
            "outcome": {"result": None, "resolution": None, "status": "ongoing"},
            "threads": [{
                "custom_id": "s_abc_00", "thread_idx": 0,
                "thread_info": {"topic": "T", "topicTag": "tag", "topicColor": "#111",
                                "summary": "s", "speechOrders": [1]},
                "speechOrders": [1], "input_hash": input_hash,
            }],
        }],
    }


def _meeting():
    return {"meetingId": "M1", "house": "参議院", "meeting": "外交防衛委員会",
            "date": "2026-05-14", "source": "ndl",
            "speeches": [{"speechOrder": 1, "speech": "a", "speaker": "X",
                          "speakerGroup": "G", "speakerPosition": "P",
                          "speechURL": "http://x"}]}


def _correct_hash():
    m = _meeting()
    ti = {"topic": "T", "topicTag": "tag", "topicColor": "#111", "summary": "s",
          "speechOrders": [1]}
    req = summarize.build_summary_request(m, ti, [m["speeches"][0]], "s_abc_00", "claude-x")
    return bs.compute_input_hash(req["params"])


def test_assemble_from_manifest_success():
    sidecar = _sidecar_with_one_thread(_correct_hash())
    results = {"s_abc_00": {"speeches": [{"speechOrder": 1, "tension": "確認",
               "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
               "commitments": []}}
    threads, ok, _ = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results, members={}, thread_counter=0,
    )
    assert ok is True
    assert len(threads) == 1
    assert threads[0]["topicTag"] == "tag"


def test_assemble_fails_on_hash_mismatch():
    sidecar = _sidecar_with_one_thread("sha256:deadbeef")  # wrong
    results = {"s_abc_00": {"speeches": [{"speechOrder": 1}], "commitments": []}}
    threads, ok, _ = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results, members={}, thread_counter=0,
    )
    assert ok is False


def test_assemble_fails_on_missing_result():
    sidecar = _sidecar_with_one_thread(_correct_hash())
    threads, ok, _ = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results={}, members={}, thread_counter=0,
    )
    assert ok is False


def test_assembly_reports_a_missing_result_as_a_thread_scoped_diagnostic():
    sidecar = _sidecar_with_one_thread(_correct_hash())   # correct hash: we want
                                                          # to reach the result check
    threads, ok, diagnostic = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results={}, members={}, thread_counter=0,
    )
    assert (threads, ok) == ([], False)
    assert diagnostic["reason"] == "missing_result"
    assert diagnostic["scope"] == "thread"
    assert diagnostic["meeting_id"] == "M1"
    assert diagnostic["custom_id"] == "s_abc_00"


def test_assembly_reports_a_hash_mismatch_before_it_looks_for_results():
    """Order matters: the hash is checked first, so a stale sidecar reports
    hash_mismatch even when the results are also absent. An annotation that
    said missing_result here would send the reader to the API instead of raw."""
    sidecar = _sidecar_with_one_thread("sha256:deadbeef")
    threads, ok, diagnostic = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results={}, members={}, thread_counter=0,
    )
    assert diagnostic["reason"] == "hash_mismatch"
    assert diagnostic["scope"] == "thread"


def test_assembly_reports_a_missing_meeting_without_a_custom_id():
    """raw_missing happens before the thread loop, so there is no custom_id.

    Filling one in would point an annotation at a thread that was never
    examined — the same fiction this design refuses to put in the tally.
    """
    sidecar = _sidecar_with_one_thread(_correct_hash())
    threads, ok, diagnostic = summarize.assemble_from_manifest(
        sidecar, meetings_by_id={}, results={}, members={}, thread_counter=0,
    )
    assert (threads, ok) == ([], False)
    assert diagnostic["reason"] == "raw_missing"
    assert diagnostic["scope"] == "meeting"
    assert diagnostic["meeting_id"] == "M1"
    assert diagnostic["custom_id"] is None


def test_assembly_returns_no_diagnostic_when_it_succeeds():
    """The diagnostic must be None on success, not an empty dict."""
    sidecar = _sidecar_with_one_thread(_correct_hash())
    results = {"s_abc_00": {"speeches": [{"speechOrder": 1, "tension": "確認",
               "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
               "commitments": []}}
    threads, ok, diagnostic = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results, members={}, thread_counter=0,
    )
    assert ok is True
    assert diagnostic is None


import os


def test_run_batch_phase_persists_sidecar_when_pending(fake_client, tmp_path, monkeypatch):
    # One meeting, grouping stubbed to a single thread; batch stays in_progress.
    monkeypatch.setattr(summarize, "group_meeting",
                        lambda c, m, model: [{"topic": "T", "topicTag": "tag",
                                              "topicColor": "#111", "summary": "s",
                                              "speechOrders": [1]}])
    monkeypatch.setattr(summarize, "extract_meeting_outcome",
                        lambda c, m, model: {"result": None, "resolution": None,
                                             "status": "ongoing"})
    pending_dir = str(tmp_path / "pending")
    meeting = _meeting()
    fake_client.messages.batches.statuses["msgbatch_fake_0001"] = "in_progress"

    phase = summarize.run_batch_phase(
        fake_client, [meeting], {"completed": [], "failed": []},
        members={}, model="claude-x", date_str="2026-05-14", thread_counter=0,
        batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=pending_dir, ci_commit=False,
    )
    assert phase["pending"] is True
    assert phase["threads"] == []
    sc = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    assert sc is not None
    assert bs.current_batch_id(sc) == "msgbatch_fake_0001"
    assert sc["meetings"][0]["threads"][0]["input_hash"].startswith("sha256:")


def test_collect_assembles_ended_and_deletes_sidecar(fake_client, tmp_path, monkeypatch):
    pending_dir = str(tmp_path / "pending")
    threads_dir = str(tmp_path / "threads")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)
    # Write raw for the date so collect can re-fetch from disk.
    import json as _json
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        _json.dump({"meetings": [_meeting()]}, f, ensure_ascii=False)
    # Sidecar with a correct hash, batch now ended with a result.
    sidecar = _sidecar_with_one_thread(_correct_hash())
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"
    from tests.conftest import _ResultEntry  # type: ignore
    import json as J
    b.results_by_id["b1"] = [_ResultEntry("s_abc_00", "succeeded",
        text=J.dumps({"speeches": [{"speechOrder": 1, "tension": "確認",
        "summaries": {"easy": "e", "teen": "t", "adult": "a"}}], "commitments": []}))]

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert result["hard_fail"] is False
    assert not os.path.exists(os.path.join(pending_dir, "2026-05-14.json"))
    assert os.path.exists(os.path.join(threads_dir, "2026-05-14.json"))


def test_a_retry_exhausted_sidecar_is_held_not_a_hard_fail(fake_client, tmp_path):
    """T2d, entry path. Three genuine resubmits have failed and a human is needed
    — but Collect exits 1 under `set -e`, which skips summarize/commit/push for
    every OTHER date too. That was tolerable while one sidecar skipped Summarize
    anyway; with the per-date gate it is the #52 amplification again."""
    pending_dir = str(tmp_path / "pending")
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["retry_count"] = 3
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"),
        raw_dir=str(tmp_path / "r"),
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert result["hard_fail"] is False
    assert result["held_dates"] == ["2026-05-14"]
    assert fake_client.messages.batches.created_requests == []
    kept = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    assert kept["blocked"]["reason"] == "retry_exhausted"


def test_the_third_failed_resubmit_becomes_held_in_the_same_run(fake_client, tmp_path, monkeypatch):
    """T2d, threshold path. The count reaches 3 inside _apply_failure_policy; it
    must convert to retry_exhausted there and not submit a fourth batch."""
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["retry_count"] = 2
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[sidecar], results={}, existing_threads=0)
    assert result["hard_fail"] is False
    assert result["held_dates"] == ["2026-05-14"]
    assert fake_client.messages.batches.created_requests == []
    kept = bs.load_sidecar(str(tmp_path / "pending" / "2026-05-14.json"))
    assert kept["retry_count"] == 3
    assert kept["blocked"]["reason"] == "retry_exhausted"


def test_a_held_sidecar_does_not_stop_the_next_one(fake_client, tmp_path, monkeypatch):
    """T2c's real point. `hard_fail = True; continue` returned exit 1 at the end,
    so every later sidecar's work was thrown away with the run. Holding must be
    per-sidecar."""
    stale = _sidecar_with_one_thread(_correct_hash())
    stale["schema_version"] = 1
    stale["date"] = "2026-05-13"
    stale["attempts"][-1]["batch_id"] = "b0"
    good = _sidecar_with_one_thread(_correct_hash())
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[stale, good],
                          results={"s_abc_00": {"speeches": [{"speechOrder": 1,
                                   "tension": "確認",
                                   "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
                                   "commitments": []}},
                          existing_threads=0)
    assert result["hard_fail"] is False
    assert result["held_dates"] == ["2026-05-13"]
    # The good sidecar was collected and removed despite the held one above it.
    assert not os.path.exists(str(tmp_path / "pending" / "2026-05-14.json"))


def test_collect_survives_expired_results_and_resubmits(fake_client, tmp_path):
    """An 'ended' batch whose results have expired (results_url gone) must not
    crash the run. With raw still available, it resubmits from the manifest."""
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)
    import json as _json
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        _json.dump({"meetings": [_meeting()]}, f, ensure_ascii=False)
    sidecar = _sidecar_with_one_thread(_correct_hash())  # current batch id "b1"
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"
    b.expired_results.add("b1")          # results_url is gone -> .results() raises
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert result["hard_fail"] is False
    sc = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    assert sc is not None                       # kept — resubmitted, not abandoned
    assert sc["retry_count"] == 1
    assert bs.current_batch_id(sc) == "msgbatch_resub_1"


def test_collect_abandons_uncollectable_old_sidecar(fake_client, tmp_path, monkeypatch):
    """Raw has aged out of the fetch window (no raw on disk) and the sidecar is
    older than the abandon threshold -> it is structurally unrecoverable and must
    be deleted, not kept forever nor crash on expired results."""
    monkeypatch.setattr(summarize, "_utcnow_iso", lambda: "2026-07-15T00:00:00Z")
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)                          # empty — no raw for the date
    sidecar = _sidecar_with_one_thread(_correct_hash())
    # Last attempt submitted 2026-06-13 -> ~32 days old at the pinned "now".
    sidecar["attempts"][-1]["submitted_at"] = "2026-06-13T21:35:21Z"
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"
    b.expired_results.add("b1")                   # results also expired

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert result["hard_fail"] is False
    assert not os.path.exists(os.path.join(pending_dir, "2026-05-14.json"))  # abandoned


def test_collect_keeps_young_sidecar_when_raw_missing(fake_client, tmp_path, monkeypatch):
    """Raw missing but the sidecar is still young (within the fetch window) — the
    miss may be transient (raw not fetched this run), so keep it for next time."""
    monkeypatch.setattr(summarize, "_utcnow_iso", lambda: "2026-07-15T00:00:00Z")
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)                          # empty — no raw for the date
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["attempts"][-1]["submitted_at"] = "2026-07-14T00:00:00Z"  # ~1 day old
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert result["hard_fail"] is False
    assert os.path.exists(os.path.join(pending_dir, "2026-05-14.json"))  # kept


def test_collect_resubmits_on_expired(fake_client, tmp_path):
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)
    import json as _json
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        _json.dump({"meetings": [_meeting()]}, f, ensure_ascii=False)
    sidecar = _sidecar_with_one_thread(_correct_hash())  # current batch id "b1"
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    b = fake_client.messages.batches
    b.statuses["b1"] = "expired"
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert result["hard_fail"] is False
    sc = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    assert sc is not None                       # kept — resubmitted, not abandoned
    assert sc["retry_count"] == 1               # the expired batch counted once
    assert bs.current_batch_id(sc) == "msgbatch_resub_1"  # a new attempt was pushed
    assert len(sc["attempts"]) == 2


# --- Truncated batch results (regression: 2026-06-16 deadlock) ---------------
#
# Two of that date's 90 requests hit max_tokens and returned unparseable JSON.
# Assembly is all-or-nothing, so the 88 good summaries were discarded and the
# whole batch was resubmitted — repeatedly, since the same request truncates
# again. The repair pass re-issues only the unusable requests, at a higher
# ceiling, before assembly runs.

_GOOD_BODY = {
    "speeches": [{"speechOrder": 1, "tension": "確認",
                  "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
    "commitments": [],
}


def _pending_with_truncated_result(fake_client, tmp_path, stop_reason="max_tokens"):
    """One-thread sidecar whose batch came back truncated (unparseable)."""
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)
    import json as _json
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        _json.dump({"meetings": [_meeting()]}, f, ensure_ascii=False)
    sidecar = _sidecar_with_one_thread(_correct_hash())
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"
    from tests.conftest import _ResultEntry  # type: ignore
    b.results_by_id["b1"] = [_ResultEntry(
        "s_abc_00", "succeeded",
        text='{"speeches": [{"speechOrder": 1, "tensi',   # cut mid-JSON
        stop_reason=stop_reason,
    )]
    return pending_dir, raw_dir


def test_collect_repairs_truncated_result_instead_of_resubmitting(fake_client, tmp_path):
    import json as J
    pending_dir, raw_dir = _pending_with_truncated_result(fake_client, tmp_path)
    threads_dir = str(tmp_path / "threads")
    fake_client.messages.create_text = J.dumps(_GOOD_BODY)

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert result["hard_fail"] is False
    # Repaired and assembled: sidecar gone, threads written, no resubmit.
    assert not os.path.exists(os.path.join(pending_dir, "2026-05-14.json"))
    assert os.path.exists(os.path.join(threads_dir, "2026-05-14.json"))
    assert fake_client.messages.batches.created_requests == []
    # The re-issue used the larger ceiling.
    assert len(fake_client.messages.create_calls) == 1
    from pipeline.summarizer import SUMMARY_RETRY_MAX_TOKENS
    call = fake_client.messages.create_calls[0]
    assert call["max_tokens"] == SUMMARY_RETRY_MAX_TOKENS
    # ...and opted out of the SDK's non-streaming guard, which otherwise raises a
    # bare ValueError before the request is sent. Without this the whole repair
    # path is dead code that crashes the run (the fake client reproduces the
    # guard, so dropping the timeout fails this test rather than shipping).
    assert "timeout" in call


def test_repair_defers_on_transient_api_error(fake_client, tmp_path):
    """A 429/529/connection blip must NOT read as a repair failure: that fails
    assembly, resubmits all N, and spends one of the three retry slots. The next
    run collects the same ended batch for free instead."""
    import anthropic
    import pytest as _pytest
    pending_dir, raw_dir = _pending_with_truncated_result(fake_client, tmp_path)

    def _boom(**params):
        raise anthropic.APIConnectionError(request=None)

    fake_client.messages.create = _boom

    with _pytest.raises(anthropic.APIConnectionError):
        summarize.collect_pending_batches(
            fake_client, members={}, model="claude-x",
            pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
            budget_seconds=0, poll_seconds=0, ci_commit=False,
        )

    sc = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    assert sc is not None
    assert sc["retry_count"] == 0                        # budget untouched
    assert fake_client.messages.batches.created_requests == []   # no resubmit


def test_repair_rejects_reissue_covering_none_of_its_speeches(fake_client, tmp_path):
    """Zero overlap with the manifest is not a judgement call: assemble_thread
    would return None and fail the date anyway, so it counts as a failed
    re-issue rather than a stored result."""
    import json as J
    pending_dir, raw_dir = _pending_with_truncated_result(fake_client, tmp_path)
    threads_dir = str(tmp_path / "threads")
    # Manifest says speechOrders == [1]; the re-issue covers a different order.
    fake_client.messages.create_text = J.dumps({
        "speeches": [{"speechOrder": 7, "tension": "確認",
                      "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
        "commitments": [],
    })
    b = fake_client.messages.batches
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"

    summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert not os.path.exists(os.path.join(threads_dir, "2026-05-14.json"))
    assert bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json")) is not None


def test_repair_accepts_a_partially_covering_reissue(fake_client, tmp_path, caplog):
    """Deliberate: the prompt never promises one entry per input speech and batch
    results are held to no such bar, so rejecting a partial re-issue would resubmit
    all N and deterministically fail — #46's deadlock, re-entered. Accept it and
    say so, because losing a whole date is worse than a thread that is short."""
    import json as J
    import logging
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    threads_dir = str(tmp_path / "threads")
    os.makedirs(raw_dir)

    meeting = _meeting()
    meeting["speeches"] = [
        {"speechOrder": i, "speech": f"speech-{i}", "speaker": "X",
         "speakerGroup": "G", "speakerPosition": "P", "speechURL": "http://x"}
        for i in (1, 2)
    ]
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        J.dump({"meetings": [meeting]}, f, ensure_ascii=False)

    ti = {"topic": "T", "topicTag": "tag", "topicColor": "#111", "summary": "s",
          "speechOrders": [1, 2]}
    req = summarize.build_summary_request(
        meeting, ti, meeting["speeches"], "s_abc_00", "claude-x")
    sidecar = _sidecar_with_one_thread(bs.compute_input_hash(req["params"]))
    sidecar["meetings"][0]["threads"][0]["thread_info"] = ti
    sidecar["meetings"][0]["threads"][0]["speechOrders"] = [1, 2]
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)

    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"
    from tests.conftest import _ResultEntry  # type: ignore
    b.results_by_id["b1"] = [_ResultEntry(
        "s_abc_00", "succeeded", text='{"speeches": [{"speechOr',
        stop_reason="max_tokens")]
    # Covers order 1 but not order 2 — a strict subset, the discriminating case.
    fake_client.messages.create_text = J.dumps({
        "speeches": [{"speechOrder": 1, "tension": "確認",
                      "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
        "commitments": [],
    })

    with caplog.at_level(logging.WARNING):
        result = summarize.collect_pending_batches(
            fake_client, members={}, model="claude-x",
            pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
            budget_seconds=0, poll_seconds=0, ci_commit=False,
        )

    assert result["hard_fail"] is False
    assert os.path.exists(os.path.join(threads_dir, "2026-05-14.json"))   # accepted
    assert b.created_requests == []                                       # no resubmit
    assert "covers 1/2 manifest speeches" in caplog.text                  # and reported


def test_repair_targets_a_result_that_parsed_but_has_no_speeches(fake_client, tmp_path):
    """Valid JSON with an empty speeches array is truthy, so it used to slip past
    the repair pass — then assemble_thread returns None and the date takes the
    full-resubmit path that fails identically every run."""
    import json as J
    pending_dir, raw_dir = _pending_with_truncated_result(fake_client, tmp_path)
    threads_dir = str(tmp_path / "threads")
    b = fake_client.messages.batches
    from tests.conftest import _ResultEntry  # type: ignore
    b.results_by_id["b1"] = [_ResultEntry(
        "s_abc_00", "succeeded",
        text='{"speeches": [], "commitments": []}',    # parses, but empty
        stop_reason="end_turn",
    )]
    fake_client.messages.create_text = J.dumps(_GOOD_BODY)

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert result["hard_fail"] is False
    assert len(fake_client.messages.create_calls) == 1        # it WAS repaired
    assert os.path.exists(os.path.join(threads_dir, "2026-05-14.json"))
    assert b.created_requests == []


def test_repair_swallows_a_deterministic_api_error_and_keeps_going(fake_client, tmp_path):
    """The mirror of the transient case: a 4xx is deterministic, so aborting the
    run on it would skip every later sidecar for nothing. Log and move on."""
    import anthropic
    import httpx
    pending_dir, raw_dir = _pending_with_truncated_result(fake_client, tmp_path)

    def _bad_request(**params):
        raise anthropic.BadRequestError(
            "bad", response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
            body=None,
        )

    fake_client.messages.create = _bad_request
    b = fake_client.messages.batches
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert result["hard_fail"] is False                       # no crash, loop completed
    sc = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    assert sc["retry_count"] == 1              # fell through to the resubmit path


def test_repair_stops_when_its_own_budget_is_gone(fake_client, tmp_path):
    """The budget gates starting a new re-issue, so an exhausted one must spend
    nothing at all rather than overrunning the CI job."""
    import json as J
    pending_dir, raw_dir = _pending_with_truncated_result(fake_client, tmp_path)
    sidecar = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    meetings_by_id = summarize._load_meetings_for_date("2026-05-14", raw_dir)
    fake_client.messages.create_text = J.dumps(_GOOD_BODY)
    results = {"s_abc_00": None}

    repaired = summarize._repair_unusable_results(
        fake_client, sidecar, meetings_by_id, results, "claude-x",
        budget_seconds=0,
    )

    assert repaired == 0
    assert fake_client.messages.create_calls == []
    assert results["s_abc_00"] is None


def test_collect_holds_a_sidecar_from_an_older_schema(fake_client, tmp_path):
    """v1 hashes were computed over a different param set, so every thread would
    fail verification. Silently resubmitting burns a retry slot per run until the
    date is permanently lost, so refuse loudly and leave it rescuable by hand.
    Loudly now means held + red, not exit 1: since #44 the per-date gate lets the
    other dates publish, so stopping the job buys nothing and costs a morning.
    """
    pending_dir, raw_dir = _pending_with_truncated_result(fake_client, tmp_path)
    path = os.path.join(pending_dir, "2026-05-14.json")
    sc = bs.load_sidecar(path)
    sc["schema_version"] = 1
    bs.save_sidecar(path, sc)

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert result["hard_fail"] is False              # was: is True
    assert result["held_dates"] == ["2026-05-14"]    # new
    kept = bs.load_sidecar(path)
    # KEEP these three verbatim. BLOCKED writes the `blocked` marker, so the
    # sidecar file does change — but retry_count must still be untouched and
    # neither the batch API nor the sync API may be called. Deleting them
    # because "the sidecar is written now" would drop the guarantees the test
    # exists for.
    assert kept is not None and kept["retry_count"] == 0
    assert fake_client.messages.batches.created_requests == []
    assert fake_client.messages.create_calls == []
    assert kept["blocked"]["reason"] == "stale_schema"   # new


def test_collect_holds_a_schema_stale_sidecar_missing_its_date_field(fake_client, tmp_path):
    """is_current_schema() being False means the sidecar's shape is not
    guaranteed — that is exactly why it is held rather than trusted. A sidecar
    missing "date" entirely (not just an old schema_version) used to raise
    KeyError on ``sidecar["date"]``, which crashes Collect under ``set -e`` and
    takes the whole morning's publish down with it — the failure mode #65
    removed. The date must be recovered from the sidecar's filename instead.
    """
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(pending_dir)
    os.makedirs(raw_dir)
    path = os.path.join(pending_dir, "2026-05-14.json")
    bs.save_sidecar(path, {"schema_version": 1, "meetings": [], "attempts": []})

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert result["hard_fail"] is False
    assert result["held_dates"] == ["2026-05-14"]      # recovered from filename
    kept = bs.load_sidecar(path)
    assert kept is not None and kept["blocked"]["reason"] == "stale_schema"


def test_repair_preserves_the_good_results_alongside_the_bad(fake_client, tmp_path):
    """The heart of #46: on 2026-06-16, 2 bad results out of 90 threw away the 88
    good ones. Two threads here, one good and one truncated — the good body must
    be assembled unmodified (only the bad one is re-issued)."""
    import json as J
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    threads_dir = str(tmp_path / "threads")
    os.makedirs(raw_dir)

    meeting = _meeting()
    meeting["speeches"] = [
        {"speechOrder": i, "speech": f"speech-{i}", "speaker": "X",
         "speakerGroup": "G", "speakerPosition": "P", "speechURL": "http://x"}
        for i in (1, 2)
    ]
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        J.dump({"meetings": [meeting]}, f, ensure_ascii=False)

    threads = []
    for i in (1, 2):
        ti = {"topic": f"T{i}", "topicTag": "tag", "topicColor": "#111",
              "summary": "s", "speechOrders": [i]}
        req = summarize.build_summary_request(
            meeting, ti, [meeting["speeches"][i - 1]], f"s_abc_{i:02d}", "claude-x")
        threads.append({
            "custom_id": f"s_abc_{i:02d}", "thread_idx": i - 1, "thread_info": ti,
            "speechOrders": [i], "input_hash": bs.compute_input_hash(req["params"]),
        })
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["meetings"][0]["threads"] = threads
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)

    def _body(order, adult):
        return J.dumps({
            "speeches": [{"speechOrder": order, "tension": "確認",
                          "summaries": {"easy": "e", "teen": "t", "adult": adult}}],
            "commitments": [],
        })

    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"
    from tests.conftest import _ResultEntry  # type: ignore
    b.results_by_id["b1"] = [
        _ResultEntry("s_abc_01", "succeeded", text=_body(1, "GOOD-FROM-BATCH")),
        _ResultEntry("s_abc_02", "succeeded", text='{"speeches": [{"speechOr',
                     stop_reason="max_tokens"),
    ]
    fake_client.messages.create_text = _body(2, "REPAIRED")

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert result["hard_fail"] is False
    assert b.created_requests == []                       # no full resubmit
    # Only the bad one was re-issued.
    assert len(fake_client.messages.create_calls) == 1
    with open(os.path.join(threads_dir, "2026-05-14.json"), encoding="utf-8") as f:
        written = J.load(f)
    adults = sorted(t["speeches"][0]["summaries"]["adult"] for t in written)
    assert adults == ["GOOD-FROM-BATCH", "REPAIRED"]


def test_repair_falls_back_to_resubmit_when_reissue_also_truncates(fake_client, tmp_path):
    import json as J
    pending_dir, raw_dir = _pending_with_truncated_result(fake_client, tmp_path)
    fake_client.messages.create_text = J.dumps(_GOOD_BODY)
    fake_client.messages.create_stop_reason = "max_tokens"   # still too long
    b = fake_client.messages.batches
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert result["hard_fail"] is False
    sc = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    assert sc is not None                 # kept for another attempt
    assert sc["retry_count"] == 1
    assert bs.current_batch_id(sc) == "msgbatch_resub_1"


def test_repair_refuses_to_reissue_on_hash_mismatch(fake_client, tmp_path):
    """Raw revised since submission: nothing may be re-issued.

    Since #65 the collect path never even reaches repair in this state —
    verify_manifest_against_raw rejects the manifest before the results are
    fetched. The hash check inside _repair_unusable_results is kept as defence in
    depth (it guards direct callers), and this test now pins the outer guarantee:
    a revised raw costs zero synchronous calls.
    """
    import json as J
    pending_dir, raw_dir = _pending_with_truncated_result(fake_client, tmp_path)
    path = os.path.join(pending_dir, "2026-05-14.json")
    sc = bs.load_sidecar(path)
    sc["meetings"][0]["threads"][0]["input_hash"] = "sha256:stale"
    bs.save_sidecar(path, sc)
    fake_client.messages.create_text = J.dumps(_GOOD_BODY)
    b = fake_client.messages.batches
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"

    summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert fake_client.messages.create_calls == []   # no re-issue attempted


def test_repair_skipped_when_too_many_results_unusable(fake_client, tmp_path):
    """Many failures at once means something systemic — don't hammer the sync
    API one request at a time."""
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)
    import json as _json
    meeting = _meeting()
    n = summarize.REPAIR_LIMIT + 1
    meeting["speeches"] = [
        {"speechOrder": i, "speech": "a", "speaker": "X", "speakerGroup": "G",
         "speakerPosition": "P", "speechURL": "http://x"}
        for i in range(1, n + 1)
    ]
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        _json.dump({"meetings": [meeting]}, f, ensure_ascii=False)

    threads = []
    for i in range(1, n + 1):
        ti = {"topic": "T", "topicTag": "tag", "topicColor": "#111", "summary": "s",
              "speechOrders": [i]}
        req = summarize.build_summary_request(
            meeting, ti, [meeting["speeches"][i - 1]], f"s_abc_{i:02d}", "claude-x")
        threads.append({
            "custom_id": f"s_abc_{i:02d}", "thread_idx": i - 1, "thread_info": ti,
            "speechOrders": [i], "input_hash": bs.compute_input_hash(req["params"]),
        })
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["meetings"][0]["threads"] = threads
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)

    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"
    from tests.conftest import _ResultEntry  # type: ignore
    b.results_by_id["b1"] = [
        _ResultEntry(f"s_abc_{i:02d}", "succeeded", text="{trunc",
                     stop_reason="max_tokens")
        for i in range(1, n + 1)
    ]
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"

    summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert fake_client.messages.create_calls == []          # no per-request repair
    assert b.created_requests                                # fell back to resubmit


# --- Resume-path verdict (#59) -----------------------------------------------
#
# A pending sidecar makes the daily workflow skip Summarize for that specific
# date (since #65, the skip is per-date, not per-run), so for that date
# collect_pending_batches() IS the run and is the only reporter for it. It used
# to answer with a bare bool and never asked systemic_failure()'s question at
# all — a resumed batch that came back fully rejected resubmitted quietly for
# days.

def _run_collect(fake_client, tmp_path, monkeypatch, sidecars,
                 results=None, batch_status="ended", existing_threads=0,
                 raw_present=True, sidecar_age_days=None):
    """Write sidecars + raw + existing threads, then run collect_pending_batches.

    ``results``: {custom_id: parsed_body_dict} for the batch's ONE result set
    (all sidecars here share date "2026-05-14" / batch "b1"). ``None`` means
    "don't touch results_by_id" (used for a still-running batch); ``{}`` means
    the batch ended but answered nothing usable for any custom_id.
    ``sidecar_age_days``: pins ``_utcnow_iso`` and backdates the sidecar's last
    ``submitted_at`` by this many days, mirroring the existing abandon-age tests.
    """
    from tests.conftest import _ResultEntry  # type: ignore
    import json as _json

    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    threads_dir = str(tmp_path / "threads")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(threads_dir, exist_ok=True)

    submitted_iso = None
    if sidecar_age_days is not None:
        now_iso = "2026-07-15T00:00:00Z"
        monkeypatch.setattr(summarize, "_utcnow_iso", lambda: now_iso)
        from datetime import datetime, timedelta, timezone
        now_dt = datetime(2026, 7, 15, tzinfo=timezone.utc)
        submitted_iso = (now_dt - timedelta(days=sidecar_age_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")

    b = fake_client.messages.batches
    for sidecar in sidecars:
        if submitted_iso is not None:
            sidecar["attempts"][-1]["submitted_at"] = submitted_iso
        bs.save_sidecar(os.path.join(pending_dir, f"{sidecar['date']}.json"), sidecar)
        batch_id = bs.current_batch_id(sidecar)
        b.statuses[batch_id] = batch_status
        if batch_status == "ended" and results is not None:
            b.results_by_id[batch_id] = [
                _ResultEntry(cid, "succeeded", text=_json.dumps(body))
                for cid, body in results.items()
            ]

    if raw_present and batch_status == "ended":
        with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w",
                  encoding="utf-8") as f:
            _json.dump({"meetings": [_meeting()]}, f, ensure_ascii=False)

    if existing_threads:
        placeholder = [{"id": f"t{i}"} for i in range(existing_threads)]
        with open(os.path.join(threads_dir, "2026-05-14.json"), "w",
                  encoding="utf-8") as f:
            _json.dump(placeholder, f, ensure_ascii=False)

    return summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )


def test_resume_reports_a_fully_rejected_batch_as_systemic(
        fake_client, tmp_path, monkeypatch):
    """#59: with a sidecar present the workflow skips Summarize entirely, so a
    resumed batch that comes back fully rejected published nothing and said
    nothing. Several consecutive mornings could go green that way."""
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          results={}, existing_threads=0)
    assert result["systemic_dates"] == ["2026-05-14"]
    assert result["suspect_dates"] == []
    assert result["hard_fail"] is False
    assert result["diagnostics"][0]["reason"] == "missing_result"


def test_resume_keeps_a_lone_failure_on_a_published_date_as_suspect(
        fake_client, tmp_path, monkeypatch):
    """The softener applies here too. A sidecar is created by any batch that
    outruns the poll budget, so it is routine — treating resume as exceptional
    would promote the same failure from suspect to systemic purely because the
    batch took longer than one run."""
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          results={}, existing_threads=3)
    assert result["systemic_dates"] == []
    assert result["suspect_dates"] == ["2026-05-14"]


def test_resume_says_nothing_while_the_batch_is_still_running(
        fake_client, tmp_path, monkeypatch):
    """An unfinished batch has answered nothing yet. Counting it would make the
    alarm fire on every slow morning."""
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          batch_status="in_progress", existing_threads=0)
    assert result["systemic_dates"] == []
    assert result["suspect_dates"] == []


def test_resume_denominator_ignores_meetings_with_no_summary_request():
    """A manifest meeting with threads: [] asked nothing of this run."""
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["meetings"].append({"meeting_id": "M2", "outcome": {}, "threads": []})
    assert summarize._resume_summary_attempted(sidecar) == 1


def test_resume_annotates_the_verdict_as_it_is_reached(
        fake_client, tmp_path, monkeypatch, capsys):
    """The annotation is the POINT of _record_resume_verdict, not a nicety.

    _annotate is a no-op unless GITHUB_ACTIONS is set, so without this test the
    entire annotate call could be deleted and every other test would still pass
    — while the one channel that survives a later sidecar's hard fail goes away.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _run_collect(fake_client, tmp_path, monkeypatch,
                 sidecars=[_sidecar_with_one_thread(_correct_hash())],
                 results={}, existing_threads=0)
    errors = [ln for ln in capsys.readouterr().out.splitlines()
              if ln.startswith("::error::")]
    assert len(errors) == 1
    assert "2026-05-14" in errors[0]
    assert "missing_result" in errors[0]


def test_resume_flags_a_finished_batch_whose_date_lost_its_raw(
        fake_client, tmp_path, monkeypatch):
    """The batch finished; the date's raw is gone but not yet old enough to be
    written off. Nothing can be assembled and the sidecar blocks Summarize for
    every date, so this stalls green for up to ABANDON_AGE_DAYS. That is the
    same failure #59 is about, arriving by a different door.

    Reported through held_dates since #65: nothing is wrong with the batch and
    nothing was resubmitted, so calling it a publication verdict would charge
    meetings that were never examined.
    """
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          raw_present=False, sidecar_age_days=2, existing_threads=0)
    assert result["held_dates"] == ["2026-05-14"]
    assert result["systemic_dates"] == []
    assert result["diagnostics"][0]["reason"] == "raw_date_missing"
    assert result["diagnostics"][0]["scope"] == "date"


def test_an_abandoned_sidecar_reds_the_run_without_blocking_the_publish(
        fake_client, tmp_path, monkeypatch, capsys):
    """T3 / #66. Past the abandon age the threads in that batch are gone for good
    — the one thing in this pipeline that cannot be undone. It used to be the ONE
    outcome that left the run green: days 1-30, while it was still fixable, were
    red; day 31, when it stopped being fixable, was a warning. The severity was
    upside down.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          raw_present=False, sidecar_age_days=40, existing_threads=0)
    assert result["abandoned_dates"] == ["2026-05-14"]
    assert result["systemic_dates"] == []      # a different claim, different text
    assert result["held_dates"] == []
    assert result["hard_fail"] is False        # loud, but the publish continues
    assert not os.path.exists(str(tmp_path / "pending" / "2026-05-14.json"))
    errors = [ln for ln in capsys.readouterr().out.splitlines()
              if ln.startswith("::error::")]
    assert len(errors) == 1
    assert "2026-05-14" in errors[0]
    # It must not overclaim: a sidecar can belong to a late meeting on a date
    # that already has published threads.
    assert "never be published" not in errors[0]
    assert "uncollected" in errors[0]


def test_resume_reports_both_observations_when_fully_rejected(
        fake_client, tmp_path, monkeypatch, capsys):
    """#59 finding 1: a resumed batch that comes back fully rejected must say
    BOTH "the API answered nothing usable" (trigger 1) AND "assembly failed"
    (trigger 2) — they are not exclusive (design §3.1 / §3.8), and reporting
    only the assembly side points an operator at the sidecar/raw instead of
    the 400 that actually happened. The date must still land in
    ``systemic_dates`` exactly once, not once per trigger.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          results={}, existing_threads=0)
    assert result["systemic_dates"] == ["2026-05-14"]
    assert result["suspect_dates"] == []
    errors = [ln for ln in capsys.readouterr().out.splitlines()
              if ln.startswith("::error::")]
    assert len(errors) == 1
    assert "produced no usable summary" in errors[0]
    assert "assembly failed: missing_result" in errors[0]


def test_a_hash_mismatch_is_reported_as_held_not_as_a_publication_verdict(
        fake_client, tmp_path, monkeypatch, capsys):
    """Was: "omits the rejection line when results are usable but the hash
    mismatches". The distinction it protected still matters — a raw-side problem
    must not be reported as an API rejection — but since #65 the whole date
    leaves through held_dates instead of systemic_dates, and the results are
    never fetched, so there is no rejection evidence to omit in the first place.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread("sha256:deadbeef")],
                          results={"s_abc_00": {"speeches": [], "commitments": []}},
                          existing_threads=0)
    assert result["held_dates"] == ["2026-05-14"]
    assert result["systemic_dates"] == []
    assert result["suspect_dates"] == []
    errors = [ln for ln in capsys.readouterr().out.splitlines()
              if ln.startswith("::error::")]
    assert len(errors) == 1
    assert "hash_mismatch" in errors[0]
    assert "produced no usable summary" not in errors[0]


def test_verification_happens_before_results_are_fetched(fake_client, tmp_path):
    """#65 の時限爆弾。

    Batch results expire ~29 days after submission. If the hash check runs after
    the fetch, then on the morning the results expire the observed reason stops
    being ``hash_mismatch`` and becomes ``results_expired`` — which is legitimately
    retryable — so a full batch is resubmitted and billed, fails identically the
    next morning, and the cycle repeats until the retry threshold. #65 would go
    from "dies in three days" to "dies in ninety", which is worse: it arrives
    after everyone has forgotten.

    Both conditions hold here: raw changed AND results expired. What this task
    can pin is the OBSERVATION — the reason recorded must be hash_mismatch, not
    results_expired, which is only possible if verification ran first. The
    consequence ("...and therefore nothing is submitted") is now in place and is
    pinned by test_an_expired_batch_whose_raw_also_changed_submits_nothing,
    which exercises this exact two-condition state end to end.
    """
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)
    import json as _json
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        _json.dump({"meetings": [_meeting()]}, f, ensure_ascii=False)

    sidecar = _sidecar_with_one_thread("sha256:stale")   # raw/prompt changed
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"
    b.expired_results.add("b1")          # ...and the results are gone too
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert result["diagnostics"][0]["reason"] == "hash_mismatch", (
        "a hash mismatch must be observed BEFORE the expired results are; once "
        "the fetch raises first, the same broken sidecar reports the retryable "
        "results_expired instead and gets billed for a doomed resubmit"
    )


def test_verify_manifest_against_raw_returns_none_when_everything_matches():
    sidecar = _sidecar_with_one_thread(_correct_hash())
    assert summarize.verify_manifest_against_raw(sidecar, {"M1": _meeting()}) is None


def test_verify_manifest_against_raw_names_the_first_problem():
    sidecar = _sidecar_with_one_thread("sha256:stale")
    diag = summarize.verify_manifest_against_raw(sidecar, {"M1": _meeting()})
    assert diag["reason"] == "hash_mismatch"
    assert diag["meeting_id"] == "M1"
    assert diag["custom_id"] == "s_abc_00"


def test_verify_manifest_against_raw_reports_a_missing_meeting():
    sidecar = _sidecar_with_one_thread(_correct_hash())
    diag = summarize.verify_manifest_against_raw(sidecar, {})
    assert diag["reason"] == "raw_missing"
    assert diag["scope"] == "meeting"


def test_hash_mismatch_is_held_and_costs_nothing(fake_client, tmp_path, monkeypatch, capsys):
    """T1 — the whole point of #65. A resubmit built from the same raw that just
    failed verification fails identically, so it must not happen at all: no
    batch, no retry slot, no repair call. The date still reds the run, through
    held_dates rather than systemic_dates."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    sidecar = _sidecar_with_one_thread("sha256:stale")
    usable = {"s_abc_00": {"speeches": [{"speechOrder": 1, "tension": "確認",
              "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
              "commitments": []}}
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[sidecar], results=usable, existing_threads=3)

    assert fake_client.messages.batches.created_requests == []   # no resubmit
    assert fake_client.messages.create_calls == []               # no repair
    assert result["held_dates"] == ["2026-05-14"]
    # Held is its own axis. Reusing the systemic/suspect verdict would drop a
    # 1-of-1 failure on an already-published date to a WARNING (see
    # publication_blocked_verdict), i.e. exactly not-red, and would also let two
    # held dates trip the workflow's SUSPECT_N >= 2 threshold with the wrong text.
    assert result["systemic_dates"] == []
    assert result["suspect_dates"] == []
    assert result["hard_fail"] is False

    kept = bs.load_sidecar(str(tmp_path / "pending" / "2026-05-14.json"))
    assert kept is not None
    assert kept["retry_count"] == 0
    assert len(kept["attempts"]) == 1
    assert kept["blocked"]["reason"] == "hash_mismatch"
    assert kept["blocked"]["custom_id"] == "s_abc_00"

    errors = [ln for ln in capsys.readouterr().out.splitlines()
              if ln.startswith("::error::")]
    assert len(errors) == 1
    assert "hash_mismatch" in errors[0]
    assert "not resubmitted" in errors[0].lower()


def test_missing_result_still_resubmits(fake_client, tmp_path, monkeypatch):
    """T2 — the other side of T1. If this ever goes green while T1 does too, the
    policy has collapsed into "never resubmit", which loses recoverable dates."""
    b = fake_client.messages.batches
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          results={}, existing_threads=0)
    assert len(b.created_requests) == 1
    assert result["held_dates"] == []
    sc = bs.load_sidecar(str(tmp_path / "pending" / "2026-05-14.json"))
    assert sc["retry_count"] == 1


def test_an_expired_batch_whose_raw_also_changed_submits_nothing(fake_client, tmp_path):
    """T1b's consequence half, now that the policy exists. This is the exact
    2-condition state that made #65 survive its own fix: on day 29 the fetch
    would raise first, the reason would become results_expired, and a doomed
    rebuild would be billed. Verification runs first (Task 2) AND the reason is
    BLOCKED (Task 3) — either one alone leaves the hole open."""
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)
    import json as _json
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        _json.dump({"meetings": [_meeting()]}, f, ensure_ascii=False)
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"),
                    _sidecar_with_one_thread("sha256:stale"))
    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"
    b.expired_results.add("b1")
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert b.created_requests == []
    assert result["held_dates"] == ["2026-05-14"]
    sc = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    assert sc["retry_count"] == 0


def test_a_manifest_meeting_absent_from_raw_holds(fake_client, tmp_path, monkeypatch):
    """T2b, meeting scope. raw_missing is the only meeting-scoped HOLD that
    reaches the policy, and neither the speech_gap nor the raw_date_missing test
    exercises it — wire it to RESUBMIT by mistake and both of those stay green."""
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["meetings"][0]["meeting_id"] = "M2"      # raw on disk only has M1
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[sidecar], results={}, existing_threads=0)
    assert fake_client.messages.batches.created_requests == []
    assert result["held_dates"] == ["2026-05-14"]
    assert result["diagnostics"][0]["reason"] == "raw_missing"
    sc = bs.load_sidecar(str(tmp_path / "pending" / "2026-05-14.json"))
    assert sc["retry_count"] == 0


def test_a_speech_order_gap_holds_without_spending_a_retry(fake_client, tmp_path, monkeypatch):
    """T2b — speech_gap is policy HOLD, so _apply_failure_policy returns "held"
    at the policy check and never reaches the rebuild-then-count code below it.
    This does NOT exercise the record_terminal/rebuild ordering — see
    test_a_canceled_batch_with_no_raw_holds_without_spending_a_retry for the
    RESUBMIT-reason path that actually reaches that code and pins the order."""
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["meetings"][0]["threads"][0]["speechOrders"] = [1, 99]   # 99 not in raw
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[sidecar], results={}, existing_threads=0)
    assert fake_client.messages.batches.created_requests == []
    assert result["held_dates"] == ["2026-05-14"]
    sc = bs.load_sidecar(str(tmp_path / "pending" / "2026-05-14.json"))
    assert sc["retry_count"] == 0
    # HOLD does not mark the sidecar: nothing is wrong with it, the raw is just
    # not here yet.
    assert "blocked" not in sc


def test_a_second_blocked_morning_does_not_commit_an_unchanged_sidecar(
        fake_client, tmp_path, monkeypatch):
    """T9 — _git_commit_sidecar turns a no-op `git commit` (exit 1) into
    "::error:: the in-flight batch will be orphaned". Committing an unchanged
    file every morning would fire that false alarm forever."""
    commits = []
    monkeypatch.setattr(summarize, "_git_commit_sidecar",
                        lambda path, date_str: commits.append(date_str))
    sidecar = _sidecar_with_one_thread("sha256:stale")
    sidecar["blocked"] = {"reason": "hash_mismatch", "since": "2026-08-01T00:00:00Z",
                          "meeting_id": "M1", "custom_id": "s_abc_00"}
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)
    import json as _json
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        _json.dump({"meetings": [_meeting()]}, f, ensure_ascii=False)
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    fake_client.messages.batches.statuses["b1"] = "ended"

    summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=True,
    )
    assert commits == []


def test_retry_exhaustion_is_reported_as_itself_not_as_a_raw_problem(
        fake_client, tmp_path, monkeypatch, capsys):
    """Review round 1, [Critical] 1. The recursive escalation inside
    _apply_failure_policy changes the reason to retry_exhausted, but the
    original call had already passed the OLD diagnostic (e.g. missing_result)
    down to it. If the caller reports that stale diagnostic instead of the
    escalated one, _record_held_sidecar's RESUBMIT-policy branch fires and
    claims "the requests could not be rebuilt from the raw on disk this run" —
    which is false: raw was present and the rebuild succeeded three times. What
    actually happened is the retry budget ran out, and that must be the text
    an operator reads.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["retry_count"] = 2   # one more failed resubmit hits the threshold
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[sidecar], results={}, existing_threads=0)
    assert result["held_dates"] == ["2026-05-14"]
    sc = bs.load_sidecar(str(tmp_path / "pending" / "2026-05-14.json"))
    assert sc["blocked"]["reason"] == "retry_exhausted"

    errors = [ln for ln in capsys.readouterr().out.splitlines()
              if ln.startswith("::error::")]
    assert len(errors) == 1
    assert "three resubmits have failed" in errors[0]
    assert "could not be rebuilt from the raw" not in errors[0]


def test_a_canceled_batch_with_no_raw_holds_without_spending_a_retry(
        fake_client, tmp_path, monkeypatch):
    """Review round 1, [Important] 2. canceled is policy RESUBMIT, so this is
    the path that reaches the rebuild code (unlike speech_gap/raw_missing/
    raw_date_missing, which are policy HOLD and return before ever touching
    it).

    This does NOT pin the order of record_terminal against the rebuild by
    itself: the rebuild-None branch never calls save_sidecar, so whatever
    record_terminal does lives only in the in-memory dict this test cannot see
    — reordering it here leaves no trace in the file this test reads back, and
    the assertion below stays green either way. See
    test_apply_failure_policy_does_not_count_a_retry_it_never_sent for the
    in-memory assertion that actually pins the order.

    What THIS test does pin: a held morning must not persist a bumped
    retry_count. If someone reintroduces the pre-#65 `save_sidecar` call
    inside the rebuild-None branch, this test catches that regression even
    though it can't see the order.
    """
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)   # empty: no ndl-2026-05-14.json
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"),
                    _sidecar_with_one_thread(_correct_hash()))
    fake_client.messages.batches.statuses["b1"] = "canceled"
    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert result["held_dates"] == ["2026-05-14"]
    assert fake_client.messages.batches.created_requests == []
    sc = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    assert sc["retry_count"] == 0


def test_apply_failure_policy_does_not_count_a_retry_it_never_sent(
        fake_client, tmp_path):
    """Pins the ORDER inside the RESUBMIT branch, which no end-to-end test can
    see: the held path deliberately does not save, so moving record_terminal
    back in front of the rebuild leaves no trace on disk. Assert the in-memory
    sidecar instead — record_terminal mutates the dict this test still holds.

    Why it matters: the terminal-status branch runs before raw is loaded, so a
    canceled batch on a morning with no raw takes this path. Counting first
    means three such mornings retire a healthy batch to retry_exhausted having
    submitted nothing.
    """
    sidecar = _sidecar_with_one_thread(_correct_hash())
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)                      # empty: rebuild will fail
    outcome, _diag = summarize._apply_failure_policy(
        fake_client, sidecar, str(tmp_path / "sc.json"), "canceled", None,
        raw_dir, "claude-x", ci_commit=False,
    )
    assert outcome == "held"
    assert sidecar["retry_count"] == 0, (
        "record_terminal ran before the rebuild was known to be possible"
    )
    assert sidecar["attempts"][-1]["terminal_status"] is None
    assert fake_client.messages.batches.created_requests == []
