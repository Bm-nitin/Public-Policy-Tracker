from utils import clean_text, similarity_score, match_by_keywords
from policy_loader import load_policies
import google.generativeai as genai
from dotenv import load_dotenv
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(dotenv_path=ENV_PATH)

API_KEY = os.getenv("API_KEY")

if API_KEY:
    genai.configure(api_key=API_KEY)

policies = load_policies()


# Category aliases
CATEGORY_KEYWORDS = {
    "education": [
        "education", "educational", "school",
        "college", "student", "students", "learning"
    ],

    "healthcare": [
        "health", "healthcare", "medical",
        "hospital", "hospitals", "doctor",
        "medicine", "healthcare"
    ],

    "technology": [
        "technology", "tech", "ai", "artificial",
        "intelligence", "digital", "cyber", "internet"
    ],

    "environment": [
        "environment", "environmental", "climate",
        "pollution", "forest", "sustainability"
    ],

    "economy": [
        "economy", "economic", "finance",
        "financial", "bank", "banking", "tax"
    ],

    "agriculture": [
        "agriculture", "agricultural",
        "farmer", "farmers", "crop", "farming"
    ],

    "employment_skill": [
        "employment", "job", "jobs", "skill",
        "skills", "worker", "workers", "labour"
    ],

    "social_welfare": [
        "social", "welfare", "poverty",
        "pension", "benefit", "benefits"
    ],

    "infrastructure_transport": [
        "infrastructure", "transport",
        "road", "roads", "railway", "railways",
        "metro", "highway"
    ],

    "industry_business": [
        "industry", "industrial", "business",
        "startup", "startups", "company", "companies"
    ],

    "energy": [
        "energy", "electricity", "power",
        "renewable", "solar"
    ],

    "governance_legal": [
        "governance", "legal", "law",
        "laws", "government", "administration"
    ],

    "science_innovation": [
        "science", "research", "innovation",
        "scientific", "technology"
    ],

    "culture_tourism": [
        "culture", "tourism", "tourist",
        "heritage", "travel"
    ],

    "banking": [
        "banking", "bank", "banks",
        "financial", "finance"
    ]
}


def detect_category(user_input):
    """
    Detect category from the user's words.
    Uses aliases and requires only one meaningful category keyword.
    """

    words = set(clean_text(user_input).split())

    if not words:
        return None

    scores = {}

    for category, keywords in CATEGORY_KEYWORDS.items():
        scores[category] = sum(
            1 for keyword in keywords
            if keyword in words
        )

    best_category = max(scores, key=scores.get)

    if scores[best_category] >= 1:
        return best_category

    return None


def get_category_policies(category):
    """
    Get policies using the sector assigned by policy_loader.
    This is much more reliable than checking whether the category
    word appears inside the policy name.
    """

    category_clean = clean_text(category).replace(" ", "_")

    related = []

    for policy in policies:
        sector = clean_text(
            policy.get("sector", "")
        ).replace(" ", "_")

        if sector == category_clean:
            related.append(policy)

    return related


def clean_ai_response(text):
    """
    Remove unwanted citation/reference artifacts from AI output.
    """

    if not text:
        return ""

    # Remove contentReference artifacts
    text = re.sub(
        r'\\?:contentReference\[[^\]]*\]\{[^}]*\}',
        '',
        text,
        flags=re.IGNORECASE
    )

    # Remove any remaining contentReference fragments
    text = re.sub(
        r'\\?:contentReference[^\s]*',
        '',
        text,
        flags=re.IGNORECASE
    )

    # Remove excessive blank lines
    text = re.sub(r'\n\s*\n+', '\n\n', text)

    return text.strip()


