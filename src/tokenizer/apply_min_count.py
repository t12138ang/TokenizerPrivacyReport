"""Train the Gate 2 post-hoc Min-count BPE baseline from a manifest.

The implementation counts tokens on the selected training sites and removes
tokens below the configured occurrence threshold, following the official
defense's core rule.  Serialization and checkpoint safeguards are independent.
"""

from __future__ import annotations

from src.tokenizer.train_target import main


if __name__ == "__main__":
    raise SystemExit(main())
