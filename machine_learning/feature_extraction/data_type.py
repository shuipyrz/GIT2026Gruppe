"""Infer one of the five supported property types from its observed values."""

from datetime import date, datetime
import re

import numpy as np
import pandas as pd

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ].*)?$")


def infer_data_type(values) -> str:
    valid = [v for v in values if not _is_missing(v)]
    if not valid:
        return "string"
    if all(isinstance(v, (bool, np.bool_)) for v in valid):
        return "boolean"
    if all(isinstance(v, (int, np.integer)) and not isinstance(v, (bool, np.bool_)) for v in valid):
        return "integer"
    if all(isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_)) for v in valid):
        return "float"
    if all(isinstance(v, (date, datetime, pd.Timestamp, np.datetime64)) for v in valid):
        return "datetime"
    if all(isinstance(v, str) and _ISO_DATE.match(v.strip()) and not pd.isna(pd.to_datetime(v, errors="coerce")) for v in valid):
        return "datetime"
    # Numeric strings are intentionally strings: identifiers such as "01" retain meaning.
    return "string"


def _is_missing(value) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False
