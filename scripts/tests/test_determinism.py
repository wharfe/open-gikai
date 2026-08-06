"""Guards for the summary layer's determinism + truncation invariants.

Three bugs motivated this file, and all three were the same shape: a rule that
was written down in CLAUDE.md and honored in one call site, while a sibling call
site quietly did the other thing.

  #47 — no call passed ``temperature``, so the "temperature=0, same input →
        same output" invariant ran at the API default. Measured: an identical
        re-request produced 8,527 then 9,603 output tokens.
  #51 — the fix for #47 was itself the outage: claude-sonnet-5 rejects a
        non-default ``temperature`` with a 400, so pinning it took every
        grouping, outcome and summary call in the 2026-08-05 run to zero.
  #48 — ``summarizer.py`` was raised off the truncating 8192 ceiling, but
        ``batch.py`` / ``bulk_batch.py`` — the scripts an operator reaches for
        when recovering from exactly that failure — kept it.
  (unnumbered) — those same two scripts had been dead on import for months,
        importing prompt constants that ``prompts.py`` stopped exporting. A
        source-reading test certified them as correct the whole time.

So the guards here come in two layers. The behavioral ones call the real
builders and assert on real values; the AST ones sweep every module for a
hand-rolled request that bypassed a builder. Neither alone is enough: the first
cannot see a new call site, and the second cannot see whether the file runs.
"""

import ast
import importlib
import pathlib

import pytest

from .conftest import SAMPLING_PARAMS
from pipeline import batch_state as bs
from pipeline.grouper import (
    GROUPING_MAX_TOKENS, GROUPING_RETRY_MAX_TOKENS, OUTCOME_MAX_TOKENS,
    build_grouping_request, build_outcome_request,
)
from pipeline.summarizer import (
    SUMMARY_MAX_TOKENS, SUMMARY_RETRY_MAX_TOKENS,
    build_summary_messages, build_summary_request,
)

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parents[1]

# Every module that builds a request whose output becomes a published summary.
# Auxiliary layers (news_ranker, etc.) are deliberately absent — they are
# allowed LLM freedom under CLAUDE.md's "What IS allowed".
SUMMARY_LAYER_MODULES = [
    SCRIPTS_DIR / "batch.py",
    SCRIPTS_DIR / "bulk_batch.py",
    SCRIPTS_DIR / "summarize.py",
    SCRIPTS_DIR / "pipeline" / "grouper.py",
    SCRIPTS_DIR / "pipeline" / "summarizer.py",
]

# The scripts that only ever run during a manual recovery, i.e. the ones whose
# breakage nothing else would surface.
RECOVERY_MODULES = ["batch", "bulk_batch"]

# The ceiling that truncated real threads. A 31-speech thread needed ~9.6k
# output tokens, and a truncated response is unparseable.
TRUNCATING_CEILING = 8192

# Sampling params. claude-sonnet-5 rejects any of these with a 400 (#51), so on
# the summary layer they are forbidden outright rather than pinned to a value.
# The check is presence-based on purpose: "temperature must equal 0" was the
# previous guard, and it certified the request that produced the outage.
# Imported from conftest, where the fake client raises the same 400, so the AST
# guard and the on-the-wire guard cannot drift apart.
FORBIDDEN_PARAMS = set(SAMPLING_PARAMS)

# What the summary layer can still control, now that sampling is not pinnable:
# thinking is switched off explicitly, because Sonnet 5 turns adaptive thinking
# ON when the param is omitted. Checked by VALUE everywhere, including in the
# AST sweep: presence alone would pass {"type": "enabled"}, which is the exact
# mistake the old temperature guard made in reverse.
REQUIRED_THINKING = {"type": "disabled"}

