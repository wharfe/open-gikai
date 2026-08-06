"""Guards for telling an API outage apart from a quiet Diet day.

On 2026-08-05 every grouping, outcome and summary request was rejected with a
400 (#51). The Summarize step still exited 0 — verified from the run's own step
conclusions — and the job only went red because an unrelated bug crashed the
validate step (#52). Fixing #52 removed the one thing making the outage visible,
so this file pins the replacement signal.

The two directions matter equally. A guard that never fires is the bug we are
fixing; a guard that fires on an ordinary bad meeting would red the run every
morning and get switched off.

These tests deliberately drive ``run_pipeline`` rather than the rule function
where they can. An earlier version of this file asserted only on the counter
dict it passed in, so deleting the one kwarg that connects the counter to the
pipeline left the whole feature inert with every test still green — the exact
fail-open shape the feature exists to prevent.
"""

import json
import os
from pathlib import Path

import pytest

import summarize

REPO_ROOT = Path(__file__).resolve().parents[2]


def _meeting(meeting_id="M1", speeches=None):
    """A meeting with one substantive speech, i.e. one that reaches the API."""
    if speeches is None:
        speeches = [{"speechOrder": 1, "speech": "実質的な質疑です", "speaker": "X",
                     "speakerGroup": "G", "speakerPosition": "P",
                     "speechURL": "http://x"}]
    return {"meetingId": meeting_id, "house": "参議院", "meeting": "外交防衛委員会",
            "date": "2026-05-14", "source": "ndl", "speeches": speeches}


def _procedural_meeting(meeting_id="P1"):
    """A meeting the grouper short-circuits before any request is sent.

    Its "0 threads" is not evidence the API works, so it must not count as a
    success — that is the exact confusion that would let an outage read as a
    quiet day (2026-07-24 had both kinds on the same date).
    """
    return _meeting(meeting_id, speeches=[
        {"speechOrder": 1, "speech": "本日の会議を開きます。", "speaker": "委員長",
         "speakerRole": "委員長", "speakerGroup": "", "speakerPosition": "委員長",
         "speechURL": "http://x"},
    ])


_THREAD_INFO = {"topic": "T", "topicTag": "tag", "topicColor": "#111",
                "summary": "s", "speechOrders": [1]}


def _stub_grouping(monkeypatch, raises=False, thread_infos=None):
    """Replace the two synchronous prepare-phase calls."""
    def _group(client, meeting, model):
        if raises:
            raise RuntimeError("`temperature` is deprecated for this model.")
        return [dict(_THREAD_INFO)] if thread_infos is None else thread_infos

    monkeypatch.setattr(summarize, "group_meeting", _group)
    monkeypatch.setattr(summarize, "extract_meeting_outcome",
                        lambda c, m, model: {"result": None, "resolution": None,
                                             "status": "ongoing"})


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

def test_procedural_meeting_is_not_an_api_attempt():
    """The precondition the whole signal rests on."""
    assert summarize.has_question_for_the_api(_meeting()) is True
    assert summarize.has_question_for_the_api(_procedural_meeting()) is False


@pytest.mark.parametrize("stats,published,expected", [
    ({"attempted": 0, "failed": 0}, 0, False),   # quiet day / everything already done
    ({"attempted": 3, "failed": 0}, 5, False),   # healthy
    ({"attempted": 3, "failed": 1}, 4, False),   # ordinary per-meeting breakage
    ({"attempted": 3, "failed": 2}, 2, False),   # still not systemic
    ({"attempted": 3, "failed": 3}, 0, True),    # #51: nothing got through
    ({"attempted": 1, "failed": 1}, 0, True),    # accepted false-positive shape
    # One meeting is not enough evidence to overturn a date that already
    # published. auto-resume seeds progress["completed"] from the committed
    # threads file, so a date whose other meetings succeeded yesterday arrives
    # here as attempted=1 — and without this carve-out one stubborn late-added
    # meeting reds the job every single morning. The evidence is not thrown
    # away, though: see suspect_failure below.
    ({"attempted": 1, "failed": 1}, 12, False),
    # ...but the carve-out stops at one. Suppressing on published threads
    # generally is a fail-open hole: an outage landing on dates that already
    # have output would never be reported. Two meetings failing together is
    # evidence about the layer, whatever is on disk.
    ({"attempted": 2, "failed": 2}, 12, True),
    ({"attempted": 9, "failed": 9}, 400, True),
])
def test_systemic_failure_boundaries(stats, published, expected):
    assert summarize.systemic_failure(stats, published) is expected


