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
    threads, ok = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results, members={}, thread_counter=0,
    )
    assert ok is True
    assert len(threads) == 1
    assert threads[0]["topicTag"] == "tag"


def test_assemble_fails_on_hash_mismatch():
    sidecar = _sidecar_with_one_thread("sha256:deadbeef")  # wrong
    results = {"s_abc_00": {"speeches": [{"speechOrder": 1}], "commitments": []}}
    threads, ok = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results, members={}, thread_counter=0,
    )
    assert ok is False


def test_assemble_fails_on_missing_result():
    sidecar = _sidecar_with_one_thread(_correct_hash())
    threads, ok = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results={}, members={}, thread_counter=0,
    )
    assert ok is False


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

    new_threads, _, completed, pending = summarize.run_batch_phase(
        fake_client, [meeting], {"completed": [], "failed": []},
        members={}, model="claude-x", date_str="2026-05-14", thread_counter=0,
        batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=pending_dir, ci_commit=False,
    )
    assert pending is True
    assert new_threads == []
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

    hard_fail = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert hard_fail is False
    assert not os.path.exists(os.path.join(pending_dir, "2026-05-14.json"))
    assert os.path.exists(os.path.join(threads_dir, "2026-05-14.json"))


def test_collect_hard_fails_at_retry_threshold(fake_client, tmp_path):
    pending_dir = str(tmp_path / "pending")
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["retry_count"] = 3
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    hard_fail = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"),
        raw_dir=str(tmp_path / "r"),
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert hard_fail is True


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

    hard = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert hard is False
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

    hard = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert hard is False
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

    hard = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert hard is False
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

    hard = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert hard is False
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

    hard = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert hard is False
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
        hard = summarize.collect_pending_batches(
            fake_client, members={}, model="claude-x",
            pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
            budget_seconds=0, poll_seconds=0, ci_commit=False,
        )

    assert hard is False
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

    hard = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert hard is False
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

    hard = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert hard is False                       # no crash, loop completed
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

    hard = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert hard is True                                          # surfaced, not buried
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

    hard = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert hard is False
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

    hard = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert hard is False
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