# The summary request's param set, per schema version. compute_input_hash covers
# every one of these except max_tokens, so a sidecar written under one version's
# set cannot be verified under another's — which is why the set and the version
# have to move together. See test_summary_request_param_set_is_pinned.
#
# History is kept, not just the current row, so the table shows what a bump was
# FOR. Note v2 and v4 carry the same set: v3 added temperature and v4 took it
# back out. So "an older version's hashes always cover a different param set" is
# not true of v2 — v4 rejects a v2 sidecar out of caution, not necessity.
PARAM_SET_BY_SCHEMA_VERSION = {
    2: ["max_tokens", "messages", "model", "system", "thinking"],
    3: ["max_tokens", "messages", "model", "system", "temperature", "thinking"],
    4: ["max_tokens", "messages", "model", "system", "thinking"],
}

# The grouping/outcome builders' param set. Not schema-versioned — their output
# is not hashed into a sidecar — but pinned for the same reason: an allowlist
# catches the *next* param Claude 5 starts rejecting, which a denylist of the
# three known-bad names cannot.
BUILDER_PARAM_SET = ["max_tokens", "messages", "model", "system", "thinking"]

# Resolved from the modules under test, so an AST site written as a Name is
# checked by VALUE. Checking only for the literal 8192 is what made the original
# version of this test unfailable: after the constants landed, not one request
# site in the tree was a literal any more.
KNOWN_CEILINGS = {
    "GROUPING_MAX_TOKENS": GROUPING_MAX_TOKENS,
    "GROUPING_RETRY_MAX_TOKENS": GROUPING_RETRY_MAX_TOKENS,
    "OUTCOME_MAX_TOKENS": OUTCOME_MAX_TOKENS,
    "SUMMARY_MAX_TOKENS": SUMMARY_MAX_TOKENS,
}

# Sentinel for a site whose ceiling is a function parameter rather than a
# module constant — summarize_thread's inner _call takes it so the same code
# can issue both the first attempt and the higher retry.
LOCAL_CEILING = "<local>"

# Every place in the summary layer allowed to construct a request, mapped to the
# set of ceiling constants it may use.
#
# batch.py, bulk_batch.py and summarize.py appear with NO entries on purpose:
# they must not build requests at all any more, only call the builders. Adding
# a key here is a deliberate act — that is the point of the table.
#
# Be honest about one limit: summarize.py's repair path DOES reach the wire, at
# summarize.py's `messages.create(**params)`. It has no literal `model=` kwarg
# and no dict literal, so _request_sites cannot see it and an empty entry here
# is not proof it is clean. What covers it is behavioral, not syntactic: the
# params start as a copy of build_summary_request's output, and the fake client
# in conftest raises the real 400 on any sampling param, so the test_resume
# repair tests fail closed if one is ever added there.
EXPECTED_SITES = {
    "batch.py": {},
    "bulk_batch.py": {},
    "summarize.py": {},
    "grouper.py": {
        # Two sites: the first call, then the truncation retry one tier up.
        "group_meeting": {"GROUPING_MAX_TOKENS", "GROUPING_RETRY_MAX_TOKENS"},
        "build_grouping_request": {"GROUPING_RETRY_MAX_TOKENS"},
        "build_outcome_request": {"OUTCOME_MAX_TOKENS"},
        "extract_meeting_outcome": {"OUTCOME_MAX_TOKENS"},
    },
    "summarizer.py": {
        "summarize_thread": LOCAL_CEILING,
        "build_summary_request": {"SUMMARY_MAX_TOKENS"},
    },
}

MEETING = {
    "meetingId": "m1",
    "house": "参議院",
    "meeting": "外交防衛委員会",
    "date": "2026-05-14",
    "speeches": [
        {"speaker": "A", "speech": "そ" * 200, "speechOrder": 1},
        {"speaker": "B", "speech": "り" * 200, "speechOrder": 2},
    ],
}


