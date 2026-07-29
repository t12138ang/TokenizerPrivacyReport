"""Shared manifest-driven token statistics for final attacks.

The official Frequency Estimation and Naive Bayes scripts do not count the
non-overlapping tokens emitted by ``Tokenizer.encode``.  They first split text
with ``\\w+|[^\\w\\s]+`` and count every vocabulary item that is a substring
of a split unit, at most once per occurrence of that unit.  The Aho--Corasick
matcher below reproduces that feature definition without the official
quadratic token-by-word preprocessing files.
"""

from __future__ import annotations

import json
import re
from collections import deque
from collections import Counter
from pathlib import Path

from tokenizers import Tokenizer

from src.utils.run_metadata import sha256_text


WORD_PATTERN = re.compile(r"\w+|[^\w\s]+")


class SubstringVocabularyMatcher:
    """Deterministic Aho--Corasick matcher for unique vocabulary substrings."""

    def __init__(self, tokens: set[str]) -> None:
        self.transitions: list[dict[str, int]] = [{}]
        self.failures: list[int] = [0]
        self.outputs: list[list[str]] = [[]]
        for token in sorted(token for token in tokens if token):
            state = 0
            for character in token:
                next_state = self.transitions[state].get(character)
                if next_state is None:
                    next_state = len(self.transitions)
                    self.transitions[state][character] = next_state
                    self.transitions.append({})
                    self.failures.append(0)
                    self.outputs.append([])
                state = next_state
            self.outputs[state].append(token)

        queue: deque[int] = deque()
        for state in self.transitions[0].values():
            queue.append(state)
        while queue:
            state = queue.popleft()
            for character, next_state in self.transitions[state].items():
                queue.append(next_state)
                failure = self.failures[state]
                while failure and character not in self.transitions[failure]:
                    failure = self.failures[failure]
                self.failures[next_state] = self.transitions[failure].get(character, 0)
                inherited = self.outputs[self.failures[next_state]]
                if inherited:
                    self.outputs[next_state].extend(inherited)

    def unique_matches(self, value: str) -> set[str]:
        state = 0
        matches: set[str] = set()
        for character in value:
            while state and character not in self.transitions[state]:
                state = self.failures[state]
            state = self.transitions[state].get(character, 0)
            matches.update(self.outputs[state])
        return matches


def site_token_counts(
    tokenizer: Tokenizer, corpus_path: Path, site_ids: set[str]
) -> dict[str, Counter[str]]:
    """Count official overlapping substring features for selected sites.

    A vocabulary token contributes once for each occurrence of a regex-split
    unit containing it, even if it appears at multiple positions in that unit.
    This matches the official ``word_id2tokens`` plus word-frequency logic.
    """
    result = {site: Counter() for site in site_ids}
    matcher = SubstringVocabularyMatcher(set(tokenizer.get_vocab()))
    match_cache: dict[str, tuple[str, ...]] = {}
    with corpus_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            site = record["site_id"]
            if site in result:
                units = Counter(WORD_PATTERN.findall(record["text"]))
                counts = result[site]
                for unit, occurrences in units.items():
                    matched = match_cache.get(unit)
                    if matched is None:
                        matched = tuple(sorted(matcher.unique_matches(unit)))
                        match_cache[unit] = matched
                    for token in matched:
                        counts[token] += occurrences
    missing = [site for site, counts in result.items() if not counts]
    if missing:
        raise RuntimeError(f"zero token counts for {len(missing)} selected sites")
    return result


def aggregate_substring_token_counts(
    tokenizer: Tokenizer, corpus_path: Path, site_ids: set[str]
) -> Counter[str]:
    """Aggregate official overlapping substring features over selected sites."""
    matcher = SubstringVocabularyMatcher(set(tokenizer.get_vocab()))
    match_cache: dict[str, tuple[str, ...]] = {}
    result: Counter[str] = Counter()
    selected_texts = 0
    with corpus_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record["site_id"] not in site_ids:
                continue
            selected_texts += 1
            for unit, occurrences in Counter(WORD_PATTERN.findall(record["text"])).items():
                matched = match_cache.get(unit)
                if matched is None:
                    matched = tuple(sorted(matcher.unique_matches(unit)))
                    match_cache[unit] = matched
                for token in matched:
                    result[token] += occurrences
    if selected_texts == 0 or not result:
        raise RuntimeError("zero substring token counts for selected sites")
    return result


def aggregate_counts(
    counts: dict[str, Counter[str]], sites: set[str] | list[str]
) -> Counter[str]:
    result: Counter[str] = Counter()
    for site in sites:
        result.update(counts[site])
    return result


def deterministic_auxiliary_groups(
    site_ids: list[str], *, seed: int, group_count: int, fraction: float = 0.5
) -> list[list[str]]:
    if not site_ids or group_count <= 0 or not 0 < fraction <= 1:
        raise ValueError("invalid auxiliary grouping arguments")
    sample_size = max(1, round(len(site_ids) * fraction))
    groups = []
    for group_id in range(group_count):
        ranked = sorted(
            site_ids,
            key=lambda site: sha256_text(f"aux-group:{seed}:{group_id}:{site}"),
        )
        groups.append(sorted(ranked[:sample_size]))
    return groups
