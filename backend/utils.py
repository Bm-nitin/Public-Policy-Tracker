import re
from difflib import SequenceMatcher


def clean_text(text):
    if not isinstance(text, str):
        return ""

    text = text.lower()

    # Keep letters and numbers
    text = re.sub(r'[^a-z0-9 ]', ' ', text)

    text = re.sub(r'\s+', ' ', text).strip()

    return text


def similarity_score(a, b):

    if not a or not b:
        return 0

    return SequenceMatcher(
        None,
        clean_text(a),
        clean_text(b)
    ).ratio()


def extract_keywords(text):

    words = clean_text(text).split()

    stopwords = {
        "what", "is", "the", "in", "of",
        "about", "tell", "me", "give",
        "latest", "new", "show", "all",
        "list", "information", "details",
        "please", "can", "you", "explain",
        "describe", "policy", "policies",
        "change", "changes"
    }

    return [
        word
        for word in words
        if word not in stopwords and len(word) > 2
    ]


def match_by_keywords(user_input, policies):

    keywords = extract_keywords(user_input)

    if not keywords:
        return None, 0

    best_match = None
    best_score = 0

    for policy in policies:

        policy_text = " ".join([
            policy.get("name", ""),
            policy.get("change", ""),
            policy.get("impact", ""),
            policy.get("category", ""),
            policy.get("sub_category", ""),
            policy.get("sector", "")
        ])

        policy_words = set(
            clean_text(policy_text).split()
        )

        matches = sum(
            1
            for keyword in keywords
            if keyword in policy_words
        )

        score = matches / len(keywords)

        if score > best_score:
            best_score = score
            best_match = policy

    # Weak matches are ignored
    if best_score < 0.20:
        return None, 0

    return best_match, best_score