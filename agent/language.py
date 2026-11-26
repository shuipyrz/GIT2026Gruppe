"""Reply language selection independent of German tool reports."""
import re


LANGUAGE_NAMES = {"zh": "Simplified Chinese", "de": "German", "en": "English"}


def language_preference(text):
    patterns = {
        "zh": r"(?:以后|之后|一律|一直|请|用|使用).{0,20}中文.{0,10}(?:回答|回复)?|(?:reply|respond|answer).{0,20}(?:in )?chinese",
        "de": r"(?:以后|之后|一律|请|用|使用).{0,20}德语|(?:reply|respond|answer).{0,20}(?:in )?german|antworte.{0,15}deutsch",
        "en": r"(?:以后|之后|一律|请|用|使用).{0,20}英语|(?:reply|respond|answer).{0,20}(?:in )?english",
    }
    return next((language for language, pattern in patterns.items() if re.search(pattern, text, re.I)), None)


def detect_language(text, previous="de"):
    if re.search(r"[\u3400-\u9fff]", text):
        return "zh"
    if re.search(r"\b(?:bitte|zeige|erstell\w*|welche\w*|wie|warum|karte|berechne|weiter|fortsetzen|deutsch|entferne)\b|[äöüß]", text, re.I):
        return "de"
    if re.search(r"\b(?:show|create|export|how|what|which|why|use|continue|retry|filter|then|remove|hide|draw|calculate)\b", text, re.I):
        return "en"
    return previous or "de"


def language_instruction(language):
    name = LANGUAGE_NAMES.get(language, "German")
    return (f"MANDATORY RESPONSE LANGUAGE: {name}. All explanations, questions and summaries must use {name}, "
            "even when tool results or previous assistant messages are German. Translate tool reports into this language. "
            "Keep dataset names, field names and quoted titles verbatim. This overrides earlier language instructions.")
