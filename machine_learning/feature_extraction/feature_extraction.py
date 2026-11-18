"""Create one 412-dimensional node feature row for each data property."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import EMBEDDING_DIMENSION, FEATURE_DIMENSION, TYPE_ORDER
from .data_type import infer_data_type
from .embeddings import embed_names
from .statistics import field_statistics

FEATURE_NAMES = tuple(
    [f"name_embedding_{i:03d}" for i in range(EMBEDDING_DIMENSION)]
    + [f"type_{name}" for name in TYPE_ORDER]
    + ["missing_ratio", "unique_ratio", "number_of_values"]
    + [f"numeric_{n}" for n in ("mean", "std", "min", "max", "q25", "q50", "q75")]
    + [f"text_{n}" for n in ("mean_length", "max_length", "mean_word_count")]
    + [f"applicable_numeric_{n}" for n in ("mean", "std", "min", "max", "q25", "q50", "q75")]
    + [f"applicable_text_{n}" for n in ("mean_length", "max_length", "mean_word_count")]
)


def _load_table(source):
    if isinstance(source, pd.DataFrame):
        return source.copy()
    if isinstance(source, dict):
        data = source
    else:
        path = Path(source)
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path)
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        rows = [f.get("properties") or {} for f in data.get("features", [])]
        return pd.DataFrame(rows)
    if isinstance(data, dict) and data.get("type") == "Feature":
        return pd.DataFrame([data.get("properties") or {}])
    if isinstance(data, dict) and "properties" in data and isinstance(data["properties"], dict):
        data = data["properties"]
    if isinstance(data, dict):
        return pd.DataFrame(data if data and all(isinstance(v, (list, tuple)) for v in data.values()) else [data])
    if isinstance(data, list):
        return pd.DataFrame(data)
    raise ValueError("Expected a GeoJSON/JSON object, CSV path, or DataFrame.")


def extract_features(source, *, model_name=None, model=None):
    """Return (X, original field names, feature names, inferred field types)."""
    from .config import EMBEDDING_MODEL
    frame = _load_table(source)
    names = [str(c) for c in frame.columns]
    if not names:
        return np.empty((0, FEATURE_DIMENSION), dtype=np.float32), names, FEATURE_NAMES, {}
    embeddings = embed_names(names, model_name or EMBEDDING_MODEL, model=model)
    rows, types = [], {}
    for name, column in zip(names, frame.columns):
        values = frame[column].tolist()
        kind = infer_data_type(values)
        types[name] = kind
        one_hot = [float(kind == item) for item in TYPE_ORDER]
        general, numeric, textual, indicators = field_statistics(values, kind)
        rows.append(np.concatenate([embeddings[len(rows)], one_hot, general, numeric, textual, indicators]))
    X = np.asarray(rows, dtype=np.float32)
    if X.shape != (len(names), FEATURE_DIMENSION):
        raise AssertionError(f"Feature schema produced unexpected shape {X.shape}.")
    if not np.isfinite(X).all():
        raise ValueError("Feature matrix contains NaN or Inf.")
    return X, names, FEATURE_NAMES, types
