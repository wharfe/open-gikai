"""Relevance ranking for news articles attached to threads.

Given a thread and a candidate list of news articles (from Bing News RSS),
ask Claude to pick the most-relevant ones and return them in order.

Why this exists:
    Bing News' first results for a parliamentary topic often include
    semantically-similar-but-actually-unrelated articles (e.g. "AI絵画展"
    when querying "AI規制"). A short Claude call sorts the noise out.

Neutrality:
    This is a helper layer for *auxiliary* context (news links), not for
    the summary itself. The summary layer must remain deterministic and
    LLM-free at request time — see CLAUDE.md "Summary Layer Invariants".
    Even here, we use temperature=0 and a fully-cached system prompt so
    re-runs over the same input produce the same selection.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

log = logging.getLogger("pipeline.news_ranker")

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Kept in this module (not pipeline/prompts.py) because it isn't part of
# the summarization pipeline that prompts.py exists to make auditable —
# this prompt only affects which news links surface, not summary text.
RANKER_SYSTEM = """\
あなたは国会の議論スレッドに関連するニュース記事を選別するアシスタントです。
政治的に中立な立場で、与党・野党を区別せず同じ基準で処理してください。"""

RANKER_INSTRUCTIONS = """\
与えられたニュース記事候補から、議論スレッドの「議題そのもの」と関連する記事を選んでください。

## 関連性の判定ルール
- 議題で直接議論されている法案・政策・事件に関する報道 → 関連あり
- 同じキーワードを含むが議題と無関係な記事 (例: 議題「AI規制」に対する「AI絵画展」) → 関連なし
- 議題の背景や前提となる事件・データを報じた記事 → 関連あり
- 単なる人物紹介・選挙速報・無関係なゴシップ → 関連なし
- 政党や政治家への党派的な評価記事は採用しない (中立性のため)

## 採用候補について
- 最大3件まで採用
- 関連性が同程度の場合は新しい報道を優先
- 関連する記事が0件なら空配列を返してよい (無理に埋めない)

## 出力 (JSONのみ、他のテキストは不要)
```json
{
  "selected_indices": [0, 3, 7],
  "reasoning": "短い理由 (50字以内)"
}
```
indicesは 0 始まり。順序は関連性の高い順。"""


def _format_article_for_ranking(idx: int, article: dict) -> str:
    """Format one article candidate for the ranker prompt."""
    title = article.get("title", "")
    source = article.get("source", "")
    pub_date = article.get("pubDate", "")
    return f"[{idx}] {title}\n  source: {source} / pubDate: {pub_date}"


def _format_thread_context(thread: dict) -> str:
    """Summarize the thread's topic for the ranker, without leaking full summaries."""
    parts = [
        f"議題: {thread.get('topic', '')}",
        f"委員会: {thread.get('committee', '')}",
        f"日付: {thread.get('date', '')}",
    ]
    summary = thread.get("summary", "")
    if summary:
        parts.append(f"スレッド要約: {summary}")
    ctx = thread.get("context") or {}
    description = ctx.get("description") if isinstance(ctx, dict) else None
    if description:
        parts.append(f"背景: {description}")
    return "\n".join(parts)


def _parse_json_response(text: str) -> dict:
    """Extract JSON from Claude's response, tolerating markdown fences."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse JSON from ranker response: {text[:200]}...")


def rank_news_articles(
    client,
    thread: dict,
    candidates: list[dict],
    max_keep: int = 3,
    model: str = DEFAULT_MODEL,
) -> list[dict]:
    """Pick the most-relevant news articles for a thread.

    Returns the surviving subset of ``candidates`` in the order Claude
    chose. Falls back to ``candidates[:max_keep]`` if anything goes wrong —
    news enrichment is best-effort.
    """
    if not candidates:
        return []
    # Below ~3 candidates there's nothing meaningful to filter; save an API
    # call and return as-is.
    if len(candidates) <= max_keep:
        return candidates[:max_keep]

    user_input = (
        f"## スレッド情報\n{_format_thread_context(thread)}\n\n"
        "## ニュース記事候補\n"
        + "\n".join(_format_article_for_ranking(i, a) for i, a in enumerate(candidates))
    )

    messages = [{
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": RANKER_INSTRUCTIONS,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": user_input},
        ],
    }]

    try:
        response = client.messages.create(
            model=model,
            max_tokens=512,
            temperature=0,
            system=RANKER_SYSTEM,
            messages=messages,
        )
    except Exception as e:
        log.warning("Ranker API call failed for '%s': %s — falling back",
                    thread.get("topic", "?"), e)
        return candidates[:max_keep]

    _log_cache_usage(response)

    text = response.content[0].text if response.content else ""
    try:
        parsed = _parse_json_response(text)
    except ValueError as e:
        log.warning("Ranker output unparseable for '%s': %s — falling back",
                    thread.get("topic", "?"), e)
        return candidates[:max_keep]

    indices = parsed.get("selected_indices", [])
    if not isinstance(indices, list):
        return candidates[:max_keep]

    selected: list[dict] = []
    seen: set[int] = set()
    for idx in indices:
        if not isinstance(idx, int) or idx < 0 or idx >= len(candidates):
            continue
        if idx in seen:
            continue
        seen.add(idx)
        selected.append(candidates[idx])
        if len(selected) >= max_keep:
            break

    reasoning = parsed.get("reasoning") or ""
    log.info(
        "  ranker[%s]: kept %d/%d (%s)",
        thread.get("id", "?")[-10:],
        len(selected),
        len(candidates),
        (reasoning[:50] + "…") if len(reasoning) > 50 else reasoning,
    )

    return selected


def _log_cache_usage(response) -> None:
    """Log prompt cache hit/write stats so we can verify caching works."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    if read or write:
        log.info("  cache[ranker]: read=%d write=%d", read, write)