@pytest.mark.parametrize("stats,published,expected", [
    # The one shape systemic_failure lets past, caught here instead of dropped.
    ({"attempted": 1, "failed": 1}, 12, True),
    ({"attempted": 1, "failed": 1}, 0, False),   # systemic's job, not this one
    ({"attempted": 2, "failed": 2}, 12, False),  # systemic's job
    ({"attempted": 1, "failed": 0}, 12, False),  # it succeeded
    ({"attempted": 0, "failed": 0}, 12, False),  # nothing was asked
])
def test_suspect_failure_boundaries(stats, published, expected):
    assert summarize.suspect_failure(stats, published) is expected


def test_the_two_verdicts_never_overlap():
    """They map to different exit codes, so a state matching both would make the
    reported verdict depend on statement order rather than on evidence."""
    for attempted in range(0, 4):
        for failed in range(0, attempted + 1):
            for published in (0, 7):
                stats = {"attempted": attempted, "failed": failed}
                assert not (summarize.systemic_failure(stats, published)
                            and summarize.suspect_failure(stats, published))


def test_malformed_raw_does_not_abort_the_pre_check():
    """NDL can emit ``"speech": null``. That used to fail inside the per-meeting
    try; the pre-check runs OUTSIDE it, so a raise here would kill the whole run
    — every later date included — instead of one meeting. Answering True keeps
    the error where it was."""
    broken = _meeting(speeches=[{"speechOrder": 1, "speech": None, "speaker": "X"}])
    assert summarize.has_question_for_the_api(broken) is True


# ---------------------------------------------------------------------------
# Batch path — prepare-phase failures (grouping / outcome)
# ---------------------------------------------------------------------------

def _run_batch_phase(fake_client, meetings, api_stats, monkeypatch,
                     group_raises, pending_dir="/tmp/unused-pending"):
    _stub_grouping(monkeypatch, raises=group_raises, thread_infos=[])
    return summarize.run_batch_phase(
        fake_client, meetings, {"completed": [], "failed": []},
        members={}, model="claude-x", date_str="2026-05-14", thread_counter=0,
        batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=pending_dir, ci_commit=False,
        api_stats=api_stats,
    )


def test_batch_phase_flags_a_total_api_failure(fake_client, monkeypatch):
    """The 2026-08-05 shape: every request raises, no threads, exit must not be 0."""
    stats = summarize.new_api_stats()
    _run_batch_phase(fake_client, [_meeting("M1"), _meeting("M2")], stats,
                     monkeypatch, group_raises=True)

    assert stats == {"attempted": 2, "failed": 2}
    assert summarize.systemic_failure(stats, 0) is True


def test_procedural_meetings_do_not_mask_a_total_api_failure(fake_client, monkeypatch):
    """2026-07-24's actual shape: procedural meetings alongside failing ones.

    Counting the procedural ones as attempts would make failed < attempted and
    silently downgrade a real outage to "partially fine" — which is how the
    obvious rule (`every meeting failed`) misses the very run that motivated it.
    """
    meetings = [_procedural_meeting("P1"), _meeting("M1"), _procedural_meeting("P2")]
    stats = summarize.new_api_stats()
    _run_batch_phase(fake_client, meetings, stats, monkeypatch, group_raises=True)

    assert stats == {"attempted": 1, "failed": 1}
    assert summarize.systemic_failure(stats, 0) is True


def test_a_genuinely_quiet_date_is_not_flagged(fake_client, monkeypatch):
    """Grouping succeeds and legitimately finds nothing. Must stay green, or the
    guard fails the job on every quiet Diet day and gets switched off."""
    stats = summarize.new_api_stats()
    _run_batch_phase(fake_client, [_meeting("M1")], stats, monkeypatch,
                     group_raises=False)

    assert stats == {"attempted": 1, "failed": 0}
    assert summarize.systemic_failure(stats, 0) is False


