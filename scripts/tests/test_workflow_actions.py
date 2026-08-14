"""#50: nothing a workflow can reach may run on a Node runtime older than 24.

GitHub is already forcing Node-20 actions onto Node 24 ("being forced to run on
Node.js 24" in every run's log), so the compatibility risk is being taken
whether or not the pins say so — and the forcing goes away eventually, at which
point a stale pin stops running rather than warning.

Two different things carry a Node runtime, and both are checked here, because
checking only the first leaves the guarantee false in a way that reads as true:

* **an external action's version**, which is a pin in a workflow;
* **a local action's `runs.using`**, which is a line in our own action.yml. A
  composite action delegating to `actions/checkout@v4` is caught by following
  its `uses`; a JavaScript action declaring `using: node20` has no `uses` at all
  and is caught only by reading the declaration itself.

For versions this is a floor, not an equality check. Pinning the exact current
major would fail this repo's CI the day a new one ships, which trains people to
edit the test instead of reading it. What must not happen is going back BELOW
the version where each action's default runtime became Node 24.

There is a ceiling too, and it guards a different accident. `daily-batch.yml`
runs unattended at 06:00 JST and **no CI job ever executes its steps**, so a
typo'd major (`setup-python@v8`) is green in every check here, merges, and then
takes the whole morning down with "Unable to resolve action" — fetch, summarize,
publish, and the failure-notification step all live in that one file. The
ceiling is a table of majors a human confirmed exist. Be honest about its reach:
it catches a *major* that was never published, which is the typo that actually
happens when hand-editing pins. It cannot catch `@v7.999.999`, and does not try
to — bare `vN` moving tags are the only spelling this repo allows (see
`_MAJOR_TAG`), so there is no patch component to get wrong.

The scan is the part most likely to rot, so nothing here is a regex over the
file text. Under a regex, `uses: 'actions/checkout@v4'`, `Actions/checkout@v4`,
a SHA pin, `actions/upload-artifact/merge@v4`, and any workflow named `*.yaml`
all sail past *every* check below while the suite stays green — a guard that
silently approves what it cannot recognise reads as coverage and is worse than
no guard. Four rules follow, and each exists because dropping it reopens a hole:

* **Parse, don't match.** pyyaml is already a CI dependency for the other
  workflow-contract tests. Every `uses:` is read as a YAML key.
* **Classify everything, exempt nothing implicitly.** A value this file cannot
  place — a SHA pin, a branch pin, a `./path` that resolves to nothing, a
  job-level reference that is not a legal reusable-workflow call — fails.
  Adopting SHA pinning is a fine decision; it just has to be made here rather
  than by accident in a workflow.
* **Follow local actions, then read them.** `uses: ./x` used to end the
  inquiry. It doesn't: the target is resolved, scanned like any other file, and
  its own runtime declaration is checked.
* **Check the closure, not the seeds.** Every file-level check runs over what
  the worklist actually visited. Restricting them to the files the directory
  walk happened to find would exempt precisely the ones that were reachable
  only by reference — the case the following exists for.

The policy tables are keyed by owner/repo and cover **every** external action,
not just `actions/*`. An unlisted one fails: this file cannot know whether
`some-vendor/thing@v1` runs on Node 20, and guessing "probably fine" for
everything outside a five-row table is how the guard would quietly shrink back
to covering the actions someone happened to list in 2026.
"""

import os
import re

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKFLOW_DIR = os.path.join(REPO_ROOT, ".github", "workflows")

# Directories that cannot hold a workflow or an action of ours, and are
# expensive or noisy to walk. Pruning is only about the *discovery* sweep — an
# action referenced from a workflow is followed wherever it lives.
_PRUNE = {".git", "node_modules", ".next", "out", "__pycache__", ".venv", "venv"}

_ACTION_METADATA = ("action.yml", "action.yaml")

# Major version at which each action's DEFAULT runtime became Node 24, verified
# against `runs.using` in that tag's action.yml.
# Several of these shipped a "preliminary Node 24 support" major first that still
# ran on Node 20 by default (upload-artifact v5, download-artifact v6) — the
# floor is the one after that, not the first one to mention node24.
NODE24_FLOOR = {
    "actions/checkout": 5,
    "actions/setup-node": 5,
    "actions/setup-python": 6,
    "actions/upload-artifact": 6,
    "actions/download-artifact": 7,
}

