"""Public-candidate, site-clipped, batched BPE with four aggregation modes."""

from __future__ import annotations

import json
import math
import os
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
from tokenizers import Tokenizer
from tokenizers.models import BPE
from tokenizers.pre_tokenizers import Whitespace

from src.crypto.paillier_aggregation import (
    AggregationServer,
    DataClient,
    DecryptionSelectionServer,
    ProtocolKeys,
    generate_keys,
    plaintext_aggregate,
)
from src.privacy.accountant import basic_composition, uniform_round_budget
from src.privacy.clipping import l1_clip_largest_remainder
from src.privacy.discrete_laplace import two_sided_geometric
from src.tokenizer.common import SPECIAL_TOKENS
from src.utils.run_metadata import (
    PROJECT_ROOT,
    canonical_sha256,
    environment_metadata,
    peak_working_set_bytes,
    sha256_file,
    strict_json_load,
    strict_json_dumps,
    utc_now,
    write_json_exclusive,
)


AGGREGATION_MODES = {"plain", "he_only", "local_dp", "sa_dp"}


def _encrypt_vector_process(arguments: tuple[Any, list[int]]) -> list[Any]:
    public_key, values = arguments
    return DataClient(public_key).encrypt_vector(values)


def _decrypt_vector_process(arguments: tuple[Any, list[Any]]) -> list[int]:
    private_key, values = arguments
    return DecryptionSelectionServer(private_key).decrypt_aggregate(values)


def _contiguous_chunks(values: list[Any], count: int) -> list[list[Any]]:
    width = max(1, math.ceil(len(values) / max(1, count)))
    return [values[index : index + width] for index in range(0, len(values), width)]


def public_alphabet_base(
    public_texts: dict[str, list[str]],
) -> tuple[dict[str, int], list[tuple[str, str]]]:
    """Build the initial alphabet only from the disjoint public corpus."""
    pre_tokenizer = Whitespace()
    alphabet: set[str] = set()
    for site in sorted(public_texts):
        for text in public_texts[site]:
            for piece, _ in pre_tokenizer.pre_tokenize_str(text):
                alphabet.update(piece)
    tokens = SPECIAL_TOKENS + sorted(alphabet)
    return {token: index for index, token in enumerate(dict.fromkeys(tokens))}, []