def _voted_meeting() -> dict:
    """A meeting whose procedural text trips BOTH outcome patterns.

    The vote regex needs one of 原案のとおり / 修正議決 / 全会一致で before the
    可決 — an earlier version of this fixture said only "可決すべきものと決定"
    and so exercised the 附帯決議 branch alone, while asserting in its failure
    message that it covered the vote branch.
    """
    return dict(MEETING, speeches=[
        {"speaker": "委員長", "speakerRole": "委員長",
         "speech": "本案に賛成の諸君の起立を求めます。原案のとおり可決すべきものと"
                   "決定いたしました。附帯決議を付することに御異議ございませんか。"
                   + "な" * 60,
         "speechOrder": 1},
    ])


def _parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _dict_items(node: ast.Dict) -> dict:
    """Constant-keyed entries of a dict literal, as {key: value_node}."""
    return {
        k.value: v
        for k, v in zip(node.keys, node.values)
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }


def _literal(node):
    """Python value of a literal AST node, or a unique sentinel if it is not one.

    The sentinel matters: returning None for "not a literal" would make a site
    that omits the param and a site that writes ``thinking=None`` look the same
    as each other, and both would compare unequal to REQUIRED_THINKING only by
    luck. Anything unresolvable must FAIL the comparison, not pass it.
    """
    if node is None:
        return _UNRESOLVED
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return _UNRESOLVED


class _Unresolved:
    def __repr__(self):
        return "<unresolved>"


_UNRESOLVED = _Unresolved()


def _enclosing_functions(tree: ast.Module) -> dict:
    """Map every node in the tree to the name of the function containing it."""
    owner = {}
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(func):
                owner.setdefault(child, func.name)
    return owner


