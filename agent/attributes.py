"""Attribute semantics shared by agent summaries and visualization tools."""
import re


_NON_MEASURE_TOKENS = {
    "id", "identifier", "uuid", "guid", "fid", "code", "ref", "reference",
    "plz", "postcode", "postalcode", "postleitzahl", "zipcode", "zip",
    "hnr", "hnrz", "hausnummer", "hausnr", "housenumber", "houseno",
    "strassennummer", "strassennr", "streetnumber", "streetno", "buildingnumber",
    "buildingno", "apartmentnumber", "flatnumber", "doornumber",
}
_ID_SUFFIXES = ("objectid", "featureid", "recordid", "datasetid", "userid", "sourceid", "osm_id")
_NUMBER_CONTEXT_TOKENS = {"house", "haus", "street", "strasse", "building", "door", "apartment", "flat"}
_NUMBER_LABEL_TOKENS = {"number", "no", "nr", "nummer"}


def is_non_measure_attribute(field):
    """Identify numeric labels that encode identity or address, not magnitude."""
    raw_field = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(field))
    tokens = re.findall(r"[a-z0-9]+", raw_field.casefold())
    token_set = set(tokens)
    normalized = "".join(tokens)

    if token_set & _NON_MEASURE_TOKENS:
        return True
    if normalized.endswith(_ID_SUFFIXES) or normalized.startswith("id"):
        return True
    if token_set & _NUMBER_CONTEXT_TOKENS and token_set & _NUMBER_LABEL_TOKENS:
        return True
    if token_set & {"postal", "zip", "postcode", "postalcode"}:
        return True
    return False