def test_only_procedural_meetings_is_not_flagged(fake_client, monkeypatch):
    """No request was ever sent, so there is no evidence of an outage."""
    stats = summarize.new_api_stats()
    _run_batch_phase(fake_client, [_procedural_meeting("P1")], stats, monkeypatch,
                     group_raises=True)

    assert stats == {"attempted": 0, "failed": 0}
    assert summarize.systemic_failure(stats, 0) is False


# ---------------------------------------------------------------------------
# Batch path — in-batch summary failures
#
# The summary phase does not fail by raising: fetch_summary_results turns every
# errored entry into None and assembly reports its failure as "pending", which
# is indistinguishable from a batch that merely needs another day. Counting only
# exceptions left this whole phase — the bulk of the spend, and where the
# #47/#51 regression lived — asserting the API was healthy.
# ---------------------------------------------------------------------------

def _prepared(meeting_id, custom_ids, askable=True):
    return {"meeting_id": meeting_id, "askable": askable,
            "pending": [{"custom_id": c} for c in custom_ids]}


def _ok_result():
    return {"speeches": [{"speechOrder": 1}], "commitments": []}


@pytest.mark.parametrize("results,expected_failed", [
    ({"c0": None, "c1": None}, 1),                       # every request errored
    ({"c0": _ok_result(), "c1": None}, 0),               # one thread survived
    ({"c0": {"speeches": []}, "c1": None}, 1),           # parsed but empty == unusable
    ({}, 1),                                             # nothing came back at all
])
def test_a_meeting_fails_only_when_no_request_came_back_usable(results, expected_failed):
    stats = summarize.new_api_stats()
    stats["attempted"] = 1
    summarize.count_meetings_with_no_usable_result(
        [_prepared("M1", ["c0", "c1"])], results, stats,
    )
    assert stats["failed"] == expected_failed


def test_a_meeting_that_asked_nothing_is_not_charged():
    """Grouping succeeded and produced no threads. It sent no summary request,
    so there is no evidence about the summary API either way."""
    stats = summarize.new_api_stats()
    summarize.count_meetings_with_no_usable_result(
        [_prepared("M1", [])], {}, stats,
    )
    assert stats["failed"] == 0


def test_batch_phase_counts_results_that_all_came_back_errored(
    fake_client, tmp_path, monkeypatch,
):
    """End to end through run_batch_phase, not just the counting helper: without
    the call site this returns pending=True with failed=0 and the run exits 0
    while the sidecar quietly rots."""
    _stub_grouping(monkeypatch)
    b = fake_client.messages.batches
    b.statuses["msgbatch_fake_0001"] = "ended"
    custom_id = summarize.make_batch_custom_id("M1", 0)
    b.results_by_id["msgbatch_fake_0001"] = [
        _errored_entry(custom_id),
    ]

    stats = summarize.new_api_stats()
    new_threads, _, _, pending = summarize.run_batch_phase(
        fake_client, [_meeting("M1")], {"completed": [], "failed": []},
        members={}, model="claude-x", date_str="2026-05-14", thread_counter=0,
        batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"), ci_commit=False,
        api_stats=stats,
    )

    assert new_threads == []
    assert pending is True          # sidecar kept, as before — that part is right
    assert stats == {"attempted": 1, "failed": 1}
    assert summarize.systemic_failure(stats, 0) is True


def _overloaded():
    """The 529 an overloaded API answers with — a non-deterministic outage.

    Distinct from the 400 that motivated all this: the 400 arrives per request
    inside the batch, while this one is raised by the SDK at the call site.
    """
    import anthropic
    import httpx
    return anthropic.InternalServerError(
        message="Overloaded",
        response=httpx.Response(529, request=httpx.Request(
            "POST", "https://api.anthropic.com/v1/messages/batches")),
        body=None,
    )


def _raise(exc):
    def _f(*a, **k):
        raise exc
    return _f


