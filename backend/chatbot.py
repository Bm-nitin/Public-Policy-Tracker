from utils import clean_text, similarity_score, match_by_keywords
from policy_loader import load_policies
from google import genai
from config import Config
import re

# API_KEY / client are kept as module-level names (rather than only
# living on Config) because call_generative_ai() below reads them
# directly, and the Phase 0.5 test suite monkeypatches
# chatbot.API_KEY / chatbot.client to simulate missing-key and
# mocked-Gemini scenarios without any network access.
API_KEY = Config.GEMINI_API_KEY

client = None

if API_KEY:
    client = genai.Client(api_key=API_KEY)


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
    if not API_KEY or client is None:
        print("[AI ERROR] Gemini API key is missing")
        return (
            "AI assistance is currently unavailable. "
            "Please try a policy-related question from the available sectors."
        )

    try:
        print(f"[AI REQUEST] {user_input}")

        response = client.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=f"""
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
            "Please try a policy-related question from the available sectors."
        )


def get_grounded_response(user_input):
    """Phase 11 - the grounded Gemini response layer.

    Orchestration only (per the Phase 11 brief's architecture
    requirement) - this function's own logic is just "call retrieval,
    call grounding, call gemini_service, decide what to do with each
    outcome"; it contains no retrieval logic (backend/retrieval.py), no
    context-formatting logic (backend/grounding.py), and no Gemini
    request/response handling (backend/gemini_service.py) of its own.

    Desired flow (Phase 11 brief):
        user query -> hybrid_retrieve() -> retrieved policy records
        -> grounding/context builder -> Gemini 2.5 Flash -> grounded response

    Three distinct outcomes, in order:

    1. hybrid_retrieve() finds nothing at all (deterministic AND semantic
       both empty, or retrieval itself raised - see the try/except
       below): returns grounding.INSUFFICIENT_CONTEXT_MESSAGE directly.
       Gemini is NEVER called in this case - there being no policy
       context to ground on is exactly the case the Phase 11 brief says
       must not reach Gemini with an empty context.
    2. hybrid_retrieve() finds something and Gemini succeeds: returns
       Gemini's grounded answer (cleaned via the existing
       clean_ai_response() - the same citation-artifact cleanup
       call_generative_ai() already applies, reused rather than
       duplicated).
    3. hybrid_retrieve() finds something but Gemini is unavailable or
       fails (GeminiServiceError, or any other exception - never allowed
       to propagate and crash /chat): falls back to the same deterministic
       format_response()/format_multiple() formatting step 5 above uses
       for a direct match - the Phase 11 brief's required "safe
       deterministic fallback based on retrieved policy records". The
       caller cannot tell, from the response text alone, whether this
       came from Gemini or from the deterministic fallback - both are
       genuine, non-fabricated answers grounded in the same retrieved
       records either way.

    This is a distinct, separate capability from the existing (Phase 0.5)
    call_generative_ai() - that function remains exactly as it was, still
    used nowhere in this new flow. The two serve different purposes:
    call_generative_ai() gives an ungrounded, general-knowledge answer to
    a policy-topic question with NO retrieved context and no
    system_instruction (still directly callable/tested - see
    tests/test_gemini_mocked.py - even though get_response() itself no
    longer calls it); get_grounded_response() answers ONLY from actual
    retrieved policy records and explicitly refuses to answer when there
    are none, per the Phase 11 brief's anti-hallucination requirements.
    Keeping call_generative_ai() unused-but-present, rather than deleting
    it, avoids touching Phase 0.5 behavior that isn't part of this phase.
    """
    from grounding import (
        GROUNDING_SYSTEM_INSTRUCTION,
        INSUFFICIENT_CONTEXT_MESSAGE,
        MAX_POLICIES_IN_CONTEXT,
        build_context,
    )

    try:
        from retrieval import hybrid_retrieve
        results = hybrid_retrieve(user_input)
    except Exception:
        print("[GROUNDED RESPONSE] retrieval failed, no context available")
        results = []

    if not results:
        return INSUFFICIENT_CONTEXT_MESSAGE

    context = build_context(results[:MAX_POLICIES_IN_CONTEXT])

    try:
        from gemini_service import GeminiServiceError, generate_grounded_response
        reply = generate_grounded_response(user_input, context, GROUNDING_SYSTEM_INSTRUCTION)
        return clean_ai_response(reply)
    except GeminiServiceError as e:
        print("[GROUNDED RESPONSE] Gemini unavailable, using deterministic fallback:", str(e))
    except Exception as e:  # noqa: BLE001 - never let a Gemini-layer problem crash /chat
        print("[GROUNDED RESPONSE] unexpected error, using deterministic fallback:", str(e))

    if len(results) == 1:
        return format_response(results[0])
    return format_multiple(results[:3], results[0]["sector"])


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
    # 5. RETRIEVAL V2 + SEMANTIC SEARCH (hybrid_retrieve - Phase 9/10)
    # --------------------------------------------------
    # ARCHITECTURE CHANGE (Phase 11): uses hybrid_retrieve() (deterministic
    # Retrieval V2 first, then semantic search filling any remaining slots
    # - see backend/retrieval.py's hybrid_retrieve() docstring) rather than
    # retrieve_policies() alone, per the Phase 11 brief. This changes
    # nothing about the deterministic-match case: hybrid_retrieve() always
    # returns Retrieval V2's own results first, unmodified and in the same
    # order, so any query retrieve_policies() alone would have matched
    # still hits this branch and still returns the exact same raw
    # format_response()/format_multiple() text with NO Gemini call - the
    # only behavior this adds is that a query deterministic Retrieval V2
    # finds nothing for may now be answered here too, if semantic search
    # has something. Neither retrieval path calls Gemini or influences a
    # Gemini prompt at this step - see get_grounded_response() below for
    # where Gemini actually enters the picture (only once this step has
    # already found nothing at all).
    try:
        from retrieval import hybrid_retrieve
        results = hybrid_retrieve(user_input)
    except Exception:
        # Never let a retrieval-layer problem take down /chat - degrade
        # to the grounded Gemini fallback below. Logged server-side only
        # (see CURRENT_ARCHITECTURE.md's note on this app's existing
        # print-based error logging - not a new pattern).
        print("[RETRIEVAL ERROR] falling back to grounded Gemini response")
        results = []

    if results:
        if len(results) == 1:
            return format_response(results[0])
        return format_multiple(results[:3], results[0]["sector"])


    # --------------------------------------------------
    # 6. GROUNDED GEMINI RESPONSE (Phase 11)
    # --------------------------------------------------
    # Only reached once step 5 has already found nothing at all (neither
    # deterministic nor semantic). See get_grounded_response() below for
    # the full grounding/fallback strategy - in short: re-runs
    # hybrid_retrieve() (cheap - see that function's docstring; the
    # earlier call above found nothing, so this will too, but
    # get_grounded_response() is written to be callable on its own, not
    # only from here - see backend/tests/test_grounded_responses.py),
    # builds bounded grounding context from whatever it finds, and asks
    # Gemini to answer using ONLY that context - never Gemini's own
    # general knowledge (unlike the old call_generative_ai() fallback
    # this replaces here, which remains available as its own function
    # and its own tests - see get_grounded_response()'s docstring for
    # exactly why both still exist).
    return get_grounded_response(user_input)


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