def tokenizer_from_state(vocab: dict[str, int], merges: list[tuple[str, str]]) -> Tokenizer:
    tokenizer = Tokenizer(BPE(vocab=vocab, merges=merges, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.add_special_tokens([token for token in SPECIAL_TOKENS if token in vocab])
    return tokenizer


def load_site_texts(corpus_path: Path, site_ids: set[str]) -> dict[str, list[str]]:
    result = {site: [] for site in site_ids}
    with corpus_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record["site_id"] in result:
                result[record["site_id"]].append(record["text"])
    missing = [site for site, texts in result.items() if not texts]
    if missing:
        raise RuntimeError(f"missing texts for {len(missing)} selected sites")
    return result


def count_adjacent_pairs(tokenizer: Tokenizer, texts: list[str]) -> Counter[tuple[str, str]]:
    result: Counter[tuple[str, str]] = Counter()
    for encoding in tokenizer.encode_batch(texts):
        tokens = encoding.tokens
        result.update(zip(tokens, tokens[1:]))
    return result


def all_site_pair_counts(
    tokenizer: Tokenizer, site_texts: dict[str, list[str]]
) -> dict[str, Counter[tuple[str, str]]]:
    sites: list[str] = []
    texts: list[str] = []
    for site in sorted(site_texts):
        for text in site_texts[site]:
            sites.append(site)
            texts.append(text)
    result: dict[str, Counter[tuple[str, str]]] = {
        site: Counter() for site in sorted(site_texts)
    }
    for site, encoding in zip(sites, tokenizer.encode_batch(texts)):
        tokens = encoding.tokens
        word_ids = encoding.word_ids
        result[site].update(
            (left, right)
            for left, right, left_word, right_word in zip(
                tokens, tokens[1:], word_ids, word_ids[1:]
            )
            if left_word is not None and left_word == right_word
        )
    return result


class IncrementalPairCorpus:
    """Maintain exact within-pretoken pair counts while applying compatible merges.

    The representation deduplicates equal pre-tokenized pieces and records their
    per-site multiplicities.  A merge only touches word states containing one of
    the selected pairs, so the b=1 ablation does not re-tokenize the full corpus
    thousands of times.
    """

    def __init__(
        self,
        word_site_frequencies: dict[tuple[str, ...], Counter[str]],
        site_ids: list[str],
    ) -> None:
        self.word_site_frequencies = word_site_frequencies
        self.site_ids = sorted(site_ids)
        self.global_pair_counts: Counter[tuple[str, str]] = Counter()
        self.pair_site_counts: dict[tuple[str, str], Counter[str]] = {}
        self.pair_to_words: dict[tuple[str, str], set[tuple[str, ...]]] = defaultdict(set)
        self._rebuild_indexes()

    @classmethod
    def from_site_texts(
        cls,
        tokenizer: Tokenizer,
        site_texts: dict[str, list[str]],
    ) -> "IncrementalPairCorpus":
        sites: list[str] = []
        texts: list[str] = []
        for site in sorted(site_texts):
            for text in site_texts[site]:
                sites.append(site)
                texts.append(text)
        words: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
        for site, encoding in zip(sites, tokenizer.encode_batch(texts)):
            current_word_id: int | None = None
            current_tokens: list[str] = []
            for token, word_id in zip(encoding.tokens, encoding.word_ids):
                if word_id is None:
                    continue
                if current_word_id is not None and word_id != current_word_id:
                    if current_tokens:
                        words[tuple(current_tokens)][site] += 1
                    current_tokens = []
                current_word_id = word_id
                current_tokens.append(token)
            if current_tokens:
                words[tuple(current_tokens)][site] += 1
        return cls(dict(words), list(site_texts))

    @staticmethod
    def _pairs(tokens: tuple[str, ...]) -> Counter[tuple[str, str]]:
        return Counter(zip(tokens, tokens[1:]))

    def _rebuild_indexes(self) -> None:
        for tokens, site_frequencies in self.word_site_frequencies.items():
            for pair, multiplicity in self._pairs(tokens).items():
                self.pair_to_words[pair].add(tokens)
                site_counts = self.pair_site_counts.setdefault(pair, Counter())
                for site, frequency in site_frequencies.items():
                    contribution = int(multiplicity) * int(frequency)
                    site_counts[site] += contribution
                    self.global_pair_counts[pair] += contribution

    @staticmethod
    def _merge_tokens(
        tokens: tuple[str, ...],
        replacements: dict[tuple[str, str], str],
    ) -> tuple[str, ...]:
        merged: list[str] = []
        index = 0
        while index < len(tokens):
            if index + 1 < len(tokens):
                replacement = replacements.get((tokens[index], tokens[index + 1]))
                if replacement is not None:
                    merged.append(replacement)
                    index += 2
                    continue
            merged.append(tokens[index])
            index += 1
        return tuple(merged)

    def _change_pair_counts(
        self,
        tokens: tuple[str, ...],
        site_frequencies: Counter[str],
        direction: int,
    ) -> None:
        for pair, multiplicity in self._pairs(tokens).items():
            site_counts = self.pair_site_counts.setdefault(pair, Counter())
            for site, frequency in site_frequencies.items():
                contribution = direction * int(multiplicity) * int(frequency)
                site_counts[site] += contribution
                self.global_pair_counts[pair] += contribution
                if site_counts[site] == 0:
                    del site_counts[site]
            if self.global_pair_counts[pair] == 0:
                del self.global_pair_counts[pair]
            if not site_counts:
                del self.pair_site_counts[pair]

    def apply_compatible_merges(self, selected_pairs: list[tuple[str, str]]) -> None:
        replacements = {pair: pair[0] + pair[1] for pair in selected_pairs}
        affected: set[tuple[str, ...]] = set()
        for pair in selected_pairs:
            affected.update(self.pair_to_words.get(pair, set()))
        moved: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
        for old_tokens in affected:
            site_frequencies = self.word_site_frequencies.pop(old_tokens, None)
            if site_frequencies is None:
                continue
            old_pairs = set(self._pairs(old_tokens))
            self._change_pair_counts(old_tokens, site_frequencies, -1)
            for pair in old_pairs:
                words = self.pair_to_words.get(pair)
                if words is not None:
                    words.discard(old_tokens)
                    if not words:
                        del self.pair_to_words[pair]
            new_tokens = self._merge_tokens(old_tokens, replacements)
            moved[new_tokens].update(site_frequencies)
        for new_tokens, site_frequencies in moved.items():
            self.word_site_frequencies.setdefault(new_tokens, Counter()).update(site_frequencies)
            self._change_pair_counts(new_tokens, site_frequencies, 1)
            for pair in self._pairs(new_tokens):
                self.pair_to_words[pair].add(new_tokens)

    def candidates(
        self,
        current_vocab: dict[str, int],
        pool_size: int,
    ) -> tuple[list[tuple[str, str]], dict[str, Any]]:
        eligible = [
            (pair, int(count))
            for pair, count in self.global_pair_counts.items()
            if count > 0 and pair[0] + pair[1] not in current_vocab
        ]
        candidates = [
            pair
            for pair, _ in sorted(eligible, key=lambda item: (-item[1], item[0]))[:pool_size]
        ]
        public_counts = [int(self.global_pair_counts[pair]) for pair in candidates]
        per_site_norms = [
            sum(int(self.pair_site_counts.get(pair, {}).get(site, 0)) for pair in candidates)
            for site in self.site_ids
        ]
        payload = {
            "pairs": [[left, right] for left, right in candidates],
            "public_counts": public_counts,
        }
        return candidates, {
            "candidate_pool_hash": canonical_sha256(payload),
            "candidate_count": len(candidates),
            "public_site_l1_norms": per_site_norms,
        }

    def candidate_vectors(
        self,
        candidates: list[tuple[str, str]],
    ) -> tuple[list[str], list[list[int]]]:
        vectors = [
            [int(self.pair_site_counts.get(pair, {}).get(site, 0)) for pair in candidates]
            for site in self.site_ids
        ]
        return list(self.site_ids), vectors


def public_candidates(
    tokenizer: Tokenizer,
    public_texts: dict[str, list[str]],
    current_vocab: dict[str, int],
    pool_size: int,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    counts: Counter[tuple[str, str]] = Counter()
    counts_by_site = all_site_pair_counts(tokenizer, public_texts)
    for site in sorted(public_texts):
        site_counts = counts_by_site[site]
        filtered = Counter(
            {
                pair: count
                for pair, count in site_counts.items()
                if pair[0] + pair[1] not in current_vocab
            }
        )
        counts.update(filtered)
    candidates = [
        pair
        for pair, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:pool_size]
    ]
    payload = {
        "pairs": [[left, right] for left, right in candidates],
        "public_counts": [int(counts[pair]) for pair in candidates],
    }
    per_site_norms = [
        sum(int(counts_by_site[site].get(pair, 0)) for pair in candidates)
        for site in sorted(public_texts)
    ]
    return candidates, {
        "candidate_pool_hash": canonical_sha256(payload),
        "candidate_count": len(candidates),
        "public_site_l1_norms": per_site_norms,
    }


def site_candidate_vectors(
    tokenizer: Tokenizer,
    site_texts: dict[str, list[str]],
    candidates: list[tuple[str, str]],
) -> tuple[list[str], list[list[int]]]:
    index = {pair: position for position, pair in enumerate(candidates)}
    sites = sorted(site_texts)
    vectors: list[list[int]] = []
    counts_by_site = all_site_pair_counts(tokenizer, site_texts)
    for site in sites:
        counts = counts_by_site[site]
        vector = [0] * len(candidates)
        for pair, count in counts.items():
            position = index.get(pair)
            if position is not None:
                vector[position] = int(count)
        vectors.append(vector)
    return sites, vectors


def compatible_top_indices(
    values: list[int], candidates: list[tuple[str, str]], batch_size: int
) -> list[int]:
    ranked = sorted(range(len(values)), key=lambda index: (-values[index], index))
    selected = []
    selected_lefts: set[str] = set()
    selected_rights: set[str] = set()
    selected_outputs: set[str] = set()
    for index in ranked:
        left, right = candidates[index]
        # Two rules can be applied in the same deterministic left-to-right pass
        # unless the right symbol of one is the left symbol of the other.  Equal
        # lefts or equal rights are harmless because a concrete adjacent
        # occurrence can match at most one of them.
        output = left + right
        if left in selected_rights or right in selected_lefts or output in selected_outputs:
            continue
        selected_lefts.add(left)
        selected_rights.add(right)
        selected_outputs.add(output)
        selected.append(index)
        if len(selected) >= batch_size:
            break
    return selected


def aggregate_and_select(
    *,
    mode: str,
    vectors: list[list[int]],
    candidates: list[tuple[str, str]],
    batch_size: int,
    clipping_bound: int,
    epsilon_round: float | None,
    rng: np.random.Generator,
    key_bits: int,
    real_paillier: bool,
    paillier_keys: ProtocolKeys | None = None,
    paillier_worker_processes: int = 1,
) -> tuple[list[int], dict[str, Any]]:
    if mode not in AGGREGATION_MODES:
        raise ValueError(f"unknown aggregation mode: {mode}")
    started = time.perf_counter()
    clipped = [
        list(map(int, l1_clip_largest_remainder(vector, clipping_bound)))
        for vector in vectors
    ]
    noise: list[int] = [0] * len(candidates)
    crypto = {
        "real_paillier": False,
        "key_bits": None,
        "worker_processes": 1,
        "client_ciphertext_count": 0,
        "encryption_seconds": 0.0,
        "aggregation_seconds": 0.0,
        "noise_encryption_seconds": 0.0,
        "decryption_selection_seconds": 0.0,
    }
    if mode == "plain":
        aggregate = plaintext_aggregate(vectors)
        selected = compatible_top_indices(aggregate, candidates, batch_size)
    elif mode == "local_dp":
        if epsilon_round is None:
            raise ValueError("local_dp requires epsilon_round")
        noisy_clients = []
        for vector in clipped:
            local_noise = two_sided_geometric(
                epsilon=epsilon_round,
                sensitivity=clipping_bound,
                size=len(candidates),
                rng=rng,
            )
            noisy_clients.append([int(value) + int(delta) for value, delta in zip(vector, local_noise)])
        aggregate = plaintext_aggregate(noisy_clients)
        selected = compatible_top_indices(aggregate, candidates, batch_size)
    elif mode in {"he_only", "sa_dp"} and real_paillier:
        keys = paillier_keys if paillier_keys is not None else generate_keys(key_bits)
        if keys.actual_modulus_bits < key_bits:
            raise RuntimeError("provided Paillier modulus is smaller than requested")
        crypto["real_paillier"] = True
        crypto["key_bits"] = keys.actual_modulus_bits
        crypto["worker_processes"] = max(1, int(paillier_worker_processes))
        client = DataClient(keys.public_key)
        server_a = AggregationServer(keys.public_key)
        server_d = DecryptionSelectionServer(keys.private_key)
        source_vectors = vectors if mode == "he_only" else clipped
        workers = max(1, int(paillier_worker_processes))
        executor_context = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
        try:
            phase = time.perf_counter()
            if executor_context is None:
                ciphertexts = [client.encrypt_vector(vector) for vector in source_vectors]
            else:
                ciphertexts = list(executor_context.map(
                    _encrypt_vector_process,
                    [(keys.public_key, vector) for vector in source_vectors],
                ))
            crypto["encryption_seconds"] = time.perf_counter() - phase
            crypto["client_ciphertext_count"] = len(source_vectors) * len(candidates)
            phase = time.perf_counter()
            encrypted_aggregate = server_a.aggregate(ciphertexts)
            crypto["aggregation_seconds"] = time.perf_counter() - phase
            if mode == "sa_dp":
                if epsilon_round is None:
                    raise ValueError("sa_dp requires epsilon_round")
                noise = list(
                    map(
                        int,
                        two_sided_geometric(
                            epsilon=epsilon_round,
                            sensitivity=clipping_bound,
                            size=len(candidates),
                            rng=rng,
                        ),
                    )
                )
                phase = time.perf_counter()
                if executor_context is None:
                    encrypted_aggregate = server_a.add_encrypted_noise(encrypted_aggregate, noise)
                else:
                    noise_chunks = _contiguous_chunks(noise, workers)
                    encrypted_noise = [
                        value
                        for chunk in executor_context.map(
                            _encrypt_vector_process,
                            [(keys.public_key, chunk) for chunk in noise_chunks],
                        )
                        for value in chunk
                    ]
                    encrypted_aggregate = [
                        value + encrypted_delta
                        for value, encrypted_delta in zip(encrypted_aggregate, encrypted_noise)
                    ]
                crypto["noise_encryption_seconds"] = time.perf_counter() - phase
            phase = time.perf_counter()
            if executor_context is None:
                decrypted = server_d.decrypt_aggregate(encrypted_aggregate)
            else:
                encrypted_chunks = _contiguous_chunks(list(encrypted_aggregate), workers)
                decrypted = [
                    value
                    for chunk in executor_context.map(
                        _decrypt_vector_process,
                        [(keys.private_key, chunk) for chunk in encrypted_chunks],
                    )
                    for value in chunk
                ]
            selected = compatible_top_indices(decrypted, candidates, batch_size)
            crypto["decryption_selection_seconds"] = time.perf_counter() - phase
            crypto["decryption_disclosure"] = {
                "decrypted_dimension": len(decrypted),
                "returned_merge_id_count": len(selected),
                "full_frequency_vector_returned_to_A": False,
            }
        finally:
            if executor_context is not None:
                executor_context.shutdown(wait=True, cancel_futures=False)
    else:
        source_vectors = vectors if mode == "he_only" else clipped
        aggregate = plaintext_aggregate(source_vectors)
        if mode == "sa_dp":
            if epsilon_round is None:
                raise ValueError("sa_dp requires epsilon_round")
            noise = list(
                map(
                    int,
                    two_sided_geometric(
                        epsilon=epsilon_round,
                        sensitivity=clipping_bound,
                        size=len(candidates),
                        rng=rng,
                    ),
                )
            )
            aggregate = [value + delta for value, delta in zip(aggregate, noise)]
        selected = compatible_top_indices(aggregate, candidates, batch_size)
    crypto["total_seconds"] = time.perf_counter() - started
    crypto["noise_l1"] = sum(abs(value) for value in noise)
    return selected, crypto


def train_batched_private_bpe(
    *,
    manifest_path: Path,
    output_dir: Path,
    mode: str,
    requested_vocab_size: int,
    candidate_pool_size: int,
    clipping_percentile: int,
    batch_size: int,
    epsilon_total: float | None,
    key_bits: int,
    real_paillier: bool,
    method_id: str | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    checkpoint_every_rounds: int = 5,
    paillier_worker_processes: int = 1,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    output_dir = output_dir.resolve()
    manifest = strict_json_load(manifest_path)
    requested_vocab_size_input = int(requested_vocab_size)
    method_identifier = method_id or mode
    training_key = canonical_sha256({
        "manifest_sha256": manifest["manifest_sha256"],
        "mode": mode,
        "method_id": method_identifier,
        "requested_vocab_size": requested_vocab_size_input,
        "candidate_pool_size": candidate_pool_size,
        "clipping_percentile": clipping_percentile,
        "batch_size": batch_size,
        "epsilon_total": epsilon_total,
        "key_bits": key_bits,
        "real_paillier": real_paillier,
        "paillier_worker_processes": paillier_worker_processes,
    })
    artifact = output_dir / "tokenizer.json"
    metadata_path = output_dir / "metadata.json"
    if artifact.is_file() and metadata_path.is_file():
        metadata = strict_json_load(metadata_path)
        if (
            metadata.get("status") == "success"
            and metadata.get("artifact_sha256") == sha256_file(artifact)
            and metadata.get("manifest_sha256") == manifest["manifest_sha256"]
            and metadata.get("training_key") == training_key
        ):
            return {**metadata, "checkpoint_reused": True}
        raise RuntimeError(f"stale, mismatched, or invalid existing private-BPE checkpoint: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "training_checkpoint.json"
    partial_checkpoint = checkpoint_path.with_suffix(checkpoint_path.suffix + ".partial")
    if partial_checkpoint.exists():
        raise FileExistsError(f"partial private-BPE checkpoint requires audit: {partial_checkpoint}")
    allowed_existing = {checkpoint_path.name}
    unexpected = [path for path in output_dir.iterdir() if path.name not in allowed_existing]
    if unexpected:
        raise FileExistsError(f"refusing to overwrite private-BPE output with unexpected files: {unexpected}")
    corpus_path = PROJECT_ROOT / manifest["corpus_path"]
    training_sites = set(manifest["target_training_site_ids"])
    public_sites = set(manifest["public_candidate_site_ids"])
    if training_sites & public_sites:
        raise RuntimeError("public candidate and target-training sites overlap")
    all_texts = load_site_texts(corpus_path, training_sites | public_sites)
    training_texts = {site: all_texts[site] for site in training_sites}
    public_texts = {site: all_texts[site] for site in public_sites}
    vocab, merges = public_alphabet_base(public_texts)
    initial_vocab_size = len(vocab)
    requested_vocab_size = max(requested_vocab_size, initial_vocab_size)
    planned_merges = requested_vocab_size - initial_vocab_size
    planned_rounds = max(1, math.ceil(planned_merges / max(1, batch_size)))
    epsilon_round = (
        uniform_round_budget(float(epsilon_total), planned_rounds)
        if mode in {"local_dp", "sa_dp"}
        else None
    )
    rng = np.random.default_rng(int(manifest["seed"]) + requested_vocab_size * 1009)
    round_records: list[dict[str, Any]] = []
    run_started_at_utc = utc_now()
    started = time.perf_counter()
    prior_elapsed_seconds = 0.0
    prior_peak_memory_bytes = 0
    key_generation_seconds = 0.0
    crypto_totals: Counter[str] = Counter()
    if checkpoint_path.exists():
        checkpoint = strict_json_load(checkpoint_path)
        if checkpoint.get("training_key") != training_key:
            raise RuntimeError("private-BPE checkpoint parameters differ from requested run")
        vocab = {str(token): int(index) for token, index in checkpoint["vocab"].items()}
        merges = [(str(pair[0]), str(pair[1])) for pair in checkpoint["merges"]]
        round_records = list(checkpoint["rounds"])
        crypto_totals.update(checkpoint["crypto_totals"])
        rng.bit_generator.state = checkpoint["rng_state"]
        run_started_at_utc = str(checkpoint["started_at_utc"])
        prior_elapsed_seconds = float(checkpoint.get("accumulated_elapsed_seconds", 0.0))
        prior_peak_memory_bytes = int(checkpoint.get("peak_memory_bytes", 0))
        key_generation_seconds = float(checkpoint.get("paillier_key_generation_seconds", 0.0))

    base_tokenizer = tokenizer_from_state(*public_alphabet_base(public_texts))
    public_pair_corpus = IncrementalPairCorpus.from_site_texts(base_tokenizer, public_texts)
    training_pair_corpus = IncrementalPairCorpus.from_site_texts(base_tokenizer, training_texts)
    for record in round_records:
        replay_pairs = [
            (str(pair[0]), str(pair[1])) for pair in record["selected_merges"]
        ]
        public_pair_corpus.apply_compatible_merges(replay_pairs)
        training_pair_corpus.apply_compatible_merges(replay_pairs)

    def save_checkpoint(status: str = "running") -> None:
        payload = {
            "schema_version": 1,
            "status": status,
            "training_key": training_key,
            "completed_rounds": len(round_records),
            "planned_rounds": planned_rounds,
            "vocab": vocab,
            "merges": [[left, right] for left, right in merges],
            "rounds": round_records,
            "crypto_totals": dict(crypto_totals),
            "rng_state": rng.bit_generator.state,
            "started_at_utc": run_started_at_utc,
            "accumulated_elapsed_seconds": prior_elapsed_seconds + time.perf_counter() - started,
            "peak_memory_bytes": max(prior_peak_memory_bytes, int(peak_working_set_bytes() or 0)),
            "paillier_key_generation_seconds": key_generation_seconds,
            "updated_at_utc": utc_now(),
        }
        with partial_checkpoint.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(strict_json_dumps(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial_checkpoint, checkpoint_path)
    paillier_keys = None
    if real_paillier and mode in {"he_only", "sa_dp"}:
        key_started = time.perf_counter()
        paillier_keys = generate_keys(key_bits)
        key_generation_seconds += time.perf_counter() - key_started
    for round_index in range(len(round_records), planned_rounds):
        if len(vocab) >= requested_vocab_size:
            break
        candidates, candidate_meta = public_pair_corpus.candidates(vocab, candidate_pool_size)
        if not candidates:
            break
        _, vectors = training_pair_corpus.candidate_vectors(candidates)
        public_norms = candidate_meta.pop("public_site_l1_norms")
        clipping_bound = max(1, int(math.ceil(np.percentile(public_norms, clipping_percentile))))
        remaining = requested_vocab_size - len(vocab)
        selected_indices, crypto = aggregate_and_select(
            mode=mode,
            vectors=vectors,
            candidates=candidates,
            batch_size=min(batch_size, remaining),
            clipping_bound=clipping_bound,
            epsilon_round=epsilon_round,
            rng=rng,
            key_bits=key_bits,
            real_paillier=real_paillier,
            paillier_keys=paillier_keys,
            paillier_worker_processes=paillier_worker_processes,
        )
        selected_pairs = [candidates[index] for index in selected_indices]
        if not selected_pairs:
            break
        for left, right in selected_pairs:
            token = left + right
            if token in vocab:
                continue
            vocab[token] = len(vocab)
            merges.append((left, right))
        public_pair_corpus.apply_compatible_merges(selected_pairs)
        training_pair_corpus.apply_compatible_merges(selected_pairs)
        for key in (
            "encryption_seconds",
            "aggregation_seconds",
            "noise_encryption_seconds",
            "decryption_selection_seconds",
            "total_seconds",
            "client_ciphertext_count",
            "noise_l1",
        ):
            crypto_totals[key] += crypto[key]
        round_records.append(
            {
                "round": round_index,
                "candidate_pool_hash": candidate_meta["candidate_pool_hash"],
                "candidate_count": candidate_meta["candidate_count"],
                "clipping_bound": clipping_bound,
                "epsilon_round": epsilon_round,
                "selected_merge_count": len(selected_pairs),
                "selected_merges": [[left, right] for left, right in selected_pairs],
                "vocab_size_after_round": len(vocab),
                "crypto": crypto,
            }
        )
        if (round_index + 1) % max(1, checkpoint_every_rounds) == 0:
            save_checkpoint()
        if progress_callback is not None:
            progress_callback({
                "round": round_index + 1,
                "planned_rounds": planned_rounds,
                "vocab_size": len(vocab),
                "requested_vocab_size": requested_vocab_size,
                "candidate_count": candidate_meta["candidate_count"],
                "selected_merge_count": len(selected_pairs),
                "clipping_bound": clipping_bound,
                "elapsed_seconds": time.perf_counter() - started,
            })
    tokenizer = tokenizer_from_state(vocab, merges)
    partial = output_dir / "tokenizer.json.partial"
    if partial.exists() or artifact.exists():
        raise FileExistsError(f"private-BPE tokenizer output/partial already exists: {artifact}")
    tokenizer.save(str(partial))
    partial.rename(artifact)
    round_budgets = [float(epsilon_round)] * len(round_records) if epsilon_round else []
    accountant = basic_composition(round_budgets) if round_budgets else None
    metadata = {
        "schema_version": 1,
        "status": "success",
        "method": mode,
        "method_id": method_identifier,
        "training_key": training_key,
        "protocol_name": "SA-DP-BPE" if mode == "sa_dp" else mode,
        "scale": manifest["scale"],
        "seed": manifest["seed"],
        "manifest_path": str(manifest_path.relative_to(PROJECT_ROOT)),
        "manifest_sha256": manifest["manifest_sha256"],
        "corpus_sha256": manifest["corpus_sha256"],
        "public_candidate_site_count": len(public_sites),
        "target_training_site_count": len(training_sites),
        "public_target_overlap_count": len(public_sites & training_sites),
        "requested_vocab_size": requested_vocab_size,
        "requested_vocab_size_input": requested_vocab_size_input,
        "actual_vocab_size": len(vocab),
        "initial_vocab_size": initial_vocab_size,
        "initial_alphabet_source": "strictly disjoint public-candidate sites",
        "pre_tokenizer": "Whitespace",
        "pair_counting": "incremental_within_pretoken_boundaries",
        "merge_count": len(merges),
        "candidate_pool_size": candidate_pool_size,
        "clipping_percentile": clipping_percentile,
        "batch_size": batch_size,
        "planned_rounds": planned_rounds,
        "actual_rounds": len(round_records),
        "epsilon_total_requested": epsilon_total,
        "privacy_accountant": accountant,
        "adjacency": "add_remove_one_complete_site",
        "sensitivity_per_round": "recorded clipping_bound C_r",
        "integerization": "deterministic_largest_remainder",
        "dp_mechanism": "two_sided_geometric" if mode in {"local_dp", "sa_dp"} else None,
        "real_paillier": real_paillier and mode in {"he_only", "sa_dp"},
        "paillier_required_by_protocol": mode in {"he_only", "sa_dp"},
        "aggregation_execution": (
            "actual_paillier"
            if real_paillier and mode in {"he_only", "sa_dp"}
            else "protocol_equivalent_cleartext"
            if mode in {"he_only", "sa_dp"}
            else "plaintext"
        ),
        "paillier_key_bits": key_bits if real_paillier and mode in {"he_only", "sa_dp"} else None,
        "requested_paillier_key_bits": key_bits,
        "paillier_actual_modulus_bits": (
            paillier_keys.actual_modulus_bits if paillier_keys is not None else None
        ),
        "paillier_key_generation_seconds": key_generation_seconds,
        "paillier_worker_processes": paillier_worker_processes if real_paillier else None,
        "rounds": round_records,
        "crypto_totals": dict(crypto_totals),
        "started_at_utc": run_started_at_utc,
        "elapsed_seconds": prior_elapsed_seconds + time.perf_counter() - started,
        "peak_memory_bytes": max(prior_peak_memory_bytes, int(peak_working_set_bytes() or 0)),
        "artifact": str(artifact.relative_to(PROJECT_ROOT)),
        "artifact_sha256": sha256_file(artifact),
        "completed_at_utc": utc_now(),
        "environment": environment_metadata(),
        "checkpoint_reused": False,
    }
    write_json_exclusive(metadata_path, metadata)
    save_checkpoint("success")
    return metadata
