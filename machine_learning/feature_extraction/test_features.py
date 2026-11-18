"""Run feature extraction and export the complete matrix to CSV.

Examples:
    python -m machine_learning.feature_extraction.test_features
    python -m machine_learning.feature_extraction.test_features data.geojson
    python -m machine_learning.feature_extraction.test_features data.json -o features.csv
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .feature_extraction import FEATURE_DIMENSION, extract_features

DEFAULT_SAMPLE = Path(r"C:\Users\28472\Desktop\ney way\Neues Leben\TU Dresden\Kurse\KI-basierte Geoinformationsdienste\WiSe\TestDaten\260703\Arbeislosigkeit.geojson")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Extract field features and save the complete matrix as CSV.")
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_SAMPLE,
                        help="Input GeoJSON, JSON, or CSV file (defaults to the supplied test data).")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output CSV path (defaults to <input_stem>_feature_matrix.csv in the current directory).")
    args = parser.parse_args(argv)

    X, names, feature_names, types = extract_features(args.input)
    assert X.shape == (len(names), FEATURE_DIMENSION)
    assert len(feature_names) == FEATURE_DIMENSION
    assert np.isfinite(X).all()
    output = args.output or Path.cwd() / f"{args.input.stem}_feature_matrix.csv"

    # Use semantic feature labels as CSV columns and retain source field names as row labels.
    matrix = pd.DataFrame(X, index=pd.Index(names, name="field_name"), columns=feature_names)
    matrix.to_csv(output, encoding="utf-8-sig", float_format="%.8g")

    print(f"Input: {args.input}")
    print(f"Fields ({len(names)}): {names}")
    print(f"Inferred types: {types}")
    print(f"Feature matrix shape: {X.shape}")
    print(f"Finite values (no NaN/Inf): {bool(np.isfinite(X).all())}")
    print(f"Complete 412-dimensional matrix saved to: {output.resolve()}")
    print("Embedding columns: name_embedding_000 through name_embedding_383")


if __name__ == "__main__":
    main()
