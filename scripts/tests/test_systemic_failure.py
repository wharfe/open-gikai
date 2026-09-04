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

import hashlib
import json
import os
import sys
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
    # **kwargs, not a fixed signature: extract_meeting_outcome takes
    # outcome_stats since #60, and a stub that refuses it turns every caller into
    # "Failed to prepare" — which those callers then count as a failed meeting,
    # so the suite goes red somewhere far from the stub.
    monkeypatch.setattr(summarize, "extract_meeting_outcome",
                        lambda c, m, **kw: {"result": None, "resolution": None,
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


@pytest.mark.parametrize("summary_attempted,published,expected", [
    # Nothing was asked of the summary phase — assembly failure is not our story.
    (0, 0, 0),
    (0, 5, 0),
    # A date that published nothing and had requests in flight: systemic.
    (1, 0, summarize.EXIT_SYSTEMIC_FAILURE),
    (4, 0, summarize.EXIT_SYSTEMIC_FAILURE),
    # Two or more meetings blocked is evidence about the layer, whatever is
    # already on disk — the same rule trigger 1 uses.
    (2, 9, summarize.EXIT_SYSTEMIC_FAILURE),
    # Exactly one meeting blocked on an already-published date is weak evidence.
    (1, 9, summarize.EXIT_SUSPECT_FAILURE),
])
def test_publication_blocked_verdict_boundaries(summary_attempted, published, expected):
    assert summarize.publication_blocked_verdict(summary_attempted, published) == expected


def test_worst_verdict_ranks_systemic_above_suspect():
    S = summarize.EXIT_SYSTEMIC_FAILURE
    P = summarize.EXIT_SUSPECT_FAILURE
    assert summarize.worst_verdict(0, 0) == 0
    assert summarize.worst_verdict(0, P) == P
    assert summarize.worst_verdict(P, S) == S
    assert summarize.worst_verdict(S, 0) == S


def test_rejection_verdict_reuses_the_existing_predicates():
    """The new wrapper must not invent a third opinion about trigger 1."""
    stats = {"attempted": 2, "failed": 2}
    assert summarize.rejection_verdict(stats, 0) == summarize.EXIT_SYSTEMIC_FAILURE
    assert summarize.rejection_verdict({"attempted": 1, "failed": 1}, 9) == \
        summarize.EXIT_SUSPECT_FAILURE
    assert summarize.rejection_verdict({"attempted": 2, "failed": 1}, 0) == 0


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
    phase = summarize.run_batch_phase(
        fake_client, [_meeting("M1")], {"completed": [], "failed": []},
        members={}, model="claude-x", date_str="2026-05-14", thread_counter=0,
        batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"), ci_commit=False,
        api_stats=stats,
    )

    assert phase["threads"] == []
    assert phase["pending"] is True  # sidecar kept, as before — that part is right
    assert stats == {"attempted": 1, "failed": 1}
    assert summarize.systemic_failure(stats, 0) is True


def _run_batch_phase_returning_dict(fake_client, tmp_path, monkeypatch,
                                    meetings, assembly_fails_with=None):
    """Drive run_batch_phase end to end and return its dict result (#61).

    Grouping is stubbed to mirror the real short-circuit: an askable meeting
    gets one real thread (so a summary request is actually sent), a procedural
    one gets none — using a fixed thread_infos=[dict(_THREAD_INFO)] regardless
    of input, as ``_stub_grouping`` does, would put a thread under a procedural
    meeting too (its raw still has speechOrder 1). That would not make
    ``summary_attempted`` wrong (the filter already excludes non-askable
    meetings) but it WOULD flip ``publication_blocked`` for a date that never
    sent a real request — the has-question-aware stub keeps that scenario
    honest.

    The batch is set to "ended" with a usable succeeded result for every
    meeting that gets a thread. ``assembly_fails_with``, when given,
    monkeypatches ``assemble_from_manifest`` (module-global, so the patch
    reaches the call inside ``run_batch_phase``) to fail with that
    diagnostic — the shape a real speechOrder/hash/result mismatch would
    produce.
    """
    def _group(client, meeting, model):
        # "grouping" specifically, NOT has_question_for_the_api: since #60 that
        # one also answers True for a meeting whose only request is the outcome
        # one, and such a meeting sends no grouping request in production. A
        # stub keyed off it would hand that meeting a thread and quietly undo
        # the honesty this helper exists to keep.
        return ([dict(_THREAD_INFO)]
                if "grouping" in summarize.askable_request_kinds(meeting) else [])

    monkeypatch.setattr(summarize, "group_meeting", _group)
    monkeypatch.setattr(summarize, "extract_meeting_outcome",
                        lambda c, m, **kw: {"result": None, "resolution": None,
                                            "status": "ongoing"})

    b = fake_client.messages.batches
    b.statuses["msgbatch_fake_0001"] = "ended"
    from tests.conftest import _ResultEntry  # type: ignore
    import json as J
    entries = [
        _ResultEntry(summarize.make_batch_custom_id(m["meetingId"], 0), "succeeded",
                    text=J.dumps({
                        "speeches": [{"speechOrder": 1, "tension": "確認",
                                     "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
                        "commitments": [],
                    }))
        for m in meetings
        if "grouping" in summarize.askable_request_kinds(m)  # mirrors _group
    ]
    b.results_by_id["msgbatch_fake_0001"] = entries

    if assembly_fails_with is not None:
        monkeypatch.setattr(
            summarize, "assemble_from_manifest",
            lambda *a, **k: ([], False, assembly_fails_with),
        )

    return summarize.run_batch_phase(
        fake_client, meetings, {"completed": [], "failed": []},
        members={}, model="claude-x", date_str="2026-05-14", thread_counter=0,
        batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"), ci_commit=False,
    )


def test_batch_phase_reports_publication_blocked_with_a_diagnostic(
        fake_client, tmp_path, monkeypatch):
    """A batch that answers usably but cannot be assembled must say so.

    This is #61: usable_result() only asks 'did it parse and carry speeches',
    while assembly also demands the speechOrders still exist in raw. Before this
    change the counter called such a meeting a success and the run exited 0.
    """
    result = _run_batch_phase_returning_dict(
        fake_client, tmp_path, monkeypatch,
        meetings=[_meeting("M1")],
        assembly_fails_with=summarize._diagnostic("speech_gap", "M1", "s_x_00"),
    )
    assert result["publication_blocked"] is True
    assert result["summary_attempted"] == 1
    assert result["diagnostic"]["reason"] == "speech_gap"


def test_batch_phase_does_not_report_blocked_when_nothing_was_summarized(
        fake_client, tmp_path, monkeypatch):
    """A quiet date must not be reported as blocked."""
    result = _run_batch_phase_returning_dict(
        fake_client, tmp_path, monkeypatch,
        meetings=[_procedural_meeting("P1")],
    )
    assert result["publication_blocked"] is False
    assert result["summary_attempted"] == 0


def test_summary_attempted_excludes_a_meeting_whose_grouping_asked_nothing(
        fake_client, tmp_path, monkeypatch):
    """The denominator's defining property (review round 1, Important 1).

    Two meetings are ASKABLE (both substantive, both charged to
    api_stats["attempted"]) — but only one of them actually gets a summary
    request in this batch; the other's grouping legitimately finds nothing,
    the same shape a meeting with no debate content produces. Charging that
    quiet meeting to summary_attempted anyway would let a real outage on the
    OTHER meeting hide behind it, which is exactly what
    publication_blocked_verdict's docstring warns about.

    This must fail if the "askable and pending" filter in run_batch_phase is
    replaced with a raw ``len(prepared_meetings)`` — verified by hand: making
    that edit temporarily turns summary_attempted from 1 into 2 and this test
    goes red (see the task-1-report.md fix-round entry for the transcript).
    """
    m1, m2 = _meeting("M1"), _meeting("M2")

    def _group(client, meeting, model):
        # M1 gets a real thread; M2 is equally askable but its grouping
        # legitimately produces zero threads, so it sends no request.
        return [dict(_THREAD_INFO)] if meeting["meetingId"] == "M1" else []

    monkeypatch.setattr(summarize, "group_meeting", _group)
    monkeypatch.setattr(summarize, "extract_meeting_outcome",
                        lambda c, m, **kw: {"result": None, "resolution": None,
                                            "status": "ongoing"})
    fake_client.messages.batches.statuses["msgbatch_fake_0001"] = "ended"
    monkeypatch.setattr(
        summarize, "assemble_from_manifest",
        lambda *a, **k: ([], False,
                         summarize._diagnostic("missing_result", "M1", "s_x_00")),
    )

    stats = summarize.new_api_stats()
    phase = summarize.run_batch_phase(
        fake_client, [m1, m2], {"completed": [], "failed": []},
        members={}, model="claude-x", date_str="2026-05-14", thread_counter=0,
        batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"), ci_commit=False,
        api_stats=stats,
    )

    assert phase["summary_attempted"] == 1
    assert stats["attempted"] == 2


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
    phase = summarize.run_batch_phase(
        fake_client, [_meeting("M1")], progress,
        members={}, model="claude-x", date_str="2026-05-14", thread_counter=0,
        batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"), ci_commit=False,
        api_stats=stats,
    )

    assert (phase["threads"], phase["completed_meeting_ids"], phase["pending"]) == ([], [], False)
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
    phase = summarize.run_batch_phase(
        fake_client, [_meeting("M1")], {"completed": [], "failed": []},
        members={}, model="claude-x", date_str="2026-05-14", thread_counter=0,
        batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"), ci_commit=False,
        api_stats=stats,
    )

    assert (phase["threads"], phase["completed_meeting_ids"], phase["pending"]) == ([], [], True)
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


def test_run_pipeline_returns_the_verdict_on_a_pure_assembly_failure(
    fake_client, tmp_path, monkeypatch,
):
    """Trigger 2's own wiring guard.

    In test_a_fully_rejected_batch_fires_both_triggers_and_says_so, trigger 1
    (nothing usable came back) ALSO fires and independently produces
    EXIT_SYSTEMIC_FAILURE — so mutating the `blocked = (...)` wiring at
    summarize.py's publication_blocked_verdict call site to `blocked = 0`
    leaves that test green. This test drives the shape where trigger 1 stays
    silent: the batch reaches "ended" and every request comes back succeeded
    and usable (so rejection_verdict is 0), but assemble_from_manifest still
    fails, and the date has no existing threads. Only here does the exit code
    depend entirely on the `blocked` wiring — verified by hand (see the task
    report for the mutation transcript).
    """
    meetings = [_meeting("M1")]
    _stub_grouping(monkeypatch)
    b = fake_client.messages.batches
    b.statuses["msgbatch_fake_0001"] = "ended"
    from tests.conftest import _ResultEntry  # type: ignore
    import json as J
    b.results_by_id["msgbatch_fake_0001"] = [
        _ResultEntry(summarize.make_batch_custom_id("M1", 0), "succeeded",
                    text=J.dumps({
                        "speeches": [{"speechOrder": 1, "tension": "確認",
                                     "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
                        "commitments": [],
                    })),
    ]
    monkeypatch.setattr(
        summarize, "assemble_from_manifest",
        lambda *a, **k: ([], False, summarize._diagnostic("speech_gap", "M1", "s_x_00")),
    )

    assert _run_pipeline(tmp_path, fake_client, monkeypatch, meetings, batch=True) == \
        summarize.EXIT_SYSTEMIC_FAILURE


def _run_pipeline_with_all_results_errored(fake_client, tmp_path, monkeypatch, meetings):
    """Every summary request in the batch comes back errored — #51's shape.

    Grouping is stubbed normally (default: one real thread per meeting) so
    each meeting actually sends a summary request, then the batch answers
    every one of them with an errored result entry — so trigger 1 (nothing
    usable came back) and trigger 2 (assembly can't find those results
    either) both fire from the same underlying cause.
    """
    _stub_grouping(monkeypatch)
    b = fake_client.messages.batches
    b.statuses["msgbatch_fake_0001"] = "ended"
    b.results_by_id["msgbatch_fake_0001"] = [
        _errored_entry(summarize.make_batch_custom_id(m["meetingId"], 0))
        for m in meetings
    ]
    return _run_pipeline(tmp_path, fake_client, monkeypatch, meetings, batch=True)


def test_a_fully_rejected_batch_fires_both_triggers_and_says_so(
        fake_client, tmp_path, monkeypatch, capsys):
    """#51's shape: every summary request errors.

    Trigger 1 fires (nothing usable came back) and trigger 2 fires too, because
    assembly then cannot find those results. The date must be counted ONCE, and
    the annotation must not claim the answers arrived.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    exit_code = _run_pipeline_with_all_results_errored(
        fake_client, tmp_path, monkeypatch, meetings=[_meeting("M1"), _meeting("M2")],
    )
    assert exit_code == summarize.EXIT_SYSTEMIC_FAILURE
    out = capsys.readouterr().out
    annotations = [ln for ln in out.splitlines() if ln.startswith("::error::")]
    assert len(annotations) == 1, "the date must be annotated once, not per trigger"
    assert "produced no usable summary" in annotations[0]
    assert "assembly failed: missing_result" in annotations[0]
    assert "answered" not in annotations[0].lower(), (
        "must not claim the answers arrived while the API was rejecting everything"
    )


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
    # The threshold itself (SUSPECT_N >= 2, FAIL_DATES escalation) no longer
    # lives here — it moved to the last step, which is the only place that
    # sees both the Summarize and Collect paths (test_the_threshold_lives_
    # in_the_final_step_and_dedupes and test_the_summarize_step_no_longer_
    # applies_the_threshold cover that).
    assert 'systemic_dates=$SYSTEMIC' in run, (
        "the systemic dates never reach GITHUB_OUTPUT"
    )
    assert 'suspect_dates=$SUSPECT' in run, (
        "suspect dates are invisible in the run summary"
    )
    # A crash on a later date skips every step below, so the annotation is the
    # only channel that survives to name an earlier date's outage.
    assert "::error::" in run

    # And the job must still be failed, after everything else has run.
    # No `if:` any more — the last step always runs and decides inside, because
    # a GitHub expression can neither union two lists nor compare a count. Note
    # this still leaves the step an implicit success(), so anything that fails an
    # earlier step skips it. Since #65 that is no longer a sidecar's state —
    # every regime exits 0 — it is a genuine crash, which already failed the job
    # and whose annotations survive. `if: always()` is deliberately NOT the answer.
    assert "if" not in steps[-1]
    assert "exit 1" in steps[-1]["run"]


def _workflow_steps():
    yaml = pytest.importorskip("yaml")
    path = REPO_ROOT / ".github" / "workflows" / "daily-batch.yml"
    wf = yaml.safe_load(path.read_text(encoding="utf-8"))
    return wf["jobs"]["fetch-and-summarize"]["steps"]


def test_the_collect_step_does_not_block_the_publish():
    """Collect exits 0 on a soft verdict, so it needs no rc capture — but it
    must also not have grown one that swallows a hard fail."""
    steps = _workflow_steps()
    collect = next(s for s in steps if s.get("id") == "collect")
    assert "|| rc=" not in collect["run"], (
        "Collect answers with outputs, not an exit code — an rc capture here "
        "would be a second transport and could swallow a hard fail"
    )
    assert "set -e" in collect["run"]


def test_the_final_step_reads_both_paths():
    """#59's acceptance condition: a sidecar-owned date is SKIPPED within
    Summarize (per-date, since #65), so a verdict that only travels through
    Summarize's outputs can never fail the job for the dates Collect owns."""
    steps = _workflow_steps()
    final = steps[-1]
    run = final["run"]
    for ref in ("steps.summarize.outputs.systemic_dates",
                "steps.summarize.outputs.suspect_dates",
                "steps.collect.outputs.systemic_dates",
                "steps.collect.outputs.suspect_dates"):
        assert ref in str(final.get("env", {})), f"{ref} never reaches the final step"
    assert "exit 1" in run


def test_the_threshold_lives_in_the_final_step_and_dedupes():
    """The threshold counts DATES, so the union has to be a set. Concatenating
    two lists that name the same date would cross the threshold on its own."""
    steps = _workflow_steps()
    run = steps[-1]["run"]
    assert "-ge 2" in run, "the suspect threshold must be findable in one place"
    assert "sort -u" in run, "the union must deduplicate dates"
    # Moved here from the Summarize-step test, which used to be the only thing
    # asserting that a suspect date can EVER be escalated. Without this line the
    # whole suite stays green while suspect dates are collected, reported, and
    # then silently never fail the job — which is the exact shape of failure
    # this repo keeps paying for.
    assert 'FAIL_DATES="$FAIL_DATES$SUSPECT"' in run, (
        "suspect dates are collected but never escalate"
    )


def test_the_summarize_step_no_longer_applies_the_threshold():
    """It cannot: it never sees the dates Collect reported (only Collect
    owns those), so a policy applied here would be blind to half its input."""
    steps = _workflow_steps()
    summarize_step = next(s for s in steps if s.get("id") == "summarize")
    assert "-ge 2" not in summarize_step["run"]


# ---------------------------------------------------------------------------
# The --collect-pending contract (#59): a bare exit code cannot carry many
# dates' verdicts, so this path writes GITHUB_OUTPUT instead of exiting 3/4.
# ---------------------------------------------------------------------------

def _isolated_collect_argv(tmp_path):
    """argv that keeps main() away from the repo's committed data files.

    The defaults are data/members.json and data/threads: main() calls
    save_members() unconditionally, so a test that omits these REWRITES
    committed data as a side effect of asserting on an exit code.
    """
    return [
        "--collect-pending",
        "--members-path", str(tmp_path / "members.json"),
        "--output-dir", str(tmp_path / "threads"),
        "--pending-dir", str(tmp_path / "pending"),
        "--raw-dir", str(tmp_path / "raw"),
    ]


def _stub_client(monkeypatch):
    """anthropic.Anthropic() is constructed for real otherwise, and load_dotenv
    means it can pick up a live key."""
    monkeypatch.setattr(summarize.anthropic, "Anthropic", lambda *a, **k: object())


def test_collect_pending_exits_zero_on_a_soft_verdict(monkeypatch, tmp_path):
    """--collect-pending handles MANY dates in one process, so a single exit
    code cannot carry the verdicts; the date lists do. Returning 3 here would
    only add a second transport and, under the workflow's set -e, would block
    the publish — the amplification #52 was about."""
    # Tests must not write to CI state: without this, running inside GitHub
    # Actions makes _write_github_output append to the harness's real
    # GITHUB_OUTPUT file.
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    _stub_client(monkeypatch)
    monkeypatch.setattr(summarize, "collect_pending_batches", lambda *a, **k: {
        "hard_fail": False, "systemic_dates": ["2026-05-14"],
        "suspect_dates": [],
        "held_dates": [], "abandoned_dates": [],
        "diagnostics": [],
    })
    with pytest.raises(SystemExit) as e:
        summarize.main(_isolated_collect_argv(tmp_path))
    assert e.value.code == 0


def test_collect_pending_exits_one_on_a_hard_fail(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    _stub_client(monkeypatch)
    monkeypatch.setattr(summarize, "collect_pending_batches", lambda *a, **k: {
        "hard_fail": True, "systemic_dates": [], "suspect_dates": [],
        "held_dates": [], "abandoned_dates": [],
        "diagnostics": [],
    })
    with pytest.raises(SystemExit) as e:
        summarize.main(_isolated_collect_argv(tmp_path))
    assert e.value.code == 1


def test_collect_pending_writes_deduplicated_dates_to_github_output(
        monkeypatch, tmp_path):
    """Duplicated dates would be counted twice by the workflow's SUSPECT_N
    threshold and could cross it on their own."""
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    _stub_client(monkeypatch)
    monkeypatch.setattr(summarize, "collect_pending_batches", lambda *a, **k: {
        "hard_fail": False, "systemic_dates": [],
        "suspect_dates": ["2026-05-14", "2026-05-14", "2026-05-15"],
        "held_dates": [], "abandoned_dates": [],
        "diagnostics": [],
    })
    with pytest.raises(SystemExit):
        summarize.main(_isolated_collect_argv(tmp_path))
    written = out.read_text()
    assert "systemic_dates=\n" in written
    assert "suspect_dates=2026-05-14 2026-05-15\n" in written


def test_a_morning_of_only_held_and_abandoned_dates_exits_zero(monkeypatch, tmp_path):
    """T8 — through main(), not around it.

    Two things have to hold together and only main() sees both: the four lists
    reach GITHUB_OUTPUT (a verdict that never gets there cannot red the final
    step, which is the only layer that sees every date), AND the process exits 0
    (exit 1 aborts Collect under `set -e`, skipping summarize/commit/push for
    every other date — what #65 removed).
    """
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    _stub_client(monkeypatch)
    monkeypatch.setattr(summarize, "collect_pending_batches", lambda *a, **k: {
        "hard_fail": False, "systemic_dates": [], "suspect_dates": [],
        "held_dates": ["2026-01-02"], "abandoned_dates": ["2026-01-03"],
        "diagnostics": [],
    })
    with pytest.raises(SystemExit) as e:
        summarize.main(_isolated_collect_argv(tmp_path))
    assert e.value.code == 0
    written = out.read_text(encoding="utf-8")
    assert "held_dates=2026-01-02\n" in written
    assert "abandoned_dates=2026-01-03\n" in written
    assert "systemic_dates=\n" in written
    assert "suspect_dates=\n" in written


def test_the_pending_gate_is_per_date_not_per_run():
    """T4 / #44. One uncollectable sidecar used to skip Summarize for EVERY date
    — the amplifier that turned a single stuck batch into a two-month outage.
    Asserting only that the string is gone is not enough: the skip has to happen
    before the python call (or the date is summarized twice) and the run-internal
    break has to stay after it (or one run piles up several in-flight batches).
    """
    steps = _workflow_steps()
    summarize_step = next(s for s in steps if s.get("id") == "summarize")
    cond = summarize_step.get("if", "")
    assert "has_pending" not in cond, (
        "the global gate is what #44 is about; it must not gate the whole step"
    )
    # Exact match, not substring: a run-wide gate by another name (e.g.
    # "steps.collect.outputs.stuck != 'true'") would still contain
    # "steps.dates.outputs.list" as a substring and pass the check above.
    # This is the condition that turned a single stuck batch into a
    # two-month outage (#44), so the test must fail closed on any addition.
    assert cond.strip() == "steps.dates.outputs.list != ''", (
        "any additional condition here is a run-wide gate by another name — "
        "that is what #44 was about"
    )

    run = summarize_step["run"]
    assert "has_pending" not in run

    skip_at = run.find('if [ -f "data/pending-batches/$d.json" ]')
    call_at = run.find("python scripts/summarize.py --date")
    break_at = run.rfind('if [ -f "data/pending-batches/$d.json" ]')
    assert skip_at != -1 and call_at != -1, "the loop no longer looks like itself"
    assert skip_at < call_at, "the per-date skip must precede the summarize call"
    assert break_at > call_at, "the run-internal single-batch break must remain"
    assert "continue" in run[skip_at:call_at], "the skip must continue, not break"
    assert "break" in run[break_at:], "the post-call guard must break"

    collect_step = next(s for s in steps if s.get("id") == "collect")
    assert "has_pending" not in collect_step["run"], (
        "an output nothing reads is a trap: a later reader assumes it still gates"
    )


def test_held_and_abandoned_dates_fail_the_run_without_a_threshold():
    """T5. Both are unconditional: a held sidecar is a request for a decision and
    an abandoned one is a permanent loss. Neither is 'weak evidence' that a
    threshold should soften, and neither may be folded into SUSPECT."""
    steps = _workflow_steps()
    final = next(s for s in steps
                 if s.get("name", "").startswith("Fail the run"))
    env, run = final.get("env", {}), final["run"]
    assert "steps.collect.outputs.held_dates" in str(env.values())
    assert "steps.collect.outputs.abandoned_dates" in str(env.values())
    # The existing suspect escalation must survive verbatim — it is pinned
    # elsewhere in this file for a reason.
    assert 'FAIL_DATES="$FAIL_DATES$SUSPECT"' in run
    # ...and held/abandoned must NOT be folded into it. Check the whole region
    # where FAIL_DATES is assembled, not one line: an earlier draft of this test
    # looked only at the SUSPECT_N= line, so `FAIL_DATES="$FAIL_DATES$SUSPECT$HELD"`
    # would have passed it.
    build = run[run.find("FAIL_DATES="):run.find('if [ -z')]
    assert "$HELD" not in build and "$ABANDONED" not in build, (
        "held and abandoned are unconditional; routing them through the suspect "
        "threshold would soften a permanent loss into 'needs a second occurrence'"
    )
    # Held or abandoned alone must be able to fail the run — and since #75 so
    # must a data file the commit refused to stage, AND the checker having failed
    # to run at all. All four are in the same early-exit guard for the same
    # reason: none of them is weak evidence about one date, so none of them may
    # be routed through the suspect threshold. The last one is not about a file
    # either — it is "nobody looked", which a green run must not swallow.
    assert ('if [ -z "$(echo "$FAIL_DATES$HELD$ABANDONED$BROKEN_JSON'
            '$BROKEN_JSON_CHECK_FAILED"' in run)
    assert "steps.commit.outputs.broken_json" in str(env.values())
    assert "steps.commit.outputs.broken_json_check_failed" in str(env.values())
    assert "$BROKEN_JSON" not in build, (
        "a file that could not be read is not one date's worth of weak evidence")
    assert "Permanently lost" in run
    assert "held for a human decision" in run


def test_the_stuck_notifier_dedups_by_date_and_not_by_muting_itself():
    """#71. The two notifiers must not restate the same date, and must not do it
    by trading away a signal.

    The duplicate was real: a held sidecar reds the run, so notify-on-failure
    comments, and this job commented a SECOND time on the same deduped issue
    every morning for the weeks a human decision can take.

    But `if: success()` is the wrong dedup. The signal only this job has — a
    batch in flight for over two days — does NOT red the run, so muting on red
    mornings reports it zero times for as long as anything else is broken, which
    is exactly the window in which it matters (results expire ~29d, the abandon
    gate writes the batch off at 31d). So: still `always()`, and the overlap is
    removed by excluding the DATES the failure path already reported.
    """
    yaml = pytest.importorskip("yaml")
    path = REPO_ROOT / ".github" / "workflows" / "daily-batch.yml"
    text = path.read_text(encoding="utf-8")
    wf = yaml.safe_load(text)
    stuck = wf["jobs"]["notify-stuck-batch"]
    assert stuck["if"] == "always()", (
        "success() mutes the one signal only this job carries on exactly the "
        "mornings it matters; dedup by date instead"
    )
    assert stuck["needs"] == "fetch-and-summarize"
    # The failure path must still exist — this change is about removing a
    # duplicate, never about going quiet on a red morning (#66).
    assert wf["jobs"]["notify-on-failure"]["if"] == "failure()"

    # The dedup has to actually be wired: exported by the job that computes the
    # dates, and consumed by the notifier. Asserting always() alone would pass
    # with the duplicate fully restored.
    produced = wf["jobs"]["fetch-and-summarize"]["outputs"]
    assert "held_dates" in produced and "abandoned_dates" in produced
    for key in ("held_dates", "abandoned_dates"):
        assert "steps.collect.outputs." + key in produced[key]
    step = [s for s in stuck["steps"] if "--exclude-dates" in str(s.get("run", ""))]
    assert len(step) == 1, "the stuck notifier must pass --exclude-dates"
    reported = step[0]["env"]["ALREADY_REPORTED"]
    for key in ("held_dates", "abandoned_dates"):
        assert "needs.fetch-and-summarize.outputs." + key in reported

    # CI must be able to run this file at all: these YAML guards importorskip,
    # so a missing pyyaml turns every one of them into a silent skip.
    #
    # Followed through the requirements files rather than grepped off the
    # `pip install` line, because since #80 that line is `-r
    # requirements-dev.txt` and a grep for "pyyaml" there passes on nothing.
    # Resolved from what ci.yml actually installs, not from a hard-coded
    # filename, so pointing CI at a different file cannot leave this agreeing
    # with a file CI stopped using.
    ci = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    installs = [str(st.get("run", "")) for j in ci["jobs"].values()
                for st in j.get("steps", []) if "pip install" in str(st.get("run", ""))]
    assert installs, "ci.yml no longer installs anything with pip"

    def _declared(rel, seen=None):
        """Package names one requirements file declares, following `-r`."""
        seen = seen if seen is not None else set()
        if rel in seen:
            return set()
        seen.add(rel)
        names = set()
        for raw in (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith(("-r", "--requirement")):
                names |= _declared(line.split(maxsplit=1)[1].strip(), seen)
            else:
                names.add(line.split("=")[0].split("<")[0].split(">")[0]
                          .split("~")[0].split("[")[0].strip().lower())
        return names

    referenced = {tok for cmd in installs for tok in cmd.split()
                  if tok.endswith((".txt",))}
    assert referenced, (
        "ci.yml installs by name again — see "
        "test_the_anthropic_pin_matches_what_the_summary_layer_imports")
    declared = set()
    for rel in referenced:
        declared |= _declared(rel)
    assert "pyyaml" in declared, (
        f"pyyaml is not in what CI installs ({sorted(referenced)}) — every "
        f"workflow-contract test in this file then skips there, silently")


def test_the_stuck_report_skips_reported_dates_and_keeps_the_rest(
        tmp_path, monkeypatch, capsys):
    """The other half of #71's dedup, exercised for real.

    Asserting the workflow wiring alone would pass with the flag ignored. Two
    stuck sidecars, one of them already in the failure path's held list: the
    reported one must vanish from this report and the other must survive, because
    an in-flight stuck batch is a signal nothing else in the run carries.
    """
    import check_stuck_batches as csb
    from pipeline import batch_state as bs

    pending = tmp_path / "pending"
    monkeypatch.setattr(bs, "PENDING_DIR", str(pending))
    old = "2026-01-01T00:00:00Z"          # far past STUCK_AGE_DAYS in any real now
    for date in ("2026-05-14", "2026-05-15"):
        sc = bs.new_sidecar(date, "claude-x")
        bs.add_attempt(sc, f"b-{date}", old)
        bs.save_sidecar(str(pending / f"{date}.json"), sc)

    monkeypatch.setattr("sys.argv",
                        ["check_stuck_batches.py", "--exclude-dates",
                         " 2026-05-14 "])   # workflow passes a padded string
    csb.main()
    out = capsys.readouterr().out
    assert "2026-05-14" not in out, "a date the failure path reported was restated"
    assert "2026-05-15" in out, "the un-reported stuck batch lost its only reporter"

    # No exclusions -> both, so the filter cannot be silently over-broad.
    monkeypatch.setattr("sys.argv", ["check_stuck_batches.py"])
    csb.main()
    out = capsys.readouterr().out
    assert "2026-05-14" in out and "2026-05-15" in out


def test_one_unreadable_sidecar_does_not_erase_the_whole_stuck_report(
        tmp_path, monkeypatch, capsys):
    """The step runs this as `... || true`, so an exception produces EMPTY output
    — which reads as "nothing is stuck" and hides every other stuck batch too.
    That is the silent-failure shape this notifier exists to prevent, so a
    sidecar it cannot parse must degrade into a visible line, not a raise.
    """
    import check_stuck_batches as csb
    from pipeline import batch_state as bs

    pending = tmp_path / "pending"
    monkeypatch.setattr(bs, "PENDING_DIR", str(pending))
    good = bs.new_sidecar("2026-05-15", "claude-x")
    bs.add_attempt(good, "b-good", "2026-01-01T00:00:00Z")
    bs.save_sidecar(str(pending / "2026-05-15.json"), good)

    # Truncated JSON — load_sidecar only catches FileNotFoundError, so this
    # raises JSONDecodeError from the first line of _line_for. Chosen over a
    # wrong-typed "date" field on purpose: bs.reporting_date now rejects a
    # non-string date at the source, so that input no longer reaches a raise and
    # a test built on it would assert the degraded line while never producing
    # one. An unparseable file is the case that survives, and it is real —
    # sidecars are committed by a job that can be killed mid-write (#57).
    (pending / "2026-05-14.json").write_text('{"date": "2026-05-14",',
                                             encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["check_stuck_batches.py"])
    csb.main()                                  # must not raise
    out = capsys.readouterr().out
    assert "UNREADABLE sidecar" in out, "the broken sidecar went silent"
    assert "2026-05-14.json" in out             # named by file: nothing else parsed
    assert "b-good" in out, "a neighbour lost its only reporter"


# ---------------------------------------------------------------------------
# Corrupt files outside the resume path (#74)
#
# #57's atomic writer stopped the pipeline from CREATING these, so what is left
# is a file that arrived broken by another route (a hand edit, a bad merge, a
# failing disk). The question each of these pins is not "does it survive" but
# "what does it do instead", and the answer differs per reader because what is
# lost differs: cross-date links are decoration, a source's raw comes back on
# the next fetch, and an existing threads file is the only copy of published
# work.
# ---------------------------------------------------------------------------

def _corrupt(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"meetings": [{"meetingId": "M1",')       # truncated mid-write


def test_one_corrupt_raw_file_does_not_stop_the_other_sources_for_that_date(
        fake_client, tmp_path, monkeypatch, capsys):
    """A date can have raw from four adapters. Losing one to a bad file must not
    discard the three that parsed — and must not abort the date loop, which
    under `set -e` takes every LATER date's publish with it."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _stub_grouping(monkeypatch)
    raw_dir = _write_raw(tmp_path, [_meeting("M1")])
    _corrupt(os.path.join(raw_dir, "kantei-2026-05-14.json"))

    meetings, unreadable = summarize.load_raw_meetings_for_date(
        "2026-05-14", raw_dir)
    assert [m["meetingId"] for m in meetings] == ["M1"]
    assert len(unreadable) == 1 and "kantei-2026-05-14.json" in unreadable[0]


def test_a_date_whose_every_raw_file_is_corrupt_is_reported_not_silently_empty(
        fake_client, tmp_path, monkeypatch, capsys):
    """Zero meetings from unreadable files looks exactly like zero meetings from
    a quiet day. One of those is fine and one is a broken runner, so the verdict
    has to distinguish them — and the annotation must say the cause is a local
    file, not an API rejection, or the operator goes hunting a 400."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _stub_grouping(monkeypatch)
    raw_dir = _write_raw(tmp_path, [_meeting("M1")])
    _corrupt(os.path.join(raw_dir, "ndl-2026-05-14.json"))
    monkeypatch.setattr(summarize.anthropic, "Anthropic", lambda *a, **k: fake_client)

    verdict = summarize.run_pipeline(
        date_str="2026-05-14", raw_dir=raw_dir,
        output_dir=str(tmp_path / "threads"),
        members_path=str(tmp_path / "members.json"),
        batch=False, batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"),
    )
    assert verdict == summarize.EXIT_SYSTEMIC_FAILURE
    out = capsys.readouterr().out
    errors = [ln for ln in out.splitlines() if ln.startswith("::error::")]
    assert any("ndl-2026-05-14.json" in ln for ln in errors), errors
    assert any("not an API rejection" in ln for ln in errors), errors


def test_a_corrupt_threads_file_is_never_overwritten_by_a_resumed_run(
        fake_client, tmp_path, monkeypatch, capsys):
    """The one file in this pipeline that is the ONLY copy of published work.

    Reading it fails, so the resumed run cannot know what is already there —
    and appending to `[]` would republish the date with just this run's threads,
    silently dropping every thread the file still holds. Refuse the date
    instead, leave the file untouched, and let the other dates publish.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _stub_grouping(monkeypatch)
    raw_dir = _write_raw(tmp_path, [_meeting("M1")])
    threads_dir = tmp_path / "threads"
    threads_dir.mkdir()
    existing = threads_dir / "2026-05-14.json"
    _corrupt(str(existing))
    before = existing.read_text(encoding="utf-8")
    monkeypatch.setattr(summarize.anthropic, "Anthropic", lambda *a, **k: fake_client)

    verdict = summarize.run_pipeline(
        date_str="2026-05-14", raw_dir=raw_dir, output_dir=str(threads_dir),
        members_path=str(tmp_path / "members.json"),
        batch=False, batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"),
    )
    assert verdict == summarize.EXIT_SYSTEMIC_FAILURE
    assert existing.read_text(encoding="utf-8") == before, "the only copy was rewritten"
    # "The file was not rewritten" is NOT enough on its own, and finding that out
    # is why this assertion exists: with the guard removed the run continues,
    # produces no usable threads from the fakes, and writes nothing anyway — so
    # the file check passed while the protection was gone (CLAUDE.md: a passing
    # test may reach its assertion by another path). Pin the thing the guard
    # actually controls: the date is refused BEFORE any API call is made.
    assert fake_client.messages.create_calls == []
    assert fake_client.messages.batches.created_requests == []
    errors = [ln for ln in capsys.readouterr().out.splitlines()
              if ln.startswith("::error::")]
    assert any("2026-05-14.json" in ln for ln in errors), errors


def test_a_corrupt_file_from_another_date_only_costs_that_date_its_links(
        tmp_path, capsys, monkeypatch):
    """Blast radius. This reader walks EVERY date's threads file, so letting it
    raise turns one bad file into a failure for every date in the run — the
    largest reach of the three, and over the most disposable thing: cross-date
    links are auxiliary and come back the moment the file does."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    threads_dir = tmp_path / "threads"
    threads_dir.mkdir()
    with open(threads_dir / "2026-05-01.json", "w", encoding="utf-8") as f:
        json.dump([{"id": "t_20260501_aaaaaa_01"}], f)
    _corrupt(str(threads_dir / "2026-05-02.json"))

    others = summarize.load_threads_from_other_dates(str(threads_dir), "2026-05-14")
    assert [t["id"] for t in others] == ["t_20260501_aaaaaa_01"]
    warnings = [ln for ln in capsys.readouterr().out.splitlines()
                if ln.startswith("::warning::")]
    assert any("2026-05-02.json" in ln for ln in warnings), warnings


# ---------------------------------------------------------------------------
# Parses fine, wrong shape (Gate3 on #74)
#
# The guards above all sit on `json.load` raising. A file that arrives from a
# hand edit or a half-finished migration parses cleanly into the WRONG shape,
# and every one of these readers then fails later and elsewhere: `.extend()` on
# a string appends its characters, on a dict its KEYS, and the AttributeError
# surfaces in link_threads or the grouper — outside the guard, as exit 1, which
# is the publish-stopping outcome #74 exists to prevent. Same verdict as a
# parse failure is the right answer: either way we cannot tell what the file
# held.
# ---------------------------------------------------------------------------

def _write_shaped(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def test_a_raw_file_whose_meetings_is_not_a_list_counts_as_unreadable(tmp_path):
    """A `"meetings"` string parses. Left alone it extends the meeting list with
    the characters n/o/n/e, and the run dies on `m.get` two functions later with
    nothing naming the file."""
    raw_dir = _write_raw(tmp_path, [_meeting("M1")])
    _write_shaped(os.path.join(raw_dir, "kantei-2026-05-14.json"),
                  {"meetings": "none"})

    meetings, unreadable = summarize.load_raw_meetings_for_date(
        "2026-05-14", raw_dir)
    assert [m["meetingId"] for m in meetings] == ["M1"], (
        "a character from the string leaked in as a meeting")
    assert len(unreadable) == 1 and "kantei-2026-05-14.json" in unreadable[0]


def test_a_raw_file_holding_non_objects_counts_as_unreadable(tmp_path):
    """The list-of-wrong-things variant: `["M1"]` survives a bare
    isinstance(list) check and dies on the same `m.get` further down."""
    raw_dir = _write_raw(tmp_path, [_meeting("M1")])
    _write_shaped(os.path.join(raw_dir, "kantei-2026-05-14.json"),
                  {"meetings": ["M1"]})

    meetings, unreadable = summarize.load_raw_meetings_for_date(
        "2026-05-14", raw_dir)
    assert [m["meetingId"] for m in meetings] == ["M1"]
    assert len(unreadable) == 1


def test_a_partly_unreadable_date_says_out_loud_what_it_published_without(
        fake_client, tmp_path, monkeypatch, capsys):
    """The only branch here that ends GREEN, which is exactly why it needs an
    annotation: the date publishes, missing one source's meetings entirely, and
    a `log.error` is invisible on a green run."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _stub_grouping(monkeypatch)
    raw_dir = _write_raw(tmp_path, [_meeting("M1")])
    _corrupt(os.path.join(raw_dir, "kantei-2026-05-14.json"))
    monkeypatch.setattr(summarize.anthropic, "Anthropic", lambda *a, **k: fake_client)

    def _run(rd, out):
        return summarize.run_pipeline(
            date_str="2026-05-14", raw_dir=rd, output_dir=str(out),
            members_path=str(tmp_path / "members.json"),
            batch=False, batch_timeout_seconds=0, batch_poll_seconds=0,
            pending_dir=str(tmp_path / "pending"),
        )

    verdict = _run(raw_dir, tmp_path / "threads")

    # Compared against a control rather than asserted to be 0, because the
    # claim is "the unreadable file did not change the verdict" — and asserting
    # 0 outright would pin whatever the fake client happens to produce, which
    # is a different fact and one that drifts.
    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    clean_dir = _write_raw(clean_root, [_meeting("M1")])
    assert verdict == _run(clean_dir, tmp_path / "clean-threads"), (
        "the skipped raw file changed this date's verdict; the whole point of "
        "skipping is that the surviving sources decide it")
    warnings = [ln for ln in capsys.readouterr().out.splitlines()
                if ln.startswith("::warning::")]
    assert any("kantei-2026-05-14.json" in ln for ln in warnings), warnings
    assert any("not an API rejection" in ln for ln in warnings), warnings


def test_a_threads_file_that_parsed_into_a_dict_never_leaks_its_keys(
        tmp_path, capsys, monkeypatch):
    """`threads.extend(some_dict)` appends the KEYS — strings, which reach
    link_threads and raise there. Nothing about that failure names this file."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    threads_dir = tmp_path / "threads"
    threads_dir.mkdir()
    _write_shaped(str(threads_dir / "2026-05-01.json"),
                  [{"id": "t_20260501_aaaaaa_01"}])
    _write_shaped(str(threads_dir / "2026-05-02.json"),
                  {"threads": [{"id": "t_20260502_bbbbbb_01"}]})

    others = summarize.load_threads_from_other_dates(str(threads_dir), "2026-05-14")
    assert [t["id"] for t in others] == ["t_20260501_aaaaaa_01"]
    warns = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith("::warning::")]
    assert any("2026-05-02.json" in ln for ln in warns), warns


def test_many_broken_neighbours_still_produce_one_annotation(
        tmp_path, capsys, monkeypatch):
    """Every date in the run walks this directory, so per-file annotations
    multiply by the number of dates. GitHub shows 10 per level per step, and
    the eleventh is the one that mattered."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    threads_dir = tmp_path / "threads"
    threads_dir.mkdir()
    for day in range(1, 7):
        _corrupt(str(threads_dir / f"2026-05-0{day}.json"))

    summarize.load_threads_from_other_dates(str(threads_dir), "2026-05-14")
    warns = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith("::warning::")]
    assert len(warns) == 1, warns
    assert "2026-05-01.json" in warns[0] and "2026-05-06.json" in warns[0]


def test_a_wrongly_shaped_threads_file_is_refused_like_a_corrupt_one(
        fake_client, tmp_path, monkeypatch, capsys):
    """The resume read. A dict here has a `len()`, so `thread_counter` would be
    set from its key count and the refusal never fire — the date then
    republishes from an `all_threads` that no longer holds what the file did."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _stub_grouping(monkeypatch)
    raw_dir = _write_raw(tmp_path, [_meeting("M1")])
    threads_dir = tmp_path / "threads"
    threads_dir.mkdir()
    existing = threads_dir / "2026-05-14.json"
    _write_shaped(str(existing), {"threads": [{"id": "t_20260514_aaaaaa_01"}]})
    before = existing.read_text(encoding="utf-8")
    monkeypatch.setattr(summarize.anthropic, "Anthropic", lambda *a, **k: fake_client)

    verdict = summarize.run_pipeline(
        date_str="2026-05-14", raw_dir=raw_dir, output_dir=str(threads_dir),
        members_path=str(tmp_path / "members.json"),
        batch=False, batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"),
    )
    assert verdict == summarize.EXIT_SYSTEMIC_FAILURE
    assert existing.read_text(encoding="utf-8") == before
    assert fake_client.messages.create_calls == []
    assert fake_client.messages.batches.created_requests == []


def test_an_empty_object_is_refused_and_not_read_as_an_empty_list(tmp_path):
    """The case that makes the container check load-bearing rather than
    decorative. Every other wrong shape trips the per-element check on its way
    through — a dict yields its keys, a string its characters — but `{}` yields
    nothing at all, so without the container check it returns as a perfectly
    well-behaved empty result and the caller publishes over it.
    """
    with pytest.raises(TypeError):
        summarize._as_list_of_dicts({}, "x.json")
    with pytest.raises(TypeError):
        summarize._as_list_of_dicts("", "x.json")
    assert summarize._as_list_of_dicts([], "x.json") == []


@pytest.mark.parametrize("payload", [
    '{"completed": [',            # truncated
    '["not-an-object"]',          # wrong container
    '{}',                         # parses, but KeyError on progress["completed"]
    '{"failed": []}',             # one key present, one missing
    '{"completed": null}',        # present but None -> .append AttributeError
    '{"completed": {"a": 1}}',    # present but a dict -> no .append
])
def test_an_unreadable_progress_file_falls_back_to_the_default(payload, tmp_path):
    """Scratch bookkeeping, and the cheapest possible answer is the right one:
    `*.progress.json` is gitignored, so it is never the only copy of anything —
    resume re-derives it from the date's actual threads. Raising here cost the
    entire morning's publish for a file nobody would miss.

    The last four cases are the ones a container-only check misses. Every
    caller does `progress["completed"].append(...)` outside any try, so each of
    them is still exit 1 unless the guard looks at the CONTENTS.
    """
    progress = tmp_path / "2026-05-14.progress.json"
    progress.write_text(payload, encoding="utf-8")
    assert summarize.load_progress(str(progress)) == {"completed": [], "failed": []}


def test_an_unreadable_progress_file_does_not_duplicate_published_threads(
        fake_client, tmp_path, monkeypatch, capsys):
    """The regression the guard itself created, and worse than what it fixed.

    Treating an unreadable progress file as a missing one means `completed`
    starts empty. On the EXPLICIT `--resume` path — the one a human reaches for
    to unstick a date — nothing reseeded it, so every meeting was re-summarised
    and APPENDED next to the copy already in the threads file. Exit 1 is loud;
    duplicated published threads are not.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _stub_grouping(monkeypatch)
    raw_dir = _write_raw(tmp_path, [_meeting("M1")])
    threads_dir = tmp_path / "threads"
    threads_dir.mkdir()
    mid_hash = hashlib.sha256("M1".encode("utf-8")).hexdigest()[:6]
    _write_shaped(str(threads_dir / "2026-05-14.json"),
                  [{"id": f"t_20260514_{mid_hash}_00", "topic": "T"}])
    (threads_dir / "2026-05-14.progress.json").write_text(
        '{"completed": [', encoding="utf-8")
    monkeypatch.setattr(summarize.anthropic, "Anthropic", lambda *a, **k: fake_client)

    summarize.run_pipeline(
        date_str="2026-05-14", raw_dir=raw_dir, output_dir=str(threads_dir),
        members_path=str(tmp_path / "members.json"),
        resume=True,
        batch=False, batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=str(tmp_path / "pending"),
    )
    # Pin what the reseed actually controls — whether the meeting is asked
    # about at all. Checking only the file contents is not enough and finding
    # that out is why this comment exists: with the reseed reverted the run DOES
    # re-summarize M1, the fake produces nothing usable, nothing is appended,
    # and the file check passes while the protection is gone (CLAUDE.md: a
    # passing test may reach its assertion by another path).
    assert fake_client.messages.create_calls == [], (
        "M1 is already represented in the threads file, but the run asked "
        "about it again — with a working model that is a duplicate thread")
    after = json.loads((threads_dir / "2026-05-14.json").read_text(encoding="utf-8"))
    assert [t["id"] for t in after] == [f"t_20260514_{mid_hash}_00"], (
        "the meeting was re-summarized and appended alongside its own thread")


def test_an_unreadable_lexdiff_map_costs_only_the_cross_links(
        tmp_path, monkeypatch, capsys):
    """Auxiliary by CLAUDE.md's own classification, so it must not be able to
    take the summary layer with it — and it must not be SILENT either, because
    the run that loses the links is green and the links are never back-filled.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(summarize, "_LEXDIFF_MAP", None)
    broken = tmp_path / "lexdiff-mapping.json"
    broken.write_text('{"a":', encoding="utf-8")
    monkeypatch.setattr(summarize, "_LEXDIFF_PATH", str(broken))

    assert summarize._get_lexdiff_map() == {}
    warns = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith("::warning::")]
    assert any("lexdiff-mapping.json" in ln for ln in warns), (
        "a green run lost every outbound law link and said so only in the log")


def test_a_threads_file_without_ids_does_not_take_the_run_down(
        tmp_path, capsys, monkeypatch):
    """One level deeper than `list of dicts`, and reachable: a half-finished
    field rename leaves `[{"topic": "T"}]`, which passes a dict check and then
    raises `KeyError: 'id'` in the id set-comprehension — not in
    _RAW_READ_ERRORS, so exit 1 and no publish, from the reader whose entire
    justification is that one bad file only costs links."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    threads_dir = tmp_path / "threads"
    threads_dir.mkdir()
    _write_shaped(str(threads_dir / "2026-05-01.json"), [{"topic": "T"}])
    _write_shaped(str(threads_dir / "2026-05-03.json"),
                  [{"id": 12345, "topic": "T"}])        # id present, not a str
    _write_shaped(str(threads_dir / "2026-05-02.json"),
                  [{"id": "t_20260502_bbbbbb_01"}])

    others = summarize.load_threads_from_other_dates(str(threads_dir), "2026-05-14")
    assert [t["id"] for t in others] == ["t_20260502_bbbbbb_01"]
    warns = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith("::warning::")]
    assert any("2026-05-01.json" in ln and "2026-05-03.json" in ln
               for ln in warns), warns


def test_the_aggregated_warning_names_a_bounded_number_of_files(
        tmp_path, capsys, monkeypatch):
    """GitHub truncates a long annotation from the end, so an uncapped list of
    150 filenames buries the actionable half in the part that gets cut."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    threads_dir = tmp_path / "threads"
    threads_dir.mkdir()
    for day in range(1, 26):
        _corrupt(str(threads_dir / f"2026-05-{day:02d}.json"))

    summarize.load_threads_from_other_dates(str(threads_dir), "2026-06-14")
    warns = [ln for ln in capsys.readouterr().out.splitlines()
             if ln.startswith("::warning::")]
    assert len(warns) == 1, warns
    assert warns[0].count("2026-05-") == summarize._MAX_NAMED_FILES
    assert f"and {25 - summarize._MAX_NAMED_FILES} more" in warns[0]
    assert "25 unreadable" in warns[0], "the true count went missing"


def test_the_metrics_step_cannot_kill_the_publish_over_a_corrupt_file():
    """The other half of the same contract, and the half that made the Python
    half pointless. summarize.py returns 3 instead of raising SO THAT the loop
    survives and the commit step runs — but the metrics step in between reads
    every date's threads file with a bare `python3 -c json.load` under `bash -e`,
    where a failing command substitution in an assignment FAILS THE STEP. The
    commit step is `success()`-gated, so one corrupt file skipped the publish
    for every date, plus IndexNow, the summary, and the final diagnostic step
    that would have said why.

    Pinned here rather than left to review because the asymmetry is invisible
    at a glance: the neighbouring `BEFORE=` assignment has always had `|| echo 0`
    and `COUNT=` did not.
    """
    yaml = pytest.importorskip("yaml")
    path = REPO_ROOT / ".github" / "workflows" / "daily-batch.yml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = spec["jobs"]["fetch-and-summarize"]["steps"]
    run = next(s for s in steps if s.get("id") == "metrics")["run"]

    for line in run.splitlines():
        if "json.load" not in line:
            continue
        stripped = line.strip()
        assert stripped.endswith("\\") or "|| echo 0" in run.split(stripped)[1][:60], (
            f"a bare json.load in the metrics step: {stripped!r} — under "
            f"`bash -e` this fails the step and skips the commit")
    assert run.count("|| echo 0") >= 2, (
        "both threads-file reads in this step must survive a corrupt file")


def test_the_held_regime_list_shown_to_operators_matches_the_real_policy():
    """A factual enumeration in an operator-facing message drifts every time a
    HOLD reason is added — this diff added one and the message did not move.
    The message hedges ('read the per-date annotation'), so a stale list only
    misleads mildly, which is exactly why nothing catches it."""
    yaml = pytest.importorskip("yaml")
    from pipeline import batch_state as bs
    path = REPO_ROOT / ".github" / "workflows" / "daily-batch.yml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = spec["jobs"]["fetch-and-summarize"]["steps"]
    message = "\n".join(s["run"] for s in steps if "run" in s)

    holds = [r for r, p in bs.FAILURE_POLICY.items() if p == bs.HOLD]
    missing = [r for r in holds if r not in message]
    assert not missing, (
        f"HOLD reason(s) {missing} never appear in any operator-facing message "
        f"in the workflow — a held sidecar can be reported with a reason the "
        f"person reading has no way to look up")


# ---------------------------------------------------------------------------
# Outcome requests are observable too (#60)
#
# has_question_for_the_api used to mean "sends a GROUPING request", and its
# docstring said why: a procedural-only meeting carrying a 附帯決議 still sends an
# outcome request, but extract_meeting_outcome swallowed that request's
# exceptions, so counting the meeting as `attempted` would have made it one that
# can never become `failed` — masking outages instead of catching them.
#
# So the hole was shaped like this: on a date whose only meeting is procedural
# with a 附帯決議, every request can be rejected all morning and the run is green
# with nothing attempted. Fixing the swallow is what lets the pre-check mean what
# its name says.
# ---------------------------------------------------------------------------

def _procedural_meeting_with_a_resolution(meeting_id="R1"):
    """Procedural-only, so no grouping request — but it DOES send an outcome one.

    The chair's speech has to be long enough that build_outcome_messages keeps it
    (>50 chars) and must contain a resolution keyword the pattern matcher finds,
    or this meeting sends nothing at all and the test is vacuous —
    test_the_fixture_really_only_asks_the_outcome_question pins both halves.
    """
    return _meeting(meeting_id, speeches=[
        {"speechOrder": 1,
         "speech": "本案に対する附帯決議案を議題といたします。案文はお手元に配付いた"
                   "しましたとおりでございます。これより採決に入ります。本附帯決議案に"
                   "賛成の諸君の起立を求めます。起立多数と認めます。よって、本附帯決議案は"
                   "可決されました。",
         "speaker": "委員長", "speakerRole": "委員長", "speakerGroup": "",
         "speakerPosition": "委員長", "speechURL": "http://x"},
    ])


def test_the_fixture_really_only_asks_the_outcome_question():
    """Guards the two tests below from going vacuous.

    If the fixture stopped producing an outcome request they would pass while
    testing nothing at all — the shape this file's own history is full of.
    """
    from pipeline.grouper import build_grouping_messages, build_outcome_messages
    m = _procedural_meeting_with_a_resolution()
    assert build_grouping_messages(m) is None, "it would send a grouping request"
    assert build_outcome_messages(m) is not None, "it sends no outcome request"


def test_a_meeting_that_only_asks_the_outcome_question_counts_as_attempted():
    """The pre-check now means what its name says: does this reach the API."""
    assert summarize.has_question_for_the_api(
        _procedural_meeting_with_a_resolution()) is True
    # Unchanged for the two cases that already worked.
    assert summarize.has_question_for_the_api(_meeting()) is True
    assert summarize.has_question_for_the_api(_procedural_meeting()) is False


def test_a_rejected_outcome_request_is_the_only_failure_the_date_can_report(
        fake_client, tmp_path, monkeypatch, capsys):
    """The hole, end to end: one procedural meeting with a 附帯決議, its outcome
    request rejected. No thread can exist, nothing else was asked, and before #60
    the run was green with attempted=0.

    The real extract_meeting_outcome runs here and the CLIENT is what fails —
    stubbing the function to raise would test exception propagation instead, and
    pass even with the swallow (and the silence) still in place.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(fake_client.messages, "create", _raise(_overloaded()))
    verdict = _run_pipeline(tmp_path, fake_client, monkeypatch,
                            [_procedural_meeting_with_a_resolution()], batch=True)
    assert verdict == summarize.EXIT_SYSTEMIC_FAILURE
    errors = [ln for ln in capsys.readouterr().out.splitlines()
              if ln.startswith("::error::")]
    assert errors, "an outage on this date's only meeting said nothing"


def test_a_failed_outcome_request_is_counted_without_being_raised():
    """The unit. Counted, and still not raised — an outcome enriches a
    pattern-matched result, so the meeting must keep the outcome it already has.
    """
    from pipeline.grouper import extract_meeting_outcome

    class _Failing:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("400")

    stats = summarize.new_api_stats()
    meeting = _procedural_meeting_with_a_resolution()
    outcome = extract_meeting_outcome(_Failing(), meeting, model="claude-x",
                                      outcome_stats=stats)
    assert stats == {"attempted": 1, "failed": 1}
    # The pattern matcher already found the resolution; the API call was only
    # ever going to summarise it.
    assert outcome.get("resolution")


def test_an_outcome_failure_does_not_fail_a_meeting_that_asked_anything_else():
    """The other half, and the reason this is not just "count outcome too".

    A 附帯決議 blurb is not required for a thread. A meeting whose grouping and
    summaries worked has published its speeches; failing it over the missing
    blurb reds the run over something no reader can see, and two such meetings on
    one date would read as an outage.
    """
    api_stats = summarize.new_api_stats()
    api_stats["attempted"] = 1
    outcome_stats = {"attempted": 1, "failed": 1}

    assert summarize._count_outcome_only_failure(
        "M1", {"grouping", "outcome"}, outcome_stats, api_stats) is False
    assert api_stats == {"attempted": 1, "failed": 0}

    # ...and it IS folded in when the outcome request is the whole of what the
    # meeting asked, which is the case that was invisible before #60.
    assert summarize._count_outcome_only_failure(
        "R1", {"outcome"}, outcome_stats, api_stats) is True
    assert api_stats == {"attempted": 1, "failed": 1}


@pytest.mark.parametrize("batch", [False, True])
def test_a_counted_outcome_failure_is_not_also_filed_as_completed(
        fake_client, tmp_path, monkeypatch, batch):
    """A meeting charged to ``failed`` must be retried by ``--resume``.

    Both paths reach the completed/failed bookkeeping by their own route (the
    synchronous loop files per meeting, the batch path files from
    ``completed_meeting_ids``), so both are driven here. Filing this meeting as
    completed would let the next resume skip the one question it ever asks —
    the failure would vanish from the counters, which is the same invisibility
    #60 exists to end, just one layer further out.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(fake_client.messages, "create", _raise(_overloaded()))
    _run_pipeline(tmp_path, fake_client, monkeypatch,
                  [_procedural_meeting_with_a_resolution("R1")], batch=batch)

    progress_path = tmp_path / "threads" / "2026-05-14.progress.json"
    assert progress_path.exists(), (
        "the progress file was deleted, i.e. the run considered itself fully "
        "complete while it had just charged a meeting to api_stats['failed']")
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["failed"] == ["R1"]
    assert progress["completed"] == []


def test_an_outcome_response_that_parses_but_answers_nothing_is_a_failure():
    """Parsing is not answering.

    An empty object satisfies every ``.get()`` in extract_meeting_outcome, so
    without a shape check the request is filed as a success having contributed
    nothing — the same fail-open shape as the swallow #60 removed, one step
    later. The check is deliberately "none of the three keys", not "resolution
    is set": build_outcome_messages passes only the last 10 chair speeches, so a
    null resolution is a legitimate answer and failing on it would red the run
    on a correct response — which this test pins as the second case.
    """
    from pipeline.grouper import extract_meeting_outcome

    def _client_returning(text):
        class _C:
            class messages:
                @staticmethod
                def create(**kwargs):
                    class _R:
                        content = [type("B", (), {"text": text})()]
                        stop_reason = "end_turn"
                        usage = None
                    return _R()
        return _C()

    meeting = _procedural_meeting_with_a_resolution()

    def _stats_for(text):
        stats = summarize.new_api_stats()
        outcome = extract_meeting_outcome(_client_returning(text), meeting,
                                          model="claude-x", outcome_stats=stats)
        return stats, outcome

    # Enumerated rather than one example each way: a single "{}" case is
    # satisfied by an implementation that merely tests `api_result != {}`, which
    # would let every partial shape below back through.
    rejected = [
        "{}",                                    # nothing at all
        '{"foo": 1}',                            # no recognised key
        '{"status": "ongoing"}',                 # a key, but nothing answered
        '{"result": null}',                      # ditto
        '{"result": null, "resolution": null}',  # two of three, both empty
        '["result"]',                            # right words, wrong type
        # Wrong type in the value, which is the case that does not stay inside
        # this function: merged unchecked, a dict lands where the site and the
        # MCP bundle expect a string.
        '{"resolution": {"error": "overloaded"}}',
        '{"result": null, "resolution": ["a"], "status": "ongoing"}',
    ]
    for text in rejected:
        stats, _ = _stats_for(text)
        assert stats == {"attempted": 1, "failed": 1}, (
            f"{text} was accepted as an answer")

    accepted = [
        # The mandated shape. Legitimate even all-null: build_outcome_messages
        # passes only the last 10 chair speeches, so the 附帯決議 may sit outside
        # the window the model was shown.
        '{"result": null, "resolution": null, "status": "ongoing"}',
        # Off-shape, but it carries what the request was for — discarding this
        # would lose a real answer.
        '{"resolution": "附帯決議の要旨"}',
        # A wording near-miss on the result enum is still a real answer.
        # Rejecting it would discard it AND red the morning.
        '{"result": "可決すべきもの", "resolution": "要旨", "status": "resolved"}',
    ]
    for text in accepted:
        stats, outcome = _stats_for(text)
        assert stats == {"attempted": 1, "failed": 0}, (
            f"{text} was counted as a failure — that reds the run on a response "
            f"the model was entitled to give")
        # The pattern-matched resolution survives either way.
        assert outcome.get("resolution")


def test_the_annotation_does_not_send_an_operator_after_a_summary_request(
        fake_client, tmp_path, monkeypatch, capsys):
    """The standing phrase is "produced no usable summary". For a procedural
    meeting with a 附帯決議 no summary request is ever sent, so on its own that
    phrase names a failure that did not happen — the cost this repo measures in
    mornings. The annotation has to say which meetings those were.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(fake_client.messages, "create", _raise(_overloaded()))
    _run_pipeline(tmp_path, fake_client, monkeypatch,
                  [_procedural_meeting_with_a_resolution("R1")], batch=True)

    errors = [ln for ln in capsys.readouterr().out.splitlines()
              if ln.startswith("::error::")]
    assert errors, "the outage said nothing at all"
    assert "sent no summary request at all" in errors[0]
    assert "R1" in errors[0], "the operator is not told which meeting"


RUNTIME_REQUIREMENTS = "requirements.txt"
DEV_REQUIREMENTS = "requirements-dev.txt"

# Import name -> the distribution that provides it. Only the ones that differ;
# everything else is assumed to match, and an unmapped import that is not
# declared is a RED rather than a pass — a new dependency must be either pinned
# or taught to this map, and the direction that costs a human one line is the
# cheap one (#80/#81).
IMPORT_TO_DISTRIBUTION = {
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "fitz": "pymupdf",
    "yaml": "pyyaml",
}


PIP_INSTALL_RE = __import__("re").compile(
    r"\b(?:pip3?|python3?\s+-m\s+pip)\s+install\b")


def _pip_installs(script):
    """Every `pip install` in a shell script, one per shell command.

    Split on the operators that start a new command, because a whole line was
    the unit before and that is not what runs: `echo preparing && pip install
    requests` was skipped entirely on the strength of its FIRST token, while
    installing an unpinned package. Backslash continuations are joined first,
    for the neighbouring reason — a package on the next line was never looked
    at.

    A comment strips the rest of the line, and a segment whose own first token
    is `echo`/`printf`/`:` is prose about pip rather than pip: a step that
    comments the real line out and installs from a shell script must not
    satisfy the fence with a command nothing runs.
    """
    import re

    for line in script.replace("\\\n", " ").splitlines():
        line = line.split("#", 1)[0]
        for segment in re.split(r"&&|\|\||[;|]", line):
            segment = segment.strip()
            if not segment or not PIP_INSTALL_RE.search(segment):
                continue
            if segment.split()[0] in ("echo", "printf", ":"):
                continue
            yield segment


def _assert_installs_only_from_our_requirements(where, cmd, ours):
    """`cmd` may install from our requirements files and do nothing else.

    An ALLOWLIST, not a forbid-list. The earlier version kept anything that
    looked like a package name (`t[:1].isalpha()`) and let everything else
    through, so `pip install -r requirements.txt $EXTRA` passed — `$` is not
    alphabetic. What may legitimately appear here is a short, knowable set;
    anything else is a red a human resolves. Guards fail closed.
    """
    tokens = cmd.replace("'", " ").replace('"', " ").split()
    after = tokens[tokens.index("install") + 1:]
    flags = {"-r", "--requirement"}
    assert flags & set(after), (
        f"{where} installs Python packages by name (`{cmd}`) instead of from "
        f"{sorted(ours)} — a name here is a version nothing pins, which is the "
        f"pre-#80 state this fence exists to keep out")
    assert set(after) & ours, (
        f"{where} installs from a requirements file this fence does not know "
        f"(`{cmd}`) — teach it that file rather than leaving the pins "
        f"unchecked")
    extra = [t for t in after if t not in flags and t not in ours]
    assert not extra, (
        f"{where} puts {extra} on a requirements install (`{cmd}`) — a package "
        f"name is a version nothing pins, and anything the shell expands is a "
        f"version this fence cannot even read. Put it in "
        f"{RUNTIME_REQUIREMENTS} instead, where something pins it")


def _requirement_lines(rel):
    """The requirement and `-r` lines of one requirements file.

    Comments and blanks dropped; an inline `# ...` trimmed. Nothing clever:
    these files are ours and stay simple, and a parser that quietly ignores a
    line it cannot read is how a pin stops being checked.
    """
    path = REPO_ROOT / rel
    assert path.exists(), (
        f"{rel} is gone — versions were moved somewhere this fence cannot "
        f"read. Re-point it, do not drop it (#80)")
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _declarations():
    """Every package declaration across both requirements files.

    Returns `[(file, raw line, Requirement)]`. A line that does not parse as a
    requirement is a red here rather than a skip: silently ignoring it is how a
    pin stops being checked.
    """
    from packaging.requirements import InvalidRequirement, Requirement

    # The ONE option line this fence understands. Skipping every `-`-prefixed
    # line was the earlier shape, and it let `-r extra.txt` or `-e git+...`
    # smuggle a whole unpinned dependency set past everything below — the
    # option line is not the exception to the pin rule, it is a way around it.
    allowed_options = {DEV_REQUIREMENTS: f"-r {RUNTIME_REQUIREMENTS}"}

    out = []
    for rel in (RUNTIME_REQUIREMENTS, DEV_REQUIREMENTS):
        for line in _requirement_lines(rel):
            if line.startswith("-"):
                assert line == allowed_options.get(rel), (
                    f"{rel} carries an option line this fence does not know "
                    f"(`{line}`) — an include or an editable install brings in "
                    f"versions nothing here pins (#80)")
                continue
            try:
                out.append((rel, line, Requirement(line)))
            except InvalidRequirement as exc:
                raise AssertionError(
                    f"{rel} has a line this fence cannot parse (`{line}`: "
                    f"{exc}) — an unreadable line is an unchecked pin (#80)")
    return out


def test_every_dependency_is_pinned_exactly_and_declared_once():
    """#80's actual rule, applied to every package rather than to `anthropic`.

    The ceiling test below is about one coupling. This is about the property the
    requirements files exist for: a version that cannot change without a commit.
    Without it the fence approves `requests>=2` or a bare `beautifulsoup4` —
    every morning re-resolves it, and the next `anthropic` 1.0.0 arrives through
    whichever package was left loose. Guards here fail closed (a forbid-list
    that does not recognise a construct waves it through), so this demands the
    one shape that is safe rather than listing the shapes that are not:

    * exactly one clause, and it is `==`
    * no wildcard (`==2.*` re-resolves inside the major, which is the whole bug)
    * no environment marker gating it away on the runner
    * declared in exactly one of the two files — two declarations is how CI and
      the morning run drift onto different versions, which is what #80 was
    """
    from packaging.utils import canonicalize_name

    seen = {}
    for rel, line, req in _declarations():
        name = canonicalize_name(req.name)
        assert name not in seen, (
            f"{name} is declared twice ({seen.get(name)} and `{line}` in "
            f"{rel}) — two declarations are how CI and the morning run drift "
            f"onto different versions, which is what #80 was")
        seen[name] = f"`{line}` in {rel}"

        clauses = list(req.specifier)
        assert len(clauses) == 1 and clauses[0].operator == "==", (
            f"{rel} does not pin {name} to one exact version (`{line}`) — a "
            f"range re-resolves every morning, so production changes without a "
            f"commit (#80)")
        assert not clauses[0].version.endswith(".*"), (
            f"{rel} pins {name} with a wildcard (`{line}`), which still "
            f"re-resolves inside that range every morning (#80)")
        assert not req.marker, (
            f"{rel} gates {name} behind an environment marker (`{line}`) — the "
            f"fence cannot tell whether the runner installs it at all")
        assert not req.extras, (
            f"{rel} declares {name} with extras (`{line}`), which pull in "
            f"packages nothing here pins — `anthropic[httpx2]` is the shape "
            f"that matters, and it must be a decision with a version behind "
            f"it, not a bracket (#80)")


def test_every_third_party_import_is_declared():
    """#81, as a property rather than a line in a file.

    `httpx` was installed only because `anthropic` 0.x happened to depend on it,
    and #80 is what that costs: someone else's dependency change broke an import
    of ours. Declaring it fixed that ONE module. This is the general rule —
    anything `scripts/` imports and does not ship is ours to declare — and
    without it, deleting the `httpx` line goes green until the next upstream
    change re-opens #80 in exactly the same shape.

    Test-only imports may live in either file, since dev includes runtime.
    Everything the pipeline itself imports must be in requirements.txt: CI
    installing it is not the morning run installing it.
    """
    import ast
    import sys

    from packaging.utils import canonicalize_name

    scripts = REPO_ROOT / "scripts"
    # TOP LEVEL only, on both import paths that exist here: `scripts/` itself
    # (how the scripts run) and `scripts/tests/` (how pytest runs them). A
    # recursive sweep of every `*.py` at any depth was the earlier shape and it
    # is far too generous — one file named `scripts/sources/requests.py` would
    # make every `import requests` in the pipeline look local, while at runtime
    # it still resolves to the real distribution.
    local = set()
    for directory in (scripts, scripts / "tests"):
        for entry in directory.iterdir():
            if entry.suffix == ".py":
                local.add(entry.stem)
            elif entry.is_dir() and (entry / "__init__.py").exists():
                local.add(entry.name)

    declared = {}
    for rel, _line, req in _declarations():
        declared[canonicalize_name(req.name)] = rel

    for path in sorted(scripts.rglob("*.py")):
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        is_test = "tests/" in rel_path
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [(node.module or "").split(".")[0]]
            # The literal-string forms of a dynamic import. Not every dynamic
            # import is reachable this way — a name built at runtime is not —
            # but `importlib.import_module("x")` is the shape someone reaches
            # for to import an optional dependency, and it is precisely the one
            # that must still be declared.
            elif (isinstance(node, ast.Call)
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and (getattr(node.func, "id", None) == "__import__"
                         or getattr(node.func, "attr", None) == "import_module")):
                modules = [node.args[0].value.split(".")[0]]
            else:
                continue
            for module in modules:
                if (not module or module == "__future__"
                        or module in sys.stdlib_module_names
                        or module in local):
                    continue
                dist = canonicalize_name(
                    IMPORT_TO_DISTRIBUTION.get(module, module))
                where = declared.get(dist)
                assert where, (
                    f"{rel_path} imports `{module}` but nothing declares "
                    f"`{dist}` — an undeclared import is installed only because "
                    f"something else happens to depend on it, which is #81 and "
                    f"then #80. Pin it, or teach IMPORT_TO_DISTRIBUTION its "
                    f"real distribution name")
                assert is_test or where == RUNTIME_REQUIREMENTS, (
                    f"{rel_path} runs in the pipeline and imports `{module}`, "
                    f"but `{dist}` is declared only in {where} — CI would have "
                    f"it and the morning run would not")


def test_the_docs_do_not_teach_an_unpinned_install():
    """CLAUDE.md is a declaration site in the sense that matters.

    The old fence required it to carry the same constraint as the workflows,
    because "a doc that drifts to an unpinned command is how the pin gets
    removed by someone following instructions". The version string moved into
    requirements.txt; that reason did not move anywhere. Property 3 sweeps
    `.github/workflows/**` only, so without this a doc can tell a human to run
    the pre-#80 command and every test stays green.
    """
    import re

    ours = {RUNTIME_REQUIREMENTS, DEV_REQUIREMENTS}

    def _commands(text):
        """Every `pip install` a reader could copy: fenced block lines and
        inline code spans, split into shell commands by the shared helper.

        Prose ABOUT pip is not a command, and the difference is whether it
        carries arguments — this file's own rule is written as "do not add a
        package to the `pip install` line", which names the command and
        instructs the opposite. Failing on that would be a fence that fires on
        the sentence forbidding the thing it guards, and an alarm that cries
        wrong gets switched off.
        """
        fence = None
        for line in text.splitlines():
            marker = line.lstrip()[:3]
            if marker in ("```", "~~~"):
                if fence is None:
                    fence = marker
                elif fence == marker:
                    fence = None
                continue
            spans = [line] if fence else re.findall(r"`([^`]+)`", line)
            for span in spans:
                for cmd in _pip_installs(span):
                    if cmd[cmd.index("install") + len("install"):].strip():
                        yield cmd

    for rel in ("CLAUDE.md",):
        found = list(_commands((REPO_ROOT / rel).read_text(encoding="utf-8")))
        assert found, (
            f"{rel} no longer shows how to install the pipeline's Python "
            f"dependencies — the instruction moved somewhere this fence cannot "
            f"read it, which is how it drifts back to naming packages (#80)")
        for command in found:
            # The same allowlist the workflows get. Asking only "does `-r
            # requirements-dev.txt` appear" passed `pip install requests -r
            # requirements-dev.txt`, which installs an unpinned package and
            # reads, to a human copying it, as the approved command.
            _assert_installs_only_from_our_requirements(rel, command, ours)


# Each entry mutates a COPY of the repo's fence inputs in one way that #80/#81
# would come back through. Every one of them was a shape some version of this
# fence approved — most of them at the same time, because a forbid-list waves
# through what it does not recognise, and every one of these was found by
# someone reading the fence adversarially rather than by the fence itself.
def _drop(path, prefix):
    path.write_text("\n".join(l for l in path.read_text().splitlines()
                              if not l.startswith(prefix)))


def _sub(path, old, new):
    path.write_text(path.read_text().replace(old, new))


FENCE_TESTS = (
    "test_every_dependency_is_pinned_exactly_and_declared_once",
    "test_every_third_party_import_is_declared",
    "test_the_docs_do_not_teach_an_unpinned_install",
    "test_the_anthropic_pin_matches_what_the_summary_layer_imports",
)

_PINNED = "test_every_dependency_is_pinned_exactly_and_declared_once"
_IMPORTS = "test_every_third_party_import_is_declared"
_DOCS = "test_the_docs_do_not_teach_an_unpinned_install"
_ANTHROPIC = "test_the_anthropic_pin_matches_what_the_summary_layer_imports"

# Each entry names the test that MUST reject it. Asserting only that *something*
# rejected is not enough and the difference is not theoretical: several of these
# shapes are caught by two fences at once, so a property could stop working and
# the suite would stay green on its neighbour's rejection. `_ANTHROPIC` in
# particular carries four independent properties in one test, and three of them
# used to have no case that violated them ALONE.
FENCE_EVASIONS = {
    "a shell-expanded extra on the install line":
        (lambda r: _sub(r / ".github/workflows/ci.yml",
                        "pip install -r requirements-dev.txt",
                        "pip install -r requirements-dev.txt $EXTRA"), _ANTHROPIC),
    "a package on a backslash continuation line":
        (lambda r: _sub(r / ".github/workflows/ci.yml",
                        "      - run: pip install -r requirements-dev.txt",
                        "      - run: |\n"
                        "          pip install -r requirements-dev.txt \\\n"
                        "            anthropic"), _ANTHROPIC),
    "a second install hidden behind && after an echo":
        (lambda r: _sub(r / ".github/workflows/ci.yml",
                        "      - run: pip install -r requirements-dev.txt",
                        "      - run: echo installing && pip install requests"),
         _ANTHROPIC),
    "a dependency loosened from == to a range":
        (lambda r: _sub(r / "requirements.txt", "requests==2.34.2",
                        "requests>=2"), _PINNED),
    "a dependency left completely unpinned":
        (lambda r: _sub(r / "requirements.txt",
                        "beautifulsoup4==4.15.0", "beautifulsoup4"), _PINNED),
    "a wildcard pin":
        (lambda r: _sub(r / "requirements.txt", "pymupdf==1.28.2",
                        "pymupdf==1.28.*"), _PINNED),
    "extras that pull in unpinned packages":
        (lambda r: _sub(r / "requirements.txt",
                        "anthropic==0.125.0", "anthropic[bedrock]==0.125.0"),
         _PINNED),
    "an environment marker gating a pin away":
        (lambda r: _sub(r / "requirements.txt", "requests==2.34.2",
                        'requests==2.34.2; python_version < "3.0"'), _PINNED),
    "an extra include bringing in an unchecked file":
        (lambda r: _sub(r / "requirements.txt", "anthropic==0.125.0",
                        "-r extra.txt\nanthropic==0.125.0"), _PINNED),
    "the same package declared in both files":
        (lambda r: _sub(r / "requirements-dev.txt", "pytest==9.1.1",
                        "pytest==9.1.1\nrequests==2.34.2"), _PINNED),
    "the httpx declaration #81 asked for, deleted":
        (lambda r: _drop(r / "requirements.txt", "httpx=="), _IMPORTS),
    "a runtime dependency demoted to dev-only":
        (lambda r: (_drop(r / "requirements.txt", "requests=="),
                    _sub(r / "requirements-dev.txt", "pytest==9.1.1",
                         "pytest==9.1.1\nrequests==2.34.2")), _IMPORTS),
    # Property 1 of _ANTHROPIC, alone: still pinned exactly, still declared once,
    # still installed only from requirements files — only the ceiling is gone.
    "anthropic bumped into the httpx2 era":
        (lambda r: _sub(r / "requirements.txt",
                        "anthropic==0.125.0", "anthropic==1.4.0"), _ANTHROPIC),
    # Property 2 of _ANTHROPIC, alone. `_PINNED` also rejects a package declared
    # twice, which is why this case exists at all: without it, property 2 could
    # be deleted and its neighbour would keep the suite green.
    "anthropic declared in both requirements files":
        (lambda r: _sub(r / "requirements-dev.txt", "pytest==9.1.1",
                        "anthropic==0.125.0\npytest==9.1.1"), _ANTHROPIC),
    # Property 4 of _ANTHROPIC, alone, and the case that was missing entirely.
    # Dropping the `-r` line leaves every remaining declaration exactly pinned
    # and declared once, so NO fence rejected it: CI would resolve its own
    # anthropic again, which is the precise shape of #80, and the mutation suite
    # said the fence was complete.
    "requirements-dev.txt no longer includes the runtime set":
        (lambda r: _drop(r / "requirements-dev.txt", "-r requirements.txt"),
         _ANTHROPIC),
    "CLAUDE.md drifted back to naming packages":
        (lambda r: _sub(r / "CLAUDE.md", "`pip install -r requirements-dev.txt`",
                        "`pip install anthropic python-dotenv pytest`"), _DOCS),
    "CLAUDE.md naming a package alongside the approved -r":
        (lambda r: _sub(r / "CLAUDE.md", "`pip install -r requirements-dev.txt`",
                        "`pip install requests -r requirements-dev.txt`"), _DOCS),
}


def _fence_inputs_copied_to(tmp_path):
    """A throwaway repo holding everything the four fence tests read."""
    import shutil

    root = tmp_path / "repo"
    (root / ".github").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / ".github/workflows", root / ".github/workflows")
    shutil.copytree(
        REPO_ROOT / "scripts", root / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    for name in ("requirements.txt", "requirements-dev.txt", "CLAUDE.md"):
        shutil.copy(REPO_ROOT / name, root / name)
    return root


def _run_fences_against(root, monkeypatch):
    """`{test name: first line of its complaint}` for the fences that reject."""
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "REPO_ROOT", root)
    rejected = {}
    for name in FENCE_TESTS:
        try:
            getattr(module, name)()
        except AssertionError as exc:
            rejected[name] = str(exc).splitlines()[0]
    return rejected


def test_the_fence_passes_an_untouched_copy_of_this_repo(tmp_path, monkeypatch):
    """The control. Without it every case below could be passing because the
    copied tree is broken in some way of its own, and the suite would report a
    fence that rejects everything as a fence that works."""
    rejected = _run_fences_against(_fence_inputs_copied_to(tmp_path), monkeypatch)
    assert not rejected, rejected


@pytest.mark.parametrize("evasion", sorted(FENCE_EVASIONS))
def test_the_fence_rejects_each_way_80_could_come_back(evasion, tmp_path,
                                                       monkeypatch):
    """Proving a guard REJECTS is the only half that matters.

    A fence is only ever exercised here in the direction that passes, so
    "380 passed" says nothing about whether it can still say no — and every
    hole listed above was live while the suite was green. This is the repo's
    own rule about fail-closed guards applied to the fence itself: break it on
    purpose, and check that it breaks.

    It checks WHICH fence broke, not merely that one did. Several of these
    shapes trip two fences, so "something rejected" lets the property this case
    was written for rot behind a neighbour's rejection — a mutation suite
    reporting a coverage it no longer has, which is the failure mode it exists
    to catch one level down.
    """
    root = _fence_inputs_copied_to(tmp_path)
    mutate, expected = FENCE_EVASIONS[evasion]
    mutate(root)
    rejected = _run_fences_against(root, monkeypatch)
    assert rejected, (
        f"the fence APPROVED `{evasion}` — that shape reaches production with "
        f"a version nothing pins, which is #80")
    assert expected in rejected, (
        f"`{evasion}` was rejected, but by {sorted(rejected)} rather than by "
        f"{expected} — the property this case was written to exercise is no "
        f"longer doing it, and the suite is green on a neighbour's rejection")


def test_the_anthropic_pin_matches_what_the_summary_layer_imports():
    """#80. Production used to change without a commit.

    Both workflows installed bare package names, so every morning re-resolved
    the latest release of everything. On 2026-08-20 `anthropic` 1.0.0 moved the
    HTTP layer to the `httpx2` fork, `httpx` stopped being installed, and
    `summarizer.sync_call_kwargs` — which imports `httpx` DIRECTLY to build the
    timeout that stops the SDK refusing a non-streaming request before it sends
    it (#46) — took every meeting down for three mornings.

    Versions now live only in requirements.txt / requirements-dev.txt, so this
    fence guards four things. Each is a distinct way the outage comes back, and
    none of them implies another:

    1. **The ceiling.** While anything under `scripts/` imports bare `httpx`,
       the declared `anthropic` must exclude every release at or above 1.0.
       This is on the COUPLING, not on a version string: swap those imports to
       `httpx2` and the demand lifts by itself. A test that just asserted `<1`
       forever would have to be deleted by the very commit that makes the pin
       unnecessary, and a fence you delete to make progress is one you
       eventually delete carelessly.
    2. **One declaration.** `anthropic` may be named in exactly one requirements
       file. Two files declaring it is how CI and production drift apart again.
    3. **No package names in the workflows.** A name on a `pip install` line is
       a version nothing pins, which is precisely the pre-#80 state — so the
       workflows must install from a requirements file and nothing else.
    4. **The dev set contains the runtime set.** requirements-dev.txt must
       `-r requirements.txt`. Without that, CI resolves its own `anthropic` and
       there is no longer any moment at which one side could catch what the
       other is about to install — the gap the outage happened in.

    `_admits_nothing_from` is written the awkward way on purpose. The first
    version of this fence asked `SpecifierSet.contains(Version("1.0.0"))` — one
    point — while its message claimed to fence "the majors". `>=1.0.1`,
    `>=1.4,<2`, `!=1.0.0` and `>=2` all sailed through: it caught a pin deleted
    outright, not a pin *moved* into the httpx2 era, which is the likelier way
    this recurs (the #80 migration is a bump). A probe proves something about
    the versions it happened to try; an upper bound proves something about all
    of them.
    """
    import ast
    import re

    # NOT importorskip. Every other yaml-reading test in this file skips when
    # pyyaml is absent, and CLAUDE.md already says a skip there means "not
    # measured" rather than "passed". This one guards a production outage, so it
    # fails instead: an unrecognised environment must not retire it quietly.
    import yaml
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import InvalidVersion, Version

    RUNTIME = "requirements.txt"
    DEV = "requirements-dev.txt"
    # The first major that dropped httpx. Must be a major boundary (`X.0`).
    HTTPX2_MAJOR = Version("1.0")
    PIP_INSTALL = re.compile(r"\b(?:pip3?|python3?\s+-m\s+pip)\s+install\b")
    # Every Python installer this fence knows how to recognise. Broader than
    # `pip install` on purpose: the aggregated version of this check was
    # fail-open, and verified so — with one correct `-r` install present,
    # `poetry add anthropic` and `uv add anthropic` in a second step both
    # passed. (`uv pip install` happened to be caught, because the regex sees
    # the `pip install` inside it, which is exactly why one passing example is
    # not evidence about the rule.)
    #
    # The residual is real and stated rather than papered over: an installer
    # not on this list, or one inside a shell script the workflow calls, is not
    # seen. What keeps that from being silent is the whole-file check below —
    # a workflow with no readable install at all cannot mention the package.
    # Two contracts, because these commands are not the same kind of thing.
    #
    # ADD-style resolves a NEW version at run time — the pre-#80 state whatever
    # file it writes afterwards — so it is forbidden outright. PIP-style must
    # name a requirements file. LOCK-style builds from a lock file and never
    # takes `-r`, so demanding one would REJECT a correctly pinned install; the
    # previous version did exactly that, which would have blocked #93 the moment
    # it landed. Those are allowed, on the condition that the lock they build
    # from is actually tracked in the repo.
    #
    # The residual, stated rather than implied: an installer on none of these
    # lists, or one inside a shell script the workflow calls, is not seen. The
    # whole-file check further down only catches that when the workflow has NO
    # readable installer at all — with one present, an unknown one gets through.
    # That is a real hole, and the honest place for it is this comment.
    ADD_STYLE = re.compile(r"\b(?:uv|poetry|pdm|rye)\s+add\b")
    PIP_STYLE = re.compile(
        r"\b(?:pip3?\s+install|python3?\s+-m\s+pip\s+install"
        r"|uv\s+pip\s+install|easy_install)\b")
    LOCK_STYLE = {
        re.compile(r"\buv\s+sync\b"): "uv.lock",
        re.compile(r"\bpoetry\s+install\b"): "poetry.lock",
        re.compile(r"\bpipenv\s+(?:install|sync)\b"): "Pipfile.lock",
        re.compile(r"\bpdm\s+(?:install|sync)\b"): "pdm.lock",
        re.compile(r"\brye\s+sync\b"): "requirements.lock",
        re.compile(r"\b(?:conda|mamba)\s+install\b"): None,
    }

    def _admits_nothing_from(spec, boundary):
        """Does `spec` exclude every release at or above `boundary`?

        Answered by looking for a clause that caps below the boundary, never by
        sampling versions — see the docstring above. Anything unrecognised
        counts as *no* cap, so an exotic-but-valid spelling gets a red a human
        resolves. That is the cheap direction to be wrong in; the other one
        published zero threads for three days.
        """
        for clause in spec:
            op, raw = clause.operator, clause.version
            if op == "==" and raw.endswith(".*"):
                # `==0.*` tops out inside major 0, same as `~=0`.
                op, raw = "~=", raw[: -len(".*")]
            try:
                pinned = Version(raw)
            except InvalidVersion:
                continue
            if pinned.epoch != boundary.epoch:
                # `~=1!0.72` has release[0] == 0 and would read as a cap under
                # major 1, while admitting 1!0.99 — which sorts ABOVE every
                # version the boundary is about. Epochs are unrecognised here,
                # and unrecognised means no cap.
                continue
            if op == "<" and pinned <= boundary:
                return True
            if op in ("<=", "==", "===") and pinned < boundary:
                return True
            if op == "~=" and pinned.release[0] < boundary.release[0]:
                return True
        return False

    # ---- 1 + 2: the ceiling, declared exactly once ------------------------
    #
    # Bare `import httpx` / `from httpx import ...` only. `httpx2` (and an
    # `import httpx2 as httpx` alias) is the post-migration shape and must not
    # keep the pin alive.
    importers = []
    for path in sorted((REPO_ROOT / "scripts").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n == "httpx" or n.startswith("httpx.") for n in names):
                importers.append(path.relative_to(REPO_ROOT).as_posix())
                break

    declarations = []                      # (file, raw line, Requirement)
    for rel in (RUNTIME, DEV):
        for line in _requirement_lines(rel):
            if line.startswith("-"):       # `-r requirements.txt` and friends
                continue
            try:
                req = Requirement(line)
            except InvalidRequirement:
                continue
            if canonicalize_name(req.name) == "anthropic":
                declarations.append((rel, line, req))

    assert declarations, (
        f"neither {RUNTIME} nor {DEV} declares anthropic — the version moved "
        f"somewhere this fence cannot read; re-point it, do not drop it")
    assert len(declarations) == 1, (
        f"anthropic is declared in more than one place ("
        f"{[(f, line) for f, line, _ in declarations]}) — two declarations are "
        f"how CI and the morning run drift onto different SDKs, which is what "
        f"#80 was")

    where, line, req = declarations[0]
    for importer in importers:
        assert _admits_nothing_from(req.specifier, HTTPX2_MAJOR), (
            f"{where} allows anthropic {HTTPX2_MAJOR} or later (`{line}`) while "
            f"{importer} imports bare `httpx`, which those majors do not "
            f"install — this is the shape that published zero threads for "
            f"three days")

    # ---- 3: the workflows install from a file, never by name --------------
    #
    # What this sweep can see is a literal `pip install` inside a step's `run`.
    # It cannot see one built from a matrix or an env var, one inside a
    # composite action, or one in a shell script the workflow calls. The
    # `anthropic`-mention check below is what stops such a shape passing
    # unexamined rather than silently.
    ours = {RUNTIME, DEV}
    for path in sorted((REPO_ROOT / ".github" / "workflows").iterdir()):
        if path.suffix not in (".yml", ".yaml"):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        installs = []
        for job in (yaml.safe_load(text) or {}).get("jobs", {}).values():
            for step in job.get("steps") or []:
                installs.extend(_pip_installs(str(step.get("run", ""))))
        for cmd in installs:
            _assert_installs_only_from_our_requirements(rel, cmd, ours)
        # Per shell SEGMENT, not per line. Skipping a line whose first token was
        # `echo`/`printf` was a verified fail-open: `echo preparing && poetry
        # add anthropic` and `printf x; pip install anthropic` both passed.
        # Split on `&&`, `||` and `;` so each command answers for itself.
        #
        # Keyed off the installer VERB, not the package name. Keying off the
        # name looked stricter and was wrong in both directions: it tripped on
        # the failure-issue body, which says "Anthropic credit exhaustion" about
        # the company, and it would still have missed `poetry add requests`.
        tracked = {q.name for q in REPO_ROOT.iterdir()}
        for raw in text.splitlines():
            for segment in re.split(r"&&|\|\||;", raw.split("#", 1)[0]):
                probe = segment.strip()
                if not probe:
                    continue
                assert not ADD_STYLE.search(probe), (
                    f"{rel} resolves a new version at run time (`{probe}`) — "
                    f"that is the pre-#80 state whatever file it writes after")
                if PIP_STYLE.search(probe):
                    assert {"-r", "--requirement"} & set(probe.split()), (
                        f"{rel} installs Python packages without a requirements "
                        f"file (`{probe}`) — a name on an install line is a "
                        f"version nothing pins, the pre-#80 state")
                for pattern, lock in LOCK_STYLE.items():
                    if not pattern.search(probe):
                        continue
                    assert lock is not None, (
                        f"{rel} installs with a resolver this fence cannot pin "
                        f"(`{probe}`) — conda/mamba resolve at run time unless "
                        f"an explicit lock is used; make it readable here first")
                    assert lock in tracked, (
                        f"{rel} builds the environment from {lock} (`{probe}`) "
                        f"but that file is not in the repo — nothing pins what "
                        f"it installs")

        # The coarse net, for a workflow this sweep cannot read at all: it may
        # not so much as name the package. Kept as well as the per-line rule
        # above, not instead of it — the two miss different things.
        live = "\n".join(
            l.split("#", 1)[0] for l in text.splitlines()).lower()
        assert installs or "anthropic" not in live.replace(
                "anthropic_api_key", ""), (
            f"{rel} names anthropic outside a comment but runs no install this "
            f"fence can read — teach the sweep that shape rather than leaving "
            f"it unchecked")

    # ---- 4: the dev set contains the runtime set --------------------------
    includes = [l for l in _requirement_lines(DEV)
                if re.match(r"^(-r|--requirement)\b", l)]
    assert any(l.split(maxsplit=1)[-1].strip() == RUNTIME for l in includes), (
        f"{DEV} no longer includes `-r {RUNTIME}` — CI would resolve its own "
        f"dependency set again, and #80 happened in exactly that gap: neither "
        f"side could ever catch what the other was about to install")

    # ---- the other direction: stale prose once the migration lands -------
    #
    # `httpx(?!2)` and not `httpx`, or the correct post-migration sentence
    # ("the SDK uses httpx2") would itself read as the stale one.
    if not importers:
        bare = re.compile(r"httpx(?!2)")
        named_sites = [RUNTIME, DEV, "CLAUDE.md",
                       ".github/workflows/daily-batch.yml",
                       ".github/workflows/ci.yml"]
        stale = [rel for rel in named_sites
                 if bare.search((REPO_ROOT / rel).read_text(encoding="utf-8"))]
        assert not stale, (
            f"nothing imports bare httpx any more, but {stale} still explain "
            f"the anthropic pin by it — lift the pin, or say there what the "
            f"new reason is and re-point this branch (#80)")


def test_the_workflow_enriches_member_links_after_the_validator_adds_members():
    """validate-data.mjs --fix adds members that appear only in threads, with
    no links and no `id`. Enriching before it therefore commits members with
    zero links on the very first morning — which is the state this whole
    change exists to end. Order, not presence, is the contract.

    No `|| true` either: enrich-news.py carries one because a missing news
    article must not stop the publish, and copying it here would restore the
    silent drift (nobody ran the enricher for months and nothing noticed).
    """
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "daily-batch.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["fetch-and-summarize"]["steps"]
    runs = [(s.get("name", ""), s.get("run", "") or "", s) for s in steps]

    def index_of(needle):
        for i, (_, run, _) in enumerate(runs):
            if needle in run:
                return i
        raise AssertionError(f"no step runs {needle!r}")

    validate_at = index_of("scripts/validate-data.mjs --fix")
    enrich_at = index_of("scripts/enrich-members.mjs")
    feeds_at = index_of("scripts/generate-feeds.js")
    assert validate_at < enrich_at < feeds_at, (
        "enrich must run after the validator adds members and before the feeds "
        f"are generated (validate={validate_at}, enrich={enrich_at}, feeds={feeds_at})")

    _, enrich_run, enrich_step = runs[enrich_at]
    assert "|| true" not in enrich_run
    assert enrich_step.get("continue-on-error") is not True
    assert "if" not in enrich_step, "the enrich step must not be conditional"


def test_the_production_build_enriches_too():
    """package.json's `build` is the other definition of this pipeline, and it
    is the one Vercel runs. Without enrich there, the --fix in a production
    build can add a member and render the deploy with no link for them."""
    pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    build = pkg["scripts"]["build"]
    assert "scripts/enrich-members.mjs" in build
    assert build.index("validate-data.mjs") < build.index("enrich-members.mjs")


def test_ci_gives_the_python_tests_a_node_to_run():
    """test_member_links.py subprocesses the node CLI. Without setup-node the
    job leans on whatever node the runner image happens to ship — the same
    unpinned-dependency shape as #80."""
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["python-tests"]["steps"]
    node_steps = [s for s in steps if str(s.get("uses", "")).startswith("actions/setup-node@")]
    assert node_steps, f"python-tests has no setup-node: {[s.get('uses') for s in steps]}"
    # The version too, not just the action. Naming the action without pinning
    # the runtime is the #80 shape exactly: it resolves to whatever the runner
    # ships that week, and nobody wrote that number down.
    assert node_steps[0].get("with", {}).get("node-version") is not None, (
        "setup-node must pin node-version")
