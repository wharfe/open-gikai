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
        "schema_version": 1, "date": "2026-05-14", "model": "claude-x",
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