# Highest major each action has actually PUBLISHED, as confirmed against the tag
# list at the time of the pin. Raise an entry only together with the pin that
# needs it, and only after checking the tag exists — that check is the entire
# point of the table. upload-artifact and download-artifact sit on different
# majors on purpose: that is the current pairing, upload-artifact has no v8.
LATEST_VERIFIED_MAJOR = {
    "actions/checkout": 7,
    "actions/setup-node": 7,
    "actions/setup-python": 7,
    "actions/upload-artifact": 7,
    "actions/download-artifact": 8,
}

# Oldest Node runtime a local action of ours may declare in `runs.using`.
MIN_LOCAL_NODE_RUNTIME = 24

_ACTION_REF = re.compile(r"^(?P<path>[^@\s]+)@(?P<ref>\S+)$")
# Bare moving major only. `v7.0.1` and `v07` are refused, not because they are
# unsafe but because allowing them would make the ceiling above a claim this
# file cannot back: it verifies majors, so majors are all it accepts.
_MAJOR_TAG = re.compile(r"^v(0|[1-9]\d*)$")
# A local action reference. GitHub takes no `@ref` here — the action is read
# from the same commit — so a `./path@v1` is a mistake, not a pin to resolve.
_LOCAL_REF = re.compile(r"^\./[^@\s]*$")
# The only two legal reusable-workflow spellings.
_LOCAL_WORKFLOW = re.compile(r"^\./\.github/workflows/[^/@\s]+\.ya?ml$")
_REMOTE_WORKFLOW = re.compile(
    r"^[\w.-]+/[\w.-]+/\.github/workflows/[^/@\s]+\.ya?ml@\S+$")
_NODE_RUNTIME = re.compile(r"^node(\d+)$")

# Keys whose subtrees are free-form user data, where a `uses` key is a variable
# or an input name rather than an action reference. Excluded from the census so
# it cannot raise a false alarm that someone would silence by weakening it.
_NOT_ACTION_SCOPES = {"with", "env", "inputs", "outputs", "secrets", "strategy",
                      "defaults", "permissions", "concurrency"}


def _yaml_files_in(directory):
    if not os.path.isdir(directory):
        return
    for name in sorted(os.listdir(directory)):
        if name.endswith((".yml", ".yaml")):
            yield os.path.join(directory, name)


def _action_metadata_files():
    """Every action.yml/action.yaml in the repo, at any depth.

    GitHub does not reserve a directory for composite actions — any directory
    holding action metadata is one — so this cannot be a fixed glob.
    """
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in _PRUNE]
        for name in sorted(files):
            if name in _ACTION_METADATA:
                yield os.path.join(root, name)


def _seed_files():
    return sorted(set(_yaml_files_in(WORKFLOW_DIR)) | set(_action_metadata_files()))


def _local_action_file(value):
    """The metadata file a `./path` reference resolves to, or None.

    None on anything that does not resolve to a real action inside this repo —
    including a path that escapes via `../` or a symlink, which the caller turns
    into a failure rather than a skip.
    """
    if not _LOCAL_REF.match(value):
        return None
    base = os.path.realpath(os.path.join(REPO_ROOT, value[2:]))
    root = os.path.realpath(REPO_ROOT)
    if os.path.commonpath([base, root]) != root:
        return None
    for name in _ACTION_METADATA:
        candidate = os.path.join(base, name)
        if os.path.isfile(candidate) and \
                os.path.commonpath([os.path.realpath(candidate), root]) == root:
            return candidate
    return None


def _uses_in_doc(doc):
    """(context, value) for every `uses:` a runner would act on.

    context is "job" for a reusable-workflow call and "step" for an action, and
    the two are NOT interchangeable: only a job-level `uses` may name a workflow
    file, so a step-level `owner/repo/thing.yml@v1` is an action in a directory
    called `thing.yml`, not a workflow to wave through.
    """
    if not isinstance(doc, dict):
        return
    for job in (doc.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        if isinstance(job.get("uses"), str):
            yield ("job", job["uses"])
        for step in job.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("uses"), str):
                yield ("step", step["uses"])
    runs = doc.get("runs")
    if isinstance(runs, dict):
        for step in runs.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("uses"), str):
                yield ("step", step["uses"])