def test_a_rejected_batch_submission_does_not_abort_the_run(
    fake_client, tmp_path, monkeypatch,
):
    """Submission is the one API call with nothing persisted behind it.

    Unwrapped it propagated to exit 1, which aborts the workflow's date loop
    and skips enrichment, validate, commit, push and IndexNow — so an outage on
    date 3 of 20 threw away dates 1-2's threads and never ran 4-20. That is the
    #52 amplification, reached through the API instead of a crash."""
    _stub_grouping(monkeypatch)
    monkeypatch.setattr(summarize, "submit_summary_batch", _raise(_overloaded()))

    stats = summarize.new_api_stats()
    progress = {"completed": [], "failed": []}
    new_threads, _, completed, pending = summarize.run_batch_phase(
        fake_client, [_meeting("M1")], progress,
        members={}, model="claude-x", date_str="2026-05-14", thread_counter=0,
        batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"), ci_commit=False,
        api_stats=stats,
    )

    assert (new_threads, completed, pending) == ([], [], False)
    assert stats == {"attempted": 1, "failed": 1}
    assert progress["failed"] == ["M1"]        # retryable, not silently dropped
    assert summarize.systemic_failure(stats, 0) is True


def test_an_overloaded_api_after_submission_keeps_the_sidecar(
    fake_client, tmp_path, monkeypatch,
):
    """Past submission the batch is real and its results are retained ~29 days,
    so the honest answer is "pending" — resume next run for free. Not a failure:
    charging it would red the job over a transient 529 that costs nothing."""
    _stub_grouping(monkeypatch)
    fake_client.messages.batches.statuses["msgbatch_fake_0001"] = "ended"
    monkeypatch.setattr(summarize, "fetch_summary_results", _raise(_overloaded()))

    stats = summarize.new_api_stats()
    new_threads, _, completed, pending = summarize.run_batch_phase(
        fake_client, [_meeting("M1")], {"completed": [], "failed": []},
        members={}, model="claude-x", date_str="2026-05-14", thread_counter=0,
        batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"), ci_commit=False,
        api_stats=stats,
    )

    assert (new_threads, completed, pending) == ([], [], True)
    assert stats == {"attempted": 1, "failed": 0}
    assert os.path.exists(str(tmp_path / "pending" / "2026-05-14.json"))


def test_run_pipeline_survives_an_api_outage_at_submission(
    fake_client, tmp_path, monkeypatch,
):
    """The wiring: run_pipeline must RETURN the verdict, not raise it. A
    traceback here is exit 1, and exit 1 blocks the day's publish."""
    _stub_grouping(monkeypatch)
    monkeypatch.setattr(summarize, "submit_summary_batch", _raise(_overloaded()))
    assert _run_pipeline(tmp_path, fake_client, monkeypatch,
                         [_meeting("M1"), _meeting("M2")], batch=True) == summarize.EXIT_SYSTEMIC_FAILURE


def _errored_entry(custom_id):
    """A batch result entry whose request failed, as the SDK reports it."""
    import types
    entry = types.SimpleNamespace()
    entry.custom_id = custom_id
    entry.result = types.SimpleNamespace(type="errored", message=None)
    return entry


# ---------------------------------------------------------------------------
# End-to-end through run_pipeline — the wiring, which is what actually rots
# ---------------------------------------------------------------------------

def _write_raw(tmp_path, meetings, date_str="2026-05-14"):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(exist_ok=True)
    with open(raw_dir / f"ndl-{date_str}.json", "w", encoding="utf-8") as f:
        json.dump({"meetings": meetings}, f, ensure_ascii=False)
    return str(raw_dir)


def _run_pipeline(tmp_path, fake_client, monkeypatch, meetings, batch):
    monkeypatch.setattr(summarize.anthropic, "Anthropic", lambda *a, **k: fake_client)
    return summarize.run_pipeline(
        date_str="2026-05-14",
        raw_dir=_write_raw(tmp_path, meetings),
        output_dir=str(tmp_path / "threads"),
        members_path=str(tmp_path / "members.json"),
        batch=batch,
        batch_timeout_seconds=0,
        batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"),
    )


def test_run_pipeline_returns_the_verdict_on_a_total_batch_failure(
    fake_client, tmp_path, monkeypatch,
):
    """The wiring guard. Drop the api_stats kwarg at the run_batch_phase call
    site and run_batch_phase silently falls back to its own local counter — the
    feature goes completely inert. Only a test that drives run_pipeline notices."""
    _stub_grouping(monkeypatch, raises=True)
    assert _run_pipeline(tmp_path, fake_client, monkeypatch,
                         [_meeting("M1"), _meeting("M2")], batch=True) == summarize.EXIT_SYSTEMIC_FAILURE


