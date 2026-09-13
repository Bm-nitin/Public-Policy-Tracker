"""
Phase 8: GET /api/policies, GET /api/policies/<id>, GET /api/policies/sectors,
GET /api/policies/categories.

Uses policies_client (see conftest.py) - an isolated in-memory database
per test, pre-populated with the REAL 151-record dataset via the actual
import pipeline (not synthetic fixtures), so these tests exercise real
data end to end. See conftest.py's flask_sqlalchemy shim docstring for
what is/isn't validated in this offline sandbox vs. a real environment
with flask-sqlalchemy + PostgreSQL installed.
"""


# --- 1-4: basic list behavior, pagination metadata, defaults --------------

def test_list_policies_returns_200(policies_client):
    response = policies_client.get("/api/policies")
    assert response.status_code == 200


def test_list_policies_response_contains_pagination_metadata(policies_client):
    body = policies_client.get("/api/policies").get_json()
    assert "data" in body
    assert "pagination" in body
    pagination = body["pagination"]
    assert set(pagination.keys()) == {"page", "per_page", "total", "pages"}


def test_default_pagination_uses_page_1_per_page_20(policies_client):
    body = policies_client.get("/api/policies").get_json()
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["per_page"] == 20
    assert len(body["data"]) == 20
    assert body["pagination"]["total"] == 151
    assert body["pagination"]["pages"] == 8  # ceil(151 / 20)


def test_custom_page_and_per_page_work(policies_client):
    body = policies_client.get("/api/policies?page=2&per_page=10").get_json()
    assert body["pagination"]["page"] == 2
    assert body["pagination"]["per_page"] == 10
    assert len(body["data"]) == 10


def test_last_page_returns_partial_results(policies_client):
    # 151 records, per_page=20 -> page 8 has 11 records (151 - 7*20)
    body = policies_client.get("/api/policies?page=8&per_page=20").get_json()
    assert len(body["data"]) == 11


def test_page_beyond_available_data_returns_empty_data_not_error(policies_client):
    response = policies_client.get("/api/policies?page=999&per_page=20")
    assert response.status_code == 200
    body = response.get_json()
    assert body["data"] == []
    assert body["pagination"]["total"] == 151


# --- 5: per_page maximum / invalid values rejected cleanly -----------------

def test_per_page_above_maximum_is_rejected_cleanly(policies_client):
    response = policies_client.get("/api/policies?per_page=1000")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_negative_page_is_rejected_cleanly(policies_client):
    response = policies_client.get("/api/policies?page=-1")
    assert response.status_code == 400


def test_non_numeric_page_is_rejected_cleanly_not_500(policies_client):
    response = policies_client.get("/api/policies?page=abc")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid page"}


def test_non_numeric_per_page_is_rejected_cleanly_not_500(policies_client):
    response = policies_client.get("/api/policies?per_page=abc")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid per_page"}


def test_zero_per_page_is_rejected_cleanly(policies_client):
    response = policies_client.get("/api/policies?per_page=0")
    assert response.status_code == 400


def test_per_page_exactly_at_maximum_is_accepted(policies_client):
    response = policies_client.get("/api/policies?per_page=100")
    assert response.status_code == 200
    assert response.get_json()["pagination"]["per_page"] == 100


# --- 6-9: sector, category, sub_category filtering; combined ---------------

def test_sector_filtering_works(policies_client):
    body = policies_client.get("/api/policies?sector=agriculture&per_page=100").get_json()
    assert body["pagination"]["total"] == 10
    assert all(p["sector"] == "agriculture" for p in body["data"])


def test_category_filtering_works(policies_client):
    # Pick a real category value from the actual data first.
    sample = policies_client.get("/api/policies?per_page=1").get_json()["data"][0]
    category = sample["category"]

    body = policies_client.get(f"/api/policies?category={category}&per_page=100").get_json()
    assert body["pagination"]["total"] >= 1
    assert all(p["category"] == category for p in body["data"])


def test_sub_category_filtering_works(policies_client):
    sample = policies_client.get("/api/policies?per_page=1").get_json()["data"][0]
    sub_category = sample["sub_category"]

    body = policies_client.get(f"/api/policies?sub_category={sub_category}&per_page=100").get_json()
    assert body["pagination"]["total"] >= 1
    assert all(p["sub_category"] == sub_category for p in body["data"])


def test_combined_sector_and_category_filters_work(policies_client):
    sample = policies_client.get("/api/policies?sector=agriculture&per_page=1").get_json()["data"][0]
    category = sample["category"]

    body = policies_client.get(
        f"/api/policies?sector=agriculture&category={category}&per_page=100"
    ).get_json()
    assert body["pagination"]["total"] >= 1
    for policy in body["data"]:
        assert policy["sector"] == "agriculture"
        assert policy["category"] == category


def test_unknown_filter_value_returns_empty_not_error(policies_client):
    response = policies_client.get("/api/policies?sector=not-a-real-sector")
    assert response.status_code == 200
    body = response.get_json()
    assert body["data"] == []
    assert body["pagination"]["total"] == 0


# --- 10-12: GET /api/policies/<id> ------------------------------------------