def _request_sites(tree: ast.Module):
    """Yield (lineno, function, description, params) for every request site.

    Matching is deliberately loose on the *shape* and strict on the response:
    anything that looks like it might be a request has to be declared in
    EXPECTED_SITES below. An earlier version keyed on a dict literal containing
    both "max_tokens" and "model", and a params dict that merely assigned
    ``params["max_tokens"]`` on the next line slipped past it with no
    temperature at all — so a summary-layer request must now be recognized from
    "model" plus any one of the other request keys.
    """
    owner = _enclosing_functions(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            items = _dict_items(node)
            if "model" in items and {"messages", "system", "max_tokens"} & set(items):
                yield node.lineno, owner.get(node), "request params dict", items
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "create":
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            # batches.create(requests=[...]) has no model of its own.
            if "model" in kwargs:
                yield node.lineno, owner.get(node), "messages.create()", kwargs


# ---------------------------------------------------------------------------
# Behavioral guards — call the real builders
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", RECOVERY_MODULES)
def test_recovery_scripts_import(name):
    """These two are only ever run by hand, so nothing else notices when they
    rot. They spent months raising ImportError at line 1 while the AST guards
    below reported them as compliant."""
    importlib.import_module(name)


def test_build_summary_request_carries_the_invariants():
    """The one request builder the daily pipeline actually submits through."""
    params = build_summary_request(
        meeting=MEETING,
        thread_info={"topic": "T"},
        speeches=[{"speaker": "A", "speech": "x", "speechOrder": 1}],
        custom_id="s_abc_00",
        model="claude-x",
    )["params"]

    assert not FORBIDDEN_PARAMS & set(params), (
        "summary requests must send no sampling params — claude-sonnet-5 "
        "answers one with a 400 (#51)"
    )
    assert params["max_tokens"] == SUMMARY_MAX_TOKENS
    assert params["thinking"] == REQUIRED_THINKING


def test_summary_request_param_set_is_pinned():
    """Adding a param to a summary request changes what compute_input_hash
    covers, so every sidecar written before the change stops verifying. That
    does not surface as a version error — is_current_schema still passes — it
    surfaces as a per-thread "input_hash mismatch — raw/prompt changed", which
    reads like corrupted raw data and burns the retry budget to permanent loss.

    Keyed off SCHEMA_VERSION so the version is in front of whoever edits the
    expected set, and so a bump with no recorded set fails outright.

    Be honest about the limit rather than overclaiming, because overclaiming a
    guard is how this file got here: a developer who changes the param set AND
    edits this table's current row still ships green. No local test can force a
    human to increment a constant. What it does buy is that the change cannot be
    made without reading the word SCHEMA_VERSION and the history below, and that
    the two halves of the bump cannot be done by halves.
    """
    params = build_summary_request(
        meeting=MEETING,
        thread_info={"topic": "T"},
        speeches=[{"speaker": "A", "speech": "x", "speechOrder": 1}],
        custom_id="s_abc_00",
        model="claude-x",
    )["params"]

    assert bs.SCHEMA_VERSION in PARAM_SET_BY_SCHEMA_VERSION, (
        f"batch_state.SCHEMA_VERSION is {bs.SCHEMA_VERSION} but no param set is "
        f"recorded for it — add the current set to PARAM_SET_BY_SCHEMA_VERSION"
    )
    assert sorted(params) == PARAM_SET_BY_SCHEMA_VERSION[bs.SCHEMA_VERSION], (
        "summary request param set changed — bump batch_state.SCHEMA_VERSION "
        "and record the new set in PARAM_SET_BY_SCHEMA_VERSION as a NEW row. "
        "Editing the current row in place is the one way to defeat this check, "
        "and it is exactly what leaves in-flight sidecars unverifiable."
    )


def test_summary_ceilings_clear_the_truncating_one():
    """#46/#48 come back if any of these is tuned back down, and no AST sweep
    can see that — the request sites would still read the same.

    SUMMARY_RETRY_MAX_TOKENS matters most and is the easiest to overlook: it is
    the ceiling the sidecar repair path re-issues a truncated summary at, i.e.
    the rescue that #46 exists for. Setting it to 8192 breaks nothing visible.
    """
    assert TRUNCATING_CEILING < SUMMARY_MAX_TOKENS < SUMMARY_RETRY_MAX_TOKENS
    assert TRUNCATING_CEILING < GROUPING_MAX_TOKENS < GROUPING_RETRY_MAX_TOKENS \
        or GROUPING_MAX_TOKENS == TRUNCATING_CEILING, (
            "the sync grouping call may sit at the truncating ceiling because it "
            "retries; its retry may not"
        )
    assert GROUPING_RETRY_MAX_TOKENS > TRUNCATING_CEILING


def test_batch_builders_pin_the_right_ceiling_to_the_right_request():
    """A set-of-names assertion cannot tell grouping's ceiling from outcome's,
    so swapping the two would pass it while grouping ran at 1024."""
    grouping = build_grouping_request(MEETING, "group_x", "claude-x")["params"]
    assert grouping["max_tokens"] == GROUPING_RETRY_MAX_TOKENS
    assert grouping["max_tokens"] > TRUNCATING_CEILING, (
        "batch mode has no in-band truncation retry, so it must submit at the "
        "ceiling the synchronous path would retry at"
    )
    assert not FORBIDDEN_PARAMS & set(grouping)
    assert grouping["thinking"] == REQUIRED_THINKING
    # Allowlist, not just the sampling denylist. FORBIDDEN_PARAMS names the
    # three params known to 400 today; an allowlist also catches the next one
    # the API starts rejecting, and the summary builder already had this
    # protection while grouping and outcome did not.
    assert sorted(grouping) == BUILDER_PARAM_SET, (
        "grouping request param set changed — every param here is sent to a "
        "Claude 5 model, so adding one is a whole-run risk, not a tweak"
    )

    voted = _voted_meeting()
    outcome = build_outcome_request(voted, "outcome_x", "claude-x")
    assert outcome is not None, "fixture no longer trips the vote pattern matcher"
    assert outcome["params"]["max_tokens"] == OUTCOME_MAX_TOKENS
    assert not FORBIDDEN_PARAMS & set(outcome["params"])
    assert outcome["params"]["thinking"] == REQUIRED_THINKING
    assert sorted(outcome["params"]) == BUILDER_PARAM_SET, (
        "outcome request param set changed — see the grouping assertion above"
    )


def test_batch_and_sync_grouping_send_identical_prompts():
    """The recovery path must reproduce the daily path's output, and a prompt is
    an input: batch.py used to assemble its own, which is how it drifted far
    enough to stop importing at all."""
    from pipeline.grouper import build_grouping_messages

    request = build_grouping_request(MEETING, "group_x", "claude-x")
    assert request["params"]["messages"] == build_grouping_messages(MEETING)


def test_batch_and_sync_summary_send_identical_prompts(fake_client):
    """The summary twin of the grouping check above. `summarize.py --batch` and
    `summarize.py` (sync) publish into the same data/threads/ files, so a
    divergence here means the same speech reads differently depending on which
    path happened to process it — invisible in the output, fatal to the claim.

    Compares what the sync path actually PUT ON THE WIRE against the batch
    request, rather than comparing the builder to itself: the failure mode is a
    path going back to assembling its own messages, which a builder-to-builder
    comparison cannot see.
    """
    from pipeline.summarizer import summarize_thread

    thread_info, speeches = {"topic": "T"}, MEETING["speeches"]
    fake_client.messages.create_text = '{"speeches": [], "commitments": []}'
    summarize_thread(fake_client, MEETING, thread_info, speeches, model="claude-x")

    sent = fake_client.messages.create_calls[0]
    batch = build_summary_request(MEETING, thread_info, speeches, "s_0", "claude-x")

    assert sent["messages"] == batch["params"]["messages"]
    assert sent["system"] == batch["params"]["system"]
    assert sent["thinking"] == batch["params"]["thinking"] == REQUIRED_THINKING
    # Checked against what the sync path actually put on the wire, which is the
    # only view that includes the **sync_call_kwargs() splat — an AST sweep
    # cannot see a sampling param that arrives through a helper's return value.
    assert not FORBIDDEN_PARAMS & set(sent), (
        f"sync summary call sends {sorted(FORBIDDEN_PARAMS & set(sent))} — "
        f"claude-sonnet-5 answers that with a 400 (#51)"
    )


def test_builders_return_none_when_there_is_nothing_to_ask():
    empty = dict(MEETING, speeches=[])
    assert build_grouping_request(empty, "group_x", "claude-x") is None
    assert build_outcome_request(MEETING, "outcome_x", "claude-x") is None


@pytest.mark.parametrize("max_tokens_hit", [False, True])
def test_sync_grouping_survives_a_low_per_model_cap(fake_client, max_tokens_hit):
    """A non-streaming create() with no explicit timeout raises a bare
    ValueError *before sending anything* once max_tokens exceeds the per-model
    non-streaming cap — 8192 for the opus-4.x ids, which a manual rescue run can
    pass via --model. That lands in a generic `except Exception` and is recorded
    as an ordinary meeting failure. Both grouping calls are exercised: the
    parametrization drives the 16384 retry, which is the one over every cap.
    """
    from pipeline.grouper import group_meeting

    fake_client.messages.create_text = '{"threads": []}'
    fake_client.messages.create_stop_reason = "max_tokens" if max_tokens_hit else "end_turn"

    assert group_meeting(fake_client, MEETING, model="claude-opus-4-1-20250805") == []
    assert len(fake_client.messages.create_calls) == (2 if max_tokens_hit else 1)
    assert all("timeout" in c for c in fake_client.messages.create_calls)


def test_sync_outcome_call_survives_a_low_per_model_cap(fake_client):
    from pipeline.grouper import extract_meeting_outcome, build_outcome_messages

    voted = _voted_meeting()
    assert build_outcome_messages(voted) is not None, "fixture no longer votes"

    fake_client.messages.create_text = '{"resolution": "r", "result": "可決"}'
    extract_meeting_outcome(fake_client, voted, model="claude-opus-4-1-20250805")

    assert fake_client.messages.create_calls, (
        "outcome API call was skipped — extract_meeting_outcome swallows every "
        "exception, so a silent skip and a crash look identical from outside"
    )
    assert all("timeout" in c for c in fake_client.messages.create_calls)


# ---------------------------------------------------------------------------
# AST sweeps — catch a hand-rolled request that bypassed the builders
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", SUMMARY_LAYER_MODULES, ids=lambda p: p.name)
def test_every_summary_layer_request_is_a_declared_site(path):
    """Fail closed on any request this file does not already know about.

    The previous shape of this guard listed things that were forbidden, so
    every request site it failed to *recognize* was a site it approved. Three
    separate one-line edits — a params dict with max_tokens assigned on the
    following line, a hand-rolled request at exactly OUTCOME_MAX_TOKENS, a
    retry ceiling lowered to 8192 — each reintroduced #47 or #48 with the whole
    suite green. Now an unrecognized site is itself the failure: route the
    request through a builder, or add it here and say why it is safe.
    """
    tree = _parse(path)
    declared = EXPECTED_SITES.get(path.name, {})
    found = {}

    for lineno, func, what, params in _request_sites(tree):
        found.setdefault(func, set())
        where = f"{path.name}:{lineno} in {func}() ({what})"
        assert func in declared, (
            f"undeclared summary-layer request site {where} — build it with "
            f"grouper.build_grouping_request / build_outcome_request / "
            f"summarizer.build_summary_request, or declare it in EXPECTED_SITES"
        )
        sampling = FORBIDDEN_PARAMS & set(params)
        assert not sampling, (
            f"{where} sends {sorted(sampling)} — claude-sonnet-5 rejects a "
            f"non-default sampling param with a 400, which is a whole-run "
            f"outage, not a degraded day (#51)"
        )
        assert _literal(params.get("thinking")) == REQUIRED_THINKING, (
            f"{where} does not pin thinking={REQUIRED_THINKING} — Sonnet 5 turns "
            f"adaptive thinking ON when the param is omitted, which both eats "
            f"the max_tokens budget and reintroduces run-to-run variation. "
            f"Checked by value, not presence: {{'type': 'enabled'}} is exactly "
            f"the state this is here to prevent"
        )
        ceiling, allowed = params.get("max_tokens"), declared[func]
        assert isinstance(ceiling, ast.Name), (
            f"{where} hardcodes its max_tokens — name it, so this table can "
            f"pin which ceiling belongs to which request"
        )
        if allowed is LOCAL_CEILING:
            assert ceiling.id not in KNOWN_CEILINGS, (
                f"{where} was expected to take its ceiling as a parameter"
            )
        else:
            assert ceiling.id in allowed, (
                f"{where} uses {ceiling.id}, expected one of {sorted(allowed)} — "
                f"a grouping request at the outcome ceiling truncates every thread"
            )
        found[func].add(ceiling.id)

    missing = set(declared) - set(found)
    assert not missing, (
        f"{path.name}: no request site found in {sorted(missing)} — the sweep "
        f"stopped seeing sites it used to check, so it is no longer guarding "
        f"anything there"
    )
    # Per-CEILING, not just per-function. group_meeting declares two sites (the
    # first call and the truncation retry); with only a per-function check,
    # rewriting the retry into a shape the sweep cannot see left it unguarded
    # while the test stayed green — the same fail-open this file exists to stop.
    for func, allowed in declared.items():
        if allowed is LOCAL_CEILING:
            continue
        unseen = set(allowed) - found[func]
        assert not unseen, (
            f"{path.name}: {func}() declares {sorted(allowed)} but the sweep only "
            f"saw {sorted(found[func])} — the site(s) using {sorted(unseen)} are "
            f"no longer recognized, so nothing checks their params any more"
        )