def _classify(context, value):
    """dict(kind=..., action=..., major=..., follow=...) for one `uses:`.

    kind:
      exempt       — nothing with a Node major to check here (docker image, or
                     a legal reusable-workflow call, whose own steps GitHub
                     resolves in the repo that owns it)
      local        — an action inside this repo; `follow` is its metadata file
      versioned    — an external action on a bare vN tag
      unrecognised — everything else, which fails
    """
    value = value.strip()
    blank = {"kind": None, "action": None, "major": None, "follow": None}
    if value.startswith("docker://"):
        return dict(blank, kind="exempt")
    if context == "job":
        # A job-level `uses` is always a reusable-workflow call, and only two
        # spellings of one are legal. Accepting "ends in .yml" instead would
        # exempt `./typo.yml` and `octo/repo/thing.yml@v1` unchecked.
        if _LOCAL_WORKFLOW.match(value) or _REMOTE_WORKFLOW.match(value):
            return dict(blank, kind="exempt")
        return dict(blank, kind="unrecognised")
    if value.startswith("./") or value.startswith("."):
        target = _local_action_file(value)
        if target is None:
            return dict(blank, kind="unrecognised")
        return dict(blank, kind="local", follow=target)
    m = _ACTION_REF.match(value)
    if not m:
        return dict(blank, kind="unrecognised")
    parts = m.group("path").split("/")
    if len(parts) < 2:
        return dict(blank, kind="unrecognised")
    # GitHub resolves action refs case-insensitively, and a sub-path action
    # (actions/upload-artifact/merge) is tagged from its repo, so the policy is
    # a property of owner/repo.
    action = "/".join(parts[:2]).lower()
    tag = _MAJOR_TAG.match(m.group("ref"))
    if not tag:
        return dict(blank, kind="unrecognised", action=action)
    return dict(blank, kind="versioned", action=action, major=int(tag.group(1)))


def _scan():
    """(records, visited) for everything reachable from workflows and actions.

    A worklist rather than a file list: a local action discovered through a
    `uses: ./x` is scanned even if it lives somewhere `_action_metadata_files`
    would not have thought to look. `visited` is what the file-level checks run
    over, so following something and then not checking it cannot happen.
    """
    queue = _seed_files()
    visited = set(queue)
    found = []
    while queue:
        path = queue.pop()
        doc = yaml.safe_load(open(path, encoding="utf-8"))
        for context, value in _uses_in_doc(doc):
            record = _classify(context, value)
            record["file"] = os.path.relpath(path, REPO_ROOT)
            record["uses"] = value
            found.append(record)
            follow = record["follow"]
            if follow and follow not in visited:
                visited.add(follow)
                queue.append(follow)
    return found, sorted(visited)


def _records():
    return _scan()[0]


def _visited():
    return _scan()[1]


def _pins():
    """(file, action, major) for every versioned external-action pin."""
    return [(r["file"], r["action"], r["major"])
            for r in _records() if r["kind"] == "versioned"]


def _count_uses_keys(node, in_action_scope=True):
    """`uses` keys in a composed document, at positions that could be one.

    Counting keys rather than lines of text is what makes this a census: a
    `uses:` in flow style is counted, and the word "uses:" inside a `run:` block
    is not, because neither is a question about the text. Free-form subtrees
    (`env:`, `with:`, `inputs:`) are skipped — a variable happening to be called
    `uses` is not a pin, and a census that failed on one would be silenced.
    """
    total = 0
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            named = key.value if isinstance(key, yaml.ScalarNode) else None
            if in_action_scope and named == "uses":
                total += 1
            total += _count_uses_keys(
                value, in_action_scope and named not in _NOT_ACTION_SCOPES)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            total += _count_uses_keys(item, in_action_scope)
    return total


def test_no_scanned_file_uses_yaml_anchors():
    """Anchors would make the census below count a shared step once, not twice.

    The restriction is the point: this repo uses none, and forbidding them keeps
    "one `uses` key in the file" and "one `uses` the runner acts on" the same
    number. Wanting an anchor is a reason to revisit this guard, not to delete
    the check.
    """
    for path in _visited():
        with open(path, encoding="utf-8") as fh:
            offenders = [type(tok).__name__ for tok in yaml.scan(fh)
                         if isinstance(tok, (yaml.tokens.AnchorToken,
                                             yaml.tokens.AliasToken))]
        assert offenders == [], (
            f"{os.path.relpath(path, REPO_ROOT)} uses YAML anchors/aliases "
            f"({', '.join(offenders)}); the `uses` census assumes it can count "
            f"each one once")


def test_every_uses_key_is_reached_by_the_scan():
    """The checks below are only as good as the walk that feeds them.

    Without this, a `uses:` in a shape `_uses_in_doc` does not visit — a new
    document layout, a nesting level nobody anticipated — disappears silently
    while every assertion still passes.
    """
    for path in _visited():
        text = open(path, encoding="utf-8").read()
        present = _count_uses_keys(yaml.compose(text))
        walked = len(list(_uses_in_doc(yaml.safe_load(text))))
        assert present == walked, (
            f"{os.path.relpath(path, REPO_ROOT)}: {present} `uses` keys in the "
            f"document but {walked} reached by the scan — a pin is invisible "
            f"to this guard")