def test_get_valid_policy_by_id_returns_correct_policy(policies_client):
    listing = policies_client.get("/api/policies?per_page=1").get_json()
    sample = listing["data"][0]

    response = policies_client.get(f"/api/policies/{sample['id']}")
    assert response.status_code == 200
    body = response.get_json()
    assert body["data"]["id"] == sample["id"]
    assert body["data"]["name"] == sample["name"]
    assert body["data"]["sector"] == sample["sector"]


def test_get_nonexistent_policy_id_returns_404_json(policies_client):
    response = policies_client.get("/api/policies/99999999")
    assert response.status_code == 404
    assert response.get_json() == {"error": "Policy not found"}
    assert response.content_type.startswith("application/json")


def test_get_invalid_non_numeric_policy_id_returns_clean_400(policies_client):
    response = policies_client.get("/api/policies/not-a-number")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid policy id"}
    assert response.content_type.startswith("application/json")


def test_get_negative_policy_id_returns_404_not_500(policies_client):
    """A syntactically valid integer that just doesn't exist as a row -
    must not crash, must be the same clean 404 as any other missing id."""
    response = policies_client.get("/api/policies/-1")
    assert response.status_code == 404


# --- 13: GET /api/policies/sectors ------------------------------------------

def test_sectors_endpoint_returns_database_derived_sectors_and_counts(policies_client):
    response = policies_client.get("/api/policies/sectors")
    assert response.status_code == 200
    body = response.get_json()
    sectors = {entry["sector"]: entry["count"] for entry in body["data"]}

    assert len(sectors) == 15
    assert sum(sectors.values()) == 151
    assert sectors["agriculture"] == 10  # matches the real data/agriculture.json count


def test_sectors_endpoint_is_sorted_alphabetically(policies_client):
    body = policies_client.get("/api/policies/sectors").get_json()
    sector_names = [entry["sector"] for entry in body["data"]]
    assert sector_names == sorted(sector_names)


# --- 14: GET /api/policies/categories ---------------------------------------

def test_categories_endpoint_returns_database_derived_categories_and_counts(policies_client):
    response = policies_client.get("/api/policies/categories")
    assert response.status_code == 200
    body = response.get_json()
    assert len(body["data"]) > 0
    assert sum(entry["count"] for entry in body["data"]) == 151


def test_categories_endpoint_supports_sector_filtering(policies_client):
    all_categories = policies_client.get("/api/policies/categories").get_json()["data"]
    agriculture_categories = policies_client.get(
        "/api/policies/categories?sector=agriculture"
    ).get_json()["data"]

    assert sum(entry["count"] for entry in agriculture_categories) == 10
    assert len(agriculture_categories) <= len(all_categories)


def test_categories_endpoint_is_sorted_alphabetically(policies_client):
    body = policies_client.get("/api/policies/categories").get_json()
    category_names = [entry["category"] for entry in body["data"]]
    assert category_names == sorted(category_names)


# --- 15: empty result sets return valid JSON with total=0 ------------------

def test_empty_result_set_returns_valid_json_with_total_zero(policies_client):
    response = policies_client.get("/api/policies?sector=nonexistent-sector-xyz")
    assert response.status_code == 200
    body = response.get_json()
    assert body["data"] == []
    assert body["pagination"]["total"] == 0
    assert body["pagination"]["pages"] == 0


def test_categories_for_nonexistent_sector_returns_empty_list_not_error(policies_client):
    response = policies_client.get("/api/policies/categories?sector=nonexistent-sector-xyz")
    assert response.status_code == 200
    assert response.get_json()["data"] == []


# --- security / response hygiene --------------------------------------------

def test_list_response_never_exposes_source_file_or_timestamps(policies_client):
    body = policies_client.get("/api/policies?per_page=5").get_json()
    for policy in body["data"]:
        assert "source_file" not in policy
        assert "created_at" not in policy
        assert "updated_at" not in policy


def test_single_policy_response_never_exposes_source_file_or_timestamps(policies_client):
    listing = policies_client.get("/api/policies?per_page=1").get_json()
    policy_id = listing["data"][0]["id"]
    body = policies_client.get(f"/api/policies/{policy_id}").get_json()
    assert "source_file" not in body["data"]
    assert "created_at" not in body["data"]
    assert "updated_at" not in body["data"]


# --- no database configured -------------------------------------------------

def test_list_policies_without_database_configured_returns_503(no_db_client):
    response = no_db_client.get("/api/policies")
    assert response.status_code == 503


def test_get_policy_without_database_configured_returns_503(no_db_client):
    response = no_db_client.get("/api/policies/1")
    assert response.status_code == 503


def test_sectors_without_database_configured_returns_503(no_db_client):
    response = no_db_client.get("/api/policies/sectors")
    assert response.status_code == 503


def test_categories_without_database_configured_returns_503(no_db_client):
    response = no_db_client.get("/api/policies/categories")
    assert response.status_code == 503


# --- 16: existing endpoints unaffected (backward compatibility) ------------

def test_existing_json_backed_policies_endpoint_still_works(app_client):
    response = app_client.get("/policies")
    assert response.status_code == 200
    assert len(response.get_json()) == 151  # still JSON-backed, unaffected


def test_existing_chat_endpoint_still_works(app_client):
    response = app_client.post("/chat", json={"message": "agriculture policy"})
    assert response.status_code == 200


def test_existing_health_endpoint_still_works(app_client):
    response = app_client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
