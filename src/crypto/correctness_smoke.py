"""Persisted 1024-bit development-only Paillier correctness checks."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from src.crypto.paillier_aggregation import (
    AggregationServer,
    DataClient,
    DecryptionSelectionServer,
    generate_keys,
    plaintext_aggregate,
)
from src.utils.run_metadata import environment_metadata, peak_working_set_bytes, strict_json_load, utc_now, write_json_exclusive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output.resolve()
    if output.exists():
        existing = strict_json_load(output)
        if existing.get("status") == "success":
            print(f"checkpoint_reused=true output={output}")
            return 0
        raise FileExistsError(f"refusing to overwrite correctness result: {output}")
    started = time.perf_counter()
    keys = generate_keys(1024)
    client = DataClient(keys.public_key)
    server_a = AggregationServer(keys.public_key)
    server_d = DecryptionSelectionServer(keys.private_key)
    cases = [
        ([[3, 0, 7], [2, 5, 1], [9, 2, 0]], [-4, 3, -2]),
        ([[0, 0], [0, 0]], [-9, 11]),
        ([[2**48, 2**40], [2**47, 2**39]], [-(2**32), 2**31]),
    ]
    records = []
    for case_id, (vectors, noise) in enumerate(cases):
        encrypted = [client.encrypt_vector(vector) for vector in vectors]
        noisy = server_a.add_encrypted_noise(server_a.aggregate(encrypted), noise)
        decrypted = server_d.decrypt_aggregate(noisy)
        expected = [value + delta for value, delta in zip(plaintext_aggregate(vectors), noise)]
        max_error = max(abs(left - right) for left, right in zip(decrypted, expected))
        overflow = max(map(abs, expected)) > keys.public_key.max_int
        records.append({
            "case_id": case_id,
            "plaintext_aggregate": expected,
            "decrypted_he_aggregate": decrypted,
            "equality": expected == decrypted,
            "max_absolute_error": max_error,
            "overflow_flag": overflow,
        })
    if not all(record["equality"] and record["max_absolute_error"] == 0 and not record["overflow_flag"] for record in records):
        raise AssertionError("1024-bit Paillier smoke correctness failed")
    result = {
        "schema_version": 1,
        "status": "success",
        "development_only": True,
        "requested_key_bits": 1024,
        "actual_modulus_bits": keys.actual_modulus_bits,
        "signed_integer_decode_tested": True,
        "client_order_invariance_tested_in_unit_suite": True,
        "server_a_has_private_key": hasattr(server_a, "private_key"),
        "server_a_has_decrypt_method": hasattr(server_a, "decrypt"),
        "server_d_api_received_individual_client_ciphertexts": False,
        "cases": records,
        "elapsed_seconds": time.perf_counter() - started,
        "peak_memory_bytes": peak_working_set_bytes(),
        "completed_at_utc": utc_now(),
        "environment": environment_metadata(),
    }
    write_json_exclusive(output, result)
    print(f"status=success cases={len(records)} max_absolute_error=0 output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
