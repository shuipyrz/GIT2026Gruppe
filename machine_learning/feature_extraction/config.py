"""Feature schema and embedding defaults."""

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIMENSION = 384
TYPE_ORDER = ("integer", "float", "string", "boolean", "datetime")
NUMERIC_FEATURES = ("mean", "std", "min", "max", "q25", "q50", "q75")
TEXT_FEATURES = ("mean_text_length", "max_text_length", "mean_word_count")
FEATURE_DIMENSION = 412
# Applicable but uncomputable values use zero; applicability bits distinguish
# these from not-applicable statistics, and general missing ratio records gaps.
UNCOMPUTABLE_VALUE = 0.0
