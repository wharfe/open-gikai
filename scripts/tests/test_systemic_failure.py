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
        return [dict(_THREAD_INFO)] if summarize.has_question_for_the_api(meeting) else []

    monkeypatch.setattr(summarize, "group_meeting", _group)
    monkeypatch.setattr(summarize, "extract_meeting_outcome",
                        lambda c, m, model: {"result": None, "resolution": None,
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
        for m in meetings if summarize.has_question_for_the_api(m)
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
                        lambda c, m, model: {"result": None, "resolution": None,
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
    # this still leaves the step an implicit success(), so a Collect hard-fail
    # skips it — acceptable, since that already failed the job and the
    # annotations survive. `if: always()` is deliberately NOT the answer.
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
    """#59's acceptance condition: Summarize is SKIPPED on a pending morning,
    so a verdict that only travels through its outputs can never fail the job."""
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
    """It cannot: on a pending morning it does not run at all, and it never
    sees Collect's dates."""
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
    assert "steps.dates.outputs.list" in cond

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
    # Held or abandoned alone must be able to fail the run.
    assert 'if [ -z "$(echo "$FAIL_DATES$HELD$ABANDONED"' in run
    assert "Permanently lost" in run
    assert "held for a human decision" in run
