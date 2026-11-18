"""General, numerical and textual field statistics."""

import numpy as np
import pandas as pd

from .config import NUMERIC_FEATURES, TEXT_FEATURES, UNCOMPUTABLE_VALUE
from .data_type import _is_missing


def field_statistics(values, field_type: str):
    raw = list(values)
    valid = [v for v in raw if not _is_missing(v)]
    total, count = len(raw), len(valid)
    general = [1.0 - count / total if total else 0.0,
               len({str(v) for v in valid}) / count if count else 0.0,
               float(count)]
    numerical = [UNCOMPUTABLE_VALUE] * len(NUMERIC_FEATURES)
    textual = [UNCOMPUTABLE_VALUE] * len(TEXT_FEATURES)
    numeric_applicable = field_type in ("integer", "float")
    text_applicable = field_type == "string"
    if numeric_applicable and count:
        nums = pd.to_numeric(pd.Series(valid), errors="coerce").dropna().to_numpy(dtype=float)
        nums = nums[np.isfinite(nums)]
        if nums.size:
            numerical = [float(np.mean(nums)), float(np.std(nums, ddof=1)) if nums.size > 1 else 0.0,
                         float(np.min(nums)), float(np.max(nums)), *map(float, np.quantile(nums, [0.25, 0.5, 0.75]))]
    if text_applicable and count:
        strings = [str(v) for v in valid]
        lengths = [len(v) for v in strings]
        words = [len(v.split()) for v in strings]
        textual = [float(np.mean(lengths)), float(max(lengths)), float(np.mean(words))]
    indicators = [float(numeric_applicable)] * 7 + [float(text_applicable)] * 3
    return general, numerical, textual, indicators
