"""
Shared filters for reducing noise in retrieval results.
"""

from __future__ import annotations

import re


_TEST_PATTERNS = re.compile(
    r'[\\/](tests?|conftest|benchmarks|asv_benchmarks|examples)[\\/]'
    r'|[\\/]test_[^\\/]*\.py$'
    r'|_test\.py$',
    re.IGNORECASE,
)


def is_test_file(path: str) -> bool:
    """Return True if path looks like a test/benchmark/example file."""
    return bool(_TEST_PATTERNS.search(path))
