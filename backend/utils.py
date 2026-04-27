import re
from difflib import SequenceMatcher

def clean_text(text):
    if not isinstance(text, str):
        return ""

    text = text.lower()
    text = re.sub(r'[^a-z0-9 ]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def similarity_score(a, b):
    if not a or not b:
        return 0

    return SequenceMatcher(None, a, b).ratio()


def extract_keywords(text):
    words = clean_text(text).split()

    stopwords = {
        "what", "is", "the", "in", "of", "about",
        "tell", "me", "give", "latest", "new",
        "policy", "change", "show", "all", "list",
        "information", "details"
    }

    keywords = [word for word in words if word not in stopwords and len(word) > 2]

    return keywords


def match_by_keywords(user_input, policies):
    keywords = extract_keywords(user_input)

    if len(keywords) == 0:
        return None, 0

    best_match = None
    best_score = 0

    for policy in policies:
        text = " ".join([
            policy.get("name", ""),
            policy.get("change", ""),
            policy.get("impact", "")
        ])

        text = clean_text(text)
        text_words = set(text.split())

        match_count = sum(1 for word in keywords if word in text_words)

        score = match_count / len(keywords)

        if score > best_score:
            best_score = score
            best_match = policy

    if best_score < 0.2:
        return None, 0

    return best_match, best_score