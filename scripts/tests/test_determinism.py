"""Guards for the summary layer's determinism + truncation invariants.

Three bugs motivated this file, and all three were the same shape: a rule that
was written down in CLAUDE.md and honored in one call site, while a sibling call
site quietly did the other thing.

  #47 — no call passed ``temperature``, so the "temperature=0, same input →
        same output" invariant ran at the API default. Measured: an identical
        re-request produced 8,527 then 9,603 output tokens.
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


def _module_ints(tree: ast.Module) -> dict:
    """Module-level ``NAME = <int>`` assignments, for resolving Name nodes."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            value = node.value.value
            if isinstance(value, int) and not isinstance(value, bool):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out[target.id] = value
    return out


def _resolve_int(node, consts):
    """Resolved int for a node, or None if it is not a resolvable integer.

    Bools are excluded: ``False == 0`` in Python but serializes to JSON
    ``false``, which the API rejects — accepting it as "zero" would let a
    guaranteed-400 request pass the temperature check.
    """
    if isinstance(node, ast.Constant):
        value = node.value
        return None if isinstance(value, bool) or not isinstance(value, (int, float)) else value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


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

    assert params["temperature"] == 0
    assert params["max_tokens"] == SUMMARY_MAX_TOKENS
    assert params["thinking"] == {"type": "disabled"}


def test_summary_request_param_set_is_pinned():
    """Adding a param to a summary request changes what compute_input_hash
    covers, so every sidecar written before the change stops verifying. That
    does not surface as a version error — is_current_schema still passes — it
    surfaces as a per-thread "input_hash mismatch — raw/prompt changed", which
    reads like corrupted raw data and burns the retry budget to permanent loss.
    Update this list and bump batch_state.SCHEMA_VERSION in the same commit.
    """
    params = build_summary_request(
        meeting=MEETING,
        thread_info={"topic": "T"},
        speeches=[{"speaker": "A", "speech": "x", "speechOrder": 1}],
        custom_id="s_abc_00",
        model="claude-x",
    )["params"]

    assert sorted(params) == [
        "max_tokens", "messages", "model", "system", "temperature", "thinking",
    ], "summary request param set changed — bump batch_state.SCHEMA_VERSION"


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
    assert grouping["temperature"] == 0

    voted = _voted_meeting()
    outcome = build_outcome_request(voted, "outcome_x", "claude-x")
    assert outcome is not None, "fixture no longer trips the vote pattern matcher"
    assert outcome["params"]["max_tokens"] == OUTCOME_MAX_TOKENS
    assert outcome["params"]["temperature"] == 0


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
    assert sent["temperature"] == batch["params"]["temperature"] == 0


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
        found.setdefault(func, 0)
        found[func] += 1
        where = f"{path.name}:{lineno} in {func}() ({what})"
        assert func in declared, (
            f"undeclared summary-layer request site {where} — build it with "
            f"grouper.build_grouping_request / build_outcome_request / "
            f"summarizer.build_summary_request, or declare it in EXPECTED_SITES"
        )
        assert _resolve_int(params.get("temperature"), {}) == 0, (
            f"{where} does not pin temperature=0 (#47: the API default is 1.0)"
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

    missing = set(declared) - set(found)
    assert not missing, (
        f"{path.name}: no request site found in {sorted(missing)} — the sweep "
        f"stopped seeing sites it used to check, so it is no longer guarding "
        f"anything there"
    )
