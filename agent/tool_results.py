"""Normalize legacy tool failures at the adapter boundary."""
import re


def error_category(message):
    text = str(message).casefold()
    if re.search(r"timeout|timed out|connection|temporar|http (?:408|429|5\d\d)|超时|verbindung", text):
        return "transient"
    if "mehrdeutig" in text or "ambiguous" in text:
        return "ambiguous_reference"
    if re.search(r"crs|koordinatenreferenz|ziel-koordinaten|字段|attribut|feld|field|bitte|angegeben|keine numerische|weniger als|nicht vorhanden", text):
        return "needs_input"
    if "nicht gefunden" in text or "not found" in text:
        return "not_found"
    return "tool_error"