def test_no_local_action_declares_an_outdated_node_runtime():
    """Following a local action's `uses` is not the same as checking it.

    A composite action delegating to `actions/checkout@v4` is caught by the
    floor test. A JavaScript action with `runs: {using: node20, main: index.js}`
    has no `uses` at all, so every other check here passes it while it runs on
    exactly the runtime this module exists to keep out.
    """
    offenders = []
    for path in _visited():
        if os.path.basename(path) not in _ACTION_METADATA:
            continue
        runs = (yaml.safe_load(open(path, encoding="utf-8")) or {}).get("runs")
        using = runs.get("using") if isinstance(runs, dict) else None
        m = _NODE_RUNTIME.match(using) if isinstance(using, str) else None
        if m and int(m.group(1)) < MIN_LOCAL_NODE_RUNTIME:
            offenders.append(f"{os.path.relpath(path, REPO_ROOT)} runs.using={using}")
    assert offenders == [], (
        f"local actions must declare node{MIN_LOCAL_NODE_RUNTIME} or newer: "
        + ", ".join(offenders))


def test_the_workflows_are_actually_being_scanned():
    """Finding no files at all is the other way this guard passes for free."""
    scanned = {os.path.basename(r["file"]) for r in _records()}
    assert {"ci.yml", "daily-batch.yml"} <= scanned, scanned
    assert any(a == "actions/checkout" for _f, a, _m in _pins())


def test_no_uses_is_unrecognisable():
    """Every `uses:` must land in a bucket this guard understands.

    A SHA pin, a branch pin (`@main`), a patch-level tag, a `./path` that
    resolves to no action metadata or escapes the repo, or a job-level
    reference that is not a legal reusable-workflow call would otherwise be
    waved through with no version checked at all.
    """
    offenders = [f"{r['file']}: {r['uses']}"
                 for r in _records() if r["kind"] == "unrecognised"]
    assert offenders == [], (
        "these `uses:` values carry no checkable major version: "
        + ", ".join(offenders))


def test_every_external_action_has_a_recorded_policy():
    """An action with no floor recorded is unchecked, not approved.

    Restricting this to `actions/*` would let `github/codeql-action@v2` — or any
    vendor action — pass every check below on a Node 20 runtime, while the
    module docstring claims no such pin exists.
    """
    used = {a for _f, a, _m in _pins()}
    assert used <= set(NODE24_FLOOR), (
        "these actions have no Node 24 floor recorded: "
        + ", ".join(sorted(used - set(NODE24_FLOOR))))


@pytest.mark.parametrize("action,floor", sorted(NODE24_FLOOR.items()))
def test_no_action_is_pinned_below_its_node24_major(action, floor):
    offenders = [f"{f} {a}@v{major}"
                 for f, a, major in _pins()
                 if a == action and major < floor]
    assert offenders == [], (
        f"{action}@v{floor} is the first major whose default runtime is Node 24; "
        f"anything older runs on a deprecated runtime that GitHub currently "
        f"force-upgrades and will eventually stop running: " + ", ".join(offenders)
    )


@pytest.mark.parametrize("action,latest", sorted(LATEST_VERIFIED_MAJOR.items()))
def test_no_action_is_pinned_above_a_verified_major(action, latest):
    """A floor alone accepts a major that was never published.

    daily-batch.yml's pins are executed by nothing in CI, so a typo there is
    green everywhere and only surfaces at 06:00 JST as an unresolvable action,
    with the step that would have reported the failure equally unresolvable.
    """
    offenders = [f"{f} {a}@v{major}"
                 for f, a, major in _pins()
                 if a == action and major > latest]
    assert offenders == [], (
        f"{action}@v{latest} is the newest major recorded as existing. Bumping "
        f"past it means confirming the tag is real and raising "
        f"LATEST_VERIFIED_MAJOR in the same commit: " + ", ".join(offenders)
    )


def test_the_version_tables_agree_with_each_other():
    """Floor above ceiling would make one of the two unreachable and unread."""
    assert set(NODE24_FLOOR) == set(LATEST_VERIFIED_MAJOR)
    for action, floor in NODE24_FLOOR.items():
        assert floor <= LATEST_VERIFIED_MAJOR[action], action
