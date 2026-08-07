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


def test_collect_hard_fails_at_retry_threshold(fake_client, tmp_path):
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
    assert result["hard_fail"] is True


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


def test_collect_refuses_sidecar_from_an_older_schema(fake_client, tmp_path):
    """v1 hashes were computed over a different param set, so every thread would
    fail verification. Silently resubmitting burns a retry slot per run until the
    date is permanently lost, so refuse loudly and leave it rescuable by hand."""
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

    assert result["hard_fail"] is True                                          # surfaced, not buried
    kept = bs.load_sidecar(path)
    assert kept is not None and kept["retry_count"] == 0          # nothing burned
    assert fake_client.messages.batches.created_requests == []    # no resubmit
    assert fake_client.messages.create_calls == []                # no repair either


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
    """Raw revised since submission: re-issuing would summarize different text
    than the manifest describes, so the repair pass must decline."""
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
# A pending sidecar makes the daily workflow skip the whole Summarize step, so
# on those mornings collect_pending_batches() IS the run. It used to answer
# with a bare bool and never asked systemic_failure()'s question at all — a
# resumed batch that came back fully rejected resubmitted quietly for days.

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
    same failure #59 is about, arriving by a different door."""
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          raw_present=False, sidecar_age_days=2, existing_threads=0)
    assert result["systemic_dates"] == ["2026-05-14"]
    assert result["diagnostics"][0]["reason"] == "raw_date_missing"
    assert result["diagnostics"][0]["scope"] == "date"


def test_resume_stays_quiet_when_the_raw_is_legitimately_out_of_window(
        fake_client, tmp_path, monkeypatch):
    """Past the abandon age the raw is SUPPOSED to be gone. That path already
    warns and deletes the sidecar, so adding a second alarm would red the run
    for a date nobody can act on."""
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          raw_present=False, sidecar_age_days=40, existing_threads=0)
    assert result["systemic_dates"] == []
    assert result["suspect_dates"] == []
