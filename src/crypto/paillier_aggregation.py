"""Two-server Paillier additive aggregation with signed DP noise."""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Iterable, Sequence
from typing import Any

from phe import paillier


@dataclasses.dataclass(frozen=True)
class ProtocolKeys:
    public_key: paillier.PaillierPublicKey
    private_key: paillier.PaillierPrivateKey
    requested_bits: int

    @property
    def actual_modulus_bits(self) -> int:
        return self.public_key.n.bit_length()


def generate_keys(bits: int) -> ProtocolKeys:
    if bits < 1024:
        raise ValueError("Paillier prototype refuses keys below 1024 bits")
    public_key, private_key = paillier.generate_paillier_keypair(n_length=bits)
    return ProtocolKeys(public_key, private_key, bits)


class DataClient:
    """Client P_i: clips elsewhere, then encrypts every integer coordinate."""

    def __init__(self, public_key: paillier.PaillierPublicKey) -> None:
        self.public_key = public_key

    def encrypt_vector(self, values: Sequence[int]) -> list[paillier.EncryptedNumber]:
        return [self.public_key.encrypt(int(value), precision=1) for value in values]


class AggregationServer:
    """Server A owns only pk and never receives a decryption capability."""

    def __init__(self, public_key: paillier.PaillierPublicKey) -> None:
        self.public_key = public_key

    def aggregate(
        self, encrypted_vectors: Sequence[Sequence[paillier.EncryptedNumber]]
    ) -> list[paillier.EncryptedNumber]:
        if not encrypted_vectors:
            raise ValueError("at least one encrypted client vector is required")
        width = len(encrypted_vectors[0])
        if any(len(vector) != width for vector in encrypted_vectors):
            raise ValueError("encrypted vector dimensions differ")
        order = list(range(len(encrypted_vectors)))
        random.Random(0).shuffle(order)
        result = list(encrypted_vectors[order[0]])
        for client_index in order[1:]:
            result = [left + right for left, right in zip(result, encrypted_vectors[client_index])]
        return result

    def add_encrypted_noise(
        self,
        aggregate: Sequence[paillier.EncryptedNumber],
        noise: Sequence[int],
    ) -> list[paillier.EncryptedNumber]:
        if len(aggregate) != len(noise):
            raise ValueError("aggregate/noise dimensions differ")
        return [
            encrypted + self.public_key.encrypt(int(value), precision=1)
            for encrypted, value in zip(aggregate, noise)
        ]


class DecryptionSelectionServer:
    """Server D accepts only an aggregate vector and returns selected IDs."""

    def __init__(self, private_key: paillier.PaillierPrivateKey) -> None:
        self.__private_key = private_key

    def decrypt_aggregate(
        self, noisy_aggregate: Sequence[paillier.EncryptedNumber]
    ) -> list[int]:
        return [int(self.__private_key.decrypt(value)) for value in noisy_aggregate]

    def decrypt_and_select(
        self,
        noisy_aggregate: Sequence[paillier.EncryptedNumber],
        candidate_ids: Sequence[str],
        batch_size: int,
        candidate_pairs: Sequence[tuple[str, str]] | None = None,
    ) -> tuple[list[str], dict[str, Any]]:
        if len(noisy_aggregate) != len(candidate_ids):
            raise ValueError("aggregate/candidate dimensions differ")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        values = self.decrypt_aggregate(noisy_aggregate)
        ranked = sorted(range(len(values)), key=lambda index: (-values[index], index))
        selected: list[str] = []
        selected_lefts: set[str] = set()
        selected_rights: set[str] = set()
        selected_outputs: set[str] = set()
        for index in ranked:
            if candidate_pairs is not None:
                left, right = candidate_pairs[index]
                output = left + right
                if left in selected_rights or right in selected_lefts or output in selected_outputs:
                    continue
                selected_lefts.add(left)
                selected_rights.add(right)
                selected_outputs.add(output)
            selected.append(candidate_ids[index])
            if len(selected) >= batch_size:
                break
        return selected, {
            "decrypted_dimension": len(values),
            "returned_merge_id_count": len(selected),
            "full_frequency_vector_returned_to_A": False,
        }


def plaintext_aggregate(vectors: Iterable[Sequence[int]]) -> list[int]:
    materialized = [list(map(int, vector)) for vector in vectors]
    if not materialized:
        raise ValueError("at least one vector is required")
    width = len(materialized[0])
    if any(len(vector) != width for vector in materialized):
        raise ValueError("vector dimensions differ")
    return [sum(vector[index] for vector in materialized) for index in range(width)]