def test_run_pipeline_stays_green_on_a_quiet_date(fake_client, tmp_path, monkeypatch):
    _stub_grouping(monkeypatch, thread_infos=[])
    assert _run_pipeline(tmp_path, fake_client, monkeypatch,
                         [_meeting("M1")], batch=True) == 0


def test_run_pipeline_flags_a_summary_only_outage_on_the_sync_path(
    fake_client, tmp_path, monkeypatch,
):
    """Grouping works, every summary request is rejected.

    process_meeting catches summary failures per thread and returns cleanly, so
    this path used to file the meeting as *completed*, publish nothing, and exit
    0 — the 2026-08-05 shape localized to the summarizer, on the path a manual
    recovery run uses."""
    _stub_grouping(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("`temperature` is deprecated for this model.")

    monkeypatch.setattr(summarize, "summarize_thread", _boom)
    monkeypatch.setattr(summarize.time, "sleep", lambda *_: None)

    assert _run_pipeline(tmp_path, fake_client, monkeypatch,
                         [_meeting("M1")], batch=False) == summarize.EXIT_SYSTEMIC_FAILURE


def test_a_meeting_with_no_usable_summary_is_left_retryable(
    fake_client, tmp_path, monkeypatch,
):
    """Not just the exit code: the meeting must not be recorded as completed, or
    auto-resume skips it forever and the date can never recover."""
    _stub_grouping(monkeypatch)
    monkeypatch.setattr(summarize, "summarize_thread",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("400")))
    monkeypatch.setattr(summarize.time, "sleep", lambda *_: None)

    _run_pipeline(tmp_path, fake_client, monkeypatch, [_meeting("M1")], batch=False)

    progress = summarize.load_progress(
        str(tmp_path / "threads" / "2026-05-14.progress.json"))
    assert progress["failed"] == ["M1"]
    assert progress["completed"] == []


def test_a_summary_that_cannot_be_assembled_also_counts_as_lost(
    fake_client, tmp_path, monkeypatch,
):
    """The API answered fine; the answer still never became a thread.

    Counting only raised exceptions left this branch silent: the meeting was
    filed as *completed* with zero threads and auto-resume never looked at it
    again — a permanent hole in the published record, arrived at without a
    single error-level line about the meeting itself."""
    _stub_grouping(monkeypatch)
    monkeypatch.setattr(summarize, "summarize_thread",
                        lambda *a, **k: {"speeches": [{"speechOrder": 1}],
                                         "commitments": []})
    monkeypatch.setattr(summarize, "assemble_thread", lambda *a, **k: None)
    monkeypatch.setattr(summarize.time, "sleep", lambda *_: None)

    assert _run_pipeline(tmp_path, fake_client, monkeypatch,
                         [_meeting("M1")], batch=False) == summarize.EXIT_SYSTEMIC_FAILURE
    progress = summarize.load_progress(
        str(tmp_path / "threads" / "2026-05-14.progress.json"))
    assert progress["failed"] == ["M1"]


def test_a_lone_failure_on_a_published_date_is_reported_as_suspect(
    fake_client, tmp_path, monkeypatch,
):
    """Not green, not red: exit 4.

    Alone this is one bad meeting on a date that already published, and reding
    the run over it would red most mornings — the 30-day lookback re-visits
    published dates daily. But returning 0 threw the evidence away, and a total
    outage can consist of nothing but these."""
    threads_dir = tmp_path / "threads"
    threads_dir.mkdir()
    with open(threads_dir / "2026-05-14.json", "w", encoding="utf-8") as f:
        json.dump([{"id": "t_20260514_aaaaaa_1", "speeches": []}], f)

    _stub_grouping(monkeypatch, raises=True)
    assert _run_pipeline(tmp_path, fake_client, monkeypatch,
                         [_meeting("M1")], batch=True) == summarize.EXIT_SUSPECT_FAILURE


def test_a_grouping_that_names_no_real_speech_is_not_silently_completed(
    fake_client, tmp_path, monkeypatch,
):
    """Grouping answers with threads; none of their speechOrders exist in raw.

    Nothing is submitted, so the meeting used to land in neither list — never
    completed, never failed — re-charging grouping and outcome every morning
    while the date published nothing and the run exited 0."""
    _stub_grouping(monkeypatch, thread_infos=[dict(_THREAD_INFO, speechOrders=[99])])
    assert _run_pipeline(tmp_path, fake_client, monkeypatch,
                         [_meeting("M1")], batch=True) == summarize.EXIT_SYSTEMIC_FAILURE
    progress = summarize.load_progress(
        str(tmp_path / "threads" / "2026-05-14.progress.json"))
    assert progress["failed"] == ["M1"]