def call_generative_ai(user_input):
    """
    Gemini fallback.

    If Gemini is unavailable or the API key is invalid,
    return a clean user-friendly message instead of exposing
    the raw Google API error.
    """

    if not API_KEY:
        print("[AI ERROR] API_KEY is missing")
        return (
            "AI assistance is currently unavailable. "
            "Please ask about a government policy or scheme "
            "from the available sectors."
        )

    try:
        print(f"[AI REQUEST] {user_input}")

        model = genai.GenerativeModel("gemini-flash-latest")

        response = model.generate_content(
            f"""
You are a public policy assistant.

Answer only questions related to:
- government policies
- government schemes
- laws and regulations
- public administration
- policy impacts
- policy changes
- public-sector technology and governance

Keep the answer concise and informative.

User question:
{user_input}
"""
        )

        raw = getattr(response, "text", None)

        if not raw:
            return (
                "I could not generate a response. "
                "Please ask a public-policy-related question."
            )

        return clean_ai_response(raw)

    except Exception as e:
        print("[AI ERROR]", str(e))

        return (
            "AI assistance is currently unavailable. "
            "Please try a policy-related question from the "
            "available sectors."
        )


def is_policy_related(user_input):
    """
    Basic policy relevance check.
    """

    text = clean_text(user_input)
    words = set(text.split())

    policy_keywords = {
        "policy", "policies", "scheme", "schemes",
        "government", "law", "laws", "regulation",
        "regulations", "act", "governance",
        "agriculture", "farmer", "health", "healthcare",
        "education", "technology", "economy",
        "environment", "employment", "bank",
        "banking", "tax", "insurance", "energy",
        "transport", "infrastructure", "welfare",
        "industry", "business", "tourism"
    }

    if words.intersection(policy_keywords):
        return True

    # Also allow category keywords
    for keywords in CATEGORY_KEYWORDS.values():
        if words.intersection(set(keywords)):
            return True

    return False


def get_response(user_input):

    user_input_clean = clean_text(user_input)

    if not user_input_clean:
        return "Please enter a question."


    # --------------------------------------------------
    # 1. CATEGORY MATCH
    # --------------------------------------------------

    category = detect_category(user_input)

    if category:

        related = get_category_policies(category)

        if related:
            return format_multiple(
                related[:3],
                category
            )


    # --------------------------------------------------
    # 2. DIRECT POLICY NAME MATCH
    # --------------------------------------------------

    best_match = None
    best_score = 0

    for policy in policies:

        name_clean = clean_text(
            policy.get("name", "")
        )

        score = similarity_score(
            user_input_clean,
            name_clean
        )

        if score > best_score:
            best_score = score
            best_match = policy

    # Reduced from 0.8 to 0.65
    if best_match and best_score >= 0.65:
        return format_response(best_match)


    # --------------------------------------------------
    # 3. KEYWORD MATCH
    # --------------------------------------------------

    kw_match, kw_score = match_by_keywords(
        user_input,
        policies
    )

    # Reduced from 0.5 to 0.30
    if kw_match and kw_score >= 0.30:
        return format_response(kw_match)


    # --------------------------------------------------
    # 4. NON-POLICY QUESTION
    # --------------------------------------------------

    if not is_policy_related(user_input):

        return (
            "I am a Public Policy Assistant. "
            "Please ask about government policies, schemes, "
            "laws, regulations, or their impacts."
        )


    # --------------------------------------------------
    # 5. GEMINI FALLBACK
    # --------------------------------------------------

    return call_generative_ai(user_input)


def format_response(policy):

    return (
        f"Policy: {policy.get('name', 'Unknown Policy')}\n\n"
        f"Change: {policy.get('change', 'No change information available.')}\n\n"
        f"Impact: {policy.get('impact', 'No impact information available.')}"
    )


def format_multiple(policy_list, category):

    display_name = category.replace("_", " ").title()

    response = f"Showing {display_name} policies:\n\n"

    for policy in policy_list:

        name = clean_ai_response(
            policy.get("name", "Unknown Policy")
        )

        impact = clean_ai_response(
            policy.get("impact", "No impact information available.")
        )

        response += f"• {name}\n"
        response += f"  → {impact}\n\n"

    return response.strip()