def test_the_same_hole_on_the_sync_path(fake_client, tmp_path, monkeypatch):
    _stub_grouping(monkeypatch, thread_infos=[dict(_THREAD_INFO, speechOrders=[99])])
    monkeypatch.setattr(summarize.time, "sleep", lambda *_: None)
    assert _run_pipeline(tmp_path, fake_client, monkeypatch,
                         [_meeting("M1")], batch=False) == summarize.EXIT_SYSTEMIC_FAILURE


# ---------------------------------------------------------------------------
# The exit-code contract, which lives half in Python and half in YAML
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("verdict", [0, 3, 4])
def test_main_propagates_the_verdict_to_the_exit_code(monkeypatch, verdict):
    """The wiring, not the rule. run_pipeline can return the right verdict and
    the whole fix still be inert if main() drops it — which is precisely how the
    original fail-open worked: the information existed, nothing acted on it."""
    monkeypatch.setattr(summarize, "run_pipeline", lambda **kwargs: verdict)
    argv = ["--date", "2026-05-14", "--batch"]

    if verdict == 0:
        summarize.main(argv)          # must not raise
        return
    with pytest.raises(SystemExit) as excinfo:
        summarize.main(argv)
    assert excinfo.value.code == verdict


def test_the_workflow_tolerates_exactly_these_exit_codes():
    """The other half of the contract. summarize.py's exit 3 and 4 only avoid
    blocking the publish because the workflow's `set -e` loop special-cases those
    numbers; change one side and the code either aborts the loop (blocking the
    day's output — the #52 amplification) or stops failing the job at all.
    Nothing else in the repo ties the two together."""
    yaml = pytest.importorskip("yaml")
    path = REPO_ROOT / ".github" / "workflows" / "daily-batch.yml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = spec["jobs"]["fetch-and-summarize"]["steps"]

    summarize_step = next(s for s in steps if s.get("id") == "summarize")
    run = summarize_step["run"]
    for code in (summarize.EXIT_SYSTEMIC_FAILURE, summarize.EXIT_SUSPECT_FAILURE):
        assert f'-eq {code} ' in run, f"the Summarize loop no longer tolerates {code}"
        assert code != 1, "1 is indistinguishable from a crash under `set -e`"
    assert summarize.EXIT_SYSTEMIC_FAILURE != summarize.EXIT_SUSPECT_FAILURE

    # Tolerating the codes is only half of it. Checking just the `-eq` strings
    # passes even if the branch bodies are deleted — the exit codes would then be
    # swallowed outright, which is worse than not having the signal at all
    # because the run reads as clean. Pin the whole data flow: each branch
    # records its date, the loop publishes the lists, the last step reads one.
    assert 'SYSTEMIC="$SYSTEMIC $d"' in run, (
        "the systemic exit code is no longer recorded — it is now swallowed"
    )
    assert 'SUSPECT="$SUSPECT $d"' in run, (
        "the suspect exit code is no longer recorded — the evidence is discarded "
        "again, which is the hole this second code exists to close"
    )
    assert "SUSPECT_N=$((SUSPECT_N + 1))" in run
    # Several suspect dates in one run IS the outage. Without this the second
    # code is collected and then ignored, which is indistinguishable from not
    # having it.
    assert '[ "$SUSPECT_N" -ge 2 ]' in run, (
        "suspect dates are collected but never escalate"
    )
    assert 'FAIL_DATES="$FAIL_DATES$SUSPECT"' in run
    assert 'systemic_dates=$FAIL_DATES' in run, (
        "the escalation never reaches GITHUB_OUTPUT"
    )
    assert 'suspect_dates=$SUSPECT' in run, (
        "suspect dates are invisible in the run summary"
    )
    # A crash on a later date skips every step below, so the annotation is the
    # only channel that survives to name an earlier date's outage.
    assert "::error::" in run

    # And the job must still be failed, after everything else has run.
    assert steps[-1]["if"] == "steps.summarize.outputs.systemic_dates != ''"
    assert "exit 1" in steps[-1]["run"]
