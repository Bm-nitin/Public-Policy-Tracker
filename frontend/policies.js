/*
 * frontend/policies.js
 *
 * Wires policies.html up to the REAL, database-backed policy API
 * (see backend/policies_routes.py):
 *
 *   GET /api/policies?page=&per_page=&sector=&category=&sub_category=
 *       -> { data: [ {id, name, sector, category, sub_category,
 *                     change, impact}, ... ],
 *            pagination: { page, per_page, total, pages } }
 *   GET /api/policies/sectors      -> { data: [ {sector, count}, ... ] }
 *   GET /api/policies/categories   -> { data: [ {category, count}, ... ] }
 *                                      (optionally ?sector=... )
 *   GET /api/policies/<id>         -> { data: {...+id} }  (404/400 on error)
 *
 * IMPORTANT - confirmed by reading backend/policies_routes.py directly,
 * not assumed: the list endpoint has NO search/query parameter. This
 * file never sends one. See SEARCH below for how "Search policies" is
 * implemented instead.
 *
 * There is also no /api/policies/sub_categories (or similar) endpoint,
 * so sub-category filter options are derived from real policy rows
 * returned by GET /api/policies itself (see fetchAllFiltered below) -
 * never hardcoded.
 */

(function () {
  "use strict";

  var API_BASE = "https://policy-tracker-b8a3.onrender.com/api/policies";
  var PER_PAGE = 20; // matches the backend's own DEFAULT_PER_PAGE
  var BATCH_PER_PAGE = 100; // backend's MAX_PER_PAGE - used only for the
                             // client-side search/sub-category batch fetch below
  var BATCH_PAGE_CAP = 10; // safety cap: never fetch more than 10 x 100 = 1000
                            // rows in one batch operation, however large the
                            // dataset grows

  var state = {
    page: 1,
    sector: "",
    category: "",
    subCategory: "",
    search: "",
  };

  var els = {};
  var searchDebounceHandle = null;

  document.addEventListener("DOMContentLoaded", function () {
    els.search = document.getElementById("policySearch");
    els.sectorFilter = document.getElementById("sectorFilter");
    els.categoryFilter = document.getElementById("categoryFilter");
    els.subCategoryFilter = document.getElementById("subCategoryFilter");
    els.clearBtn = document.getElementById("clearFiltersBtn");
    els.count = document.getElementById("policiesCount");
    els.grid = document.getElementById("policiesGrid");
    els.pagination = document.getElementById("policiesPagination");
    els.modalOverlay = document.getElementById("policyModalOverlay");
    els.modalBody = document.getElementById("policyModalBody");
    els.modalClose = document.getElementById("policyModalClose");

    if (!els.grid) return; // not on policies.html

    wireEvents();
    loadSectorOptions();
    loadCategoryOptions();
    loadSubCategoryOptions();
    fetchAndRenderPolicies();
  });

  /* ------------------------------------------------------------------
     EVENTS
     ------------------------------------------------------------------ */

  function wireEvents() {
    els.search.addEventListener("input", function () {
      clearTimeout(searchDebounceHandle);
      searchDebounceHandle = setTimeout(function () {
        state.search = els.search.value.trim();
        state.page = 1;
        fetchAndRenderPolicies();
      }, 300);
    });

    els.sectorFilter.addEventListener("change", function () {
      state.sector = els.sectorFilter.value;
      state.category = "";
      state.subCategory = "";
      state.page = 1;
      loadCategoryOptions();
      loadSubCategoryOptions();
      fetchAndRenderPolicies();
    });

    els.categoryFilter.addEventListener("change", function () {
      state.category = els.categoryFilter.value;
      state.subCategory = "";
      state.page = 1;
      loadSubCategoryOptions();
      fetchAndRenderPolicies();
    });

    els.subCategoryFilter.addEventListener("change", function () {
      state.subCategory = els.subCategoryFilter.value;
      state.page = 1;
      fetchAndRenderPolicies();
    });

    els.clearBtn.addEventListener("click", function () {
      state = { page: 1, sector: "", category: "", subCategory: "", search: "" };
      els.search.value = "";
      els.sectorFilter.value = "";
      els.categoryFilter.value = "";
      els.subCategoryFilter.value = "";
      loadCategoryOptions();
      loadSubCategoryOptions();
      fetchAndRenderPolicies();
    });

    els.modalClose.addEventListener("click", closeModal);
    els.modalOverlay.addEventListener("click", function (e) {
      if (e.target === els.modalOverlay) closeModal();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeModal();
    });
  }

  /* ------------------------------------------------------------------
     FILTER OPTION LOADING (sectors / categories from real endpoints,
     sub-categories derived from real policy rows - see module docstring)
     ------------------------------------------------------------------ */

  function loadSectorOptions() {
    fetchJSON(API_BASE + "/sectors")
      .then(function (result) {
        if (!result.ok) return;
        var items = (result.data && result.data.data) || [];
        fillSelect(els.sectorFilter, "All sectors", items.map(function (i) {
          return { value: i.sector, label: formatLabel(i.sector) };
        }), state.sector);
      })
      .catch(function () { /* leave "All sectors" as the only option */ });
  }

  function loadCategoryOptions() {
    var url = API_BASE + "/categories";
    if (state.sector) url += "?sector=" + encodeURIComponent(state.sector);

    fetchJSON(url)
      .then(function (result) {
        if (!result.ok) return;
        var items = (result.data && result.data.data) || [];
        fillSelect(els.categoryFilter, "All categories", items.map(function (i) {
          return { value: i.category, label: formatLabel(i.category) };
        }), state.category);
      })
      .catch(function () { /* leave default option */ });
  }

  function loadSubCategoryOptions() {
    // No dedicated endpoint exists for this - derive distinct
    // sub_category values from real policy rows matching the current
    // sector/category filters (never hardcoded, never invented).
    fetchAllFiltered({ sector: state.sector, category: state.category })
      .then(function (rows) {
        var seen = {};
        var subCats = [];
        rows.forEach(function (row) {
          if (row.sub_category && !seen[row.sub_category]) {
            seen[row.sub_category] = true;
            subCats.push(row.sub_category);
          }
        });
        subCats.sort();
        fillSelect(els.subCategoryFilter, "All sub-categories", subCats.map(function (s) {
          return { value: s, label: formatLabel(s) };
        }), state.subCategory);
      })
      .catch(function () { /* leave default option */ });
  }

  function fillSelect(selectEl, defaultLabel, options, currentValue) {
    selectEl.innerHTML = "";
    var defaultOpt = document.createElement("option");
    defaultOpt.value = "";
    defaultOpt.textContent = defaultLabel;
    selectEl.appendChild(defaultOpt);

    options.forEach(function (opt) {
      var optionEl = document.createElement("option");
      optionEl.value = opt.value;
      optionEl.textContent = opt.label;
      selectEl.appendChild(optionEl);
    });

    selectEl.value = currentValue || "";
  }

  function formatLabel(raw) {
    // Presentational only (underscores -> spaces, title case) - the
    // underlying filter value sent to the API is always the raw string
    // exactly as returned by the backend, never this formatted label.
    return String(raw)
      .replace(/_/g, " ")
      .replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  /* ------------------------------------------------------------------
     LISTING - normal (server-paginated) vs search (client-side, see
     module docstring for why: the API has no search parameter at all)
     ------------------------------------------------------------------ */

  function fetchAndRenderPolicies() {
    renderLoading();

    if (state.search) {
      fetchAllFiltered({
        sector: state.sector,
        category: state.category,
        sub_category: state.subCategory,
      })
        .then(function (rows) {
          var term = state.search.toLowerCase();
          var filtered = rows.filter(function (row) {
            return (
              (row.name && row.name.toLowerCase().indexOf(term) !== -1) ||
              (row.category && row.category.toLowerCase().indexOf(term) !== -1) ||
              (row.sub_category && row.sub_category.toLowerCase().indexOf(term) !== -1) ||
              (row.change && row.change.toLowerCase().indexOf(term) !== -1) ||
              (row.impact && row.impact.toLowerCase().indexOf(term) !== -1)
            );
          });

          var total = filtered.length;
          var pages = total ? Math.ceil(total / PER_PAGE) : 0;
          if (state.page > pages && pages > 0) state.page = pages;

          var start = (state.page - 1) * PER_PAGE;
          var pageItems = filtered.slice(start, start + PER_PAGE);

          renderResults(pageItems, { page: state.page, per_page: PER_PAGE, total: total, pages: pages }, true);
        })
        .catch(function () {
          renderError();
        });
      return;
    }

    var params = new URLSearchParams();
    params.set("page", String(state.page));
    params.set("per_page", String(PER_PAGE));
    if (state.sector) params.set("sector", state.sector);
    if (state.category) params.set("category", state.category);
    if (state.subCategory) params.set("sub_category", state.subCategory);

    fetchJSON(API_BASE + "?" + params.toString())
      .then(function (result) {
        if (!result.ok) {
          renderError();
          return;
        }
        var body = result.data || {};
        renderResults(body.data || [], body.pagination || { page: 1, per_page: PER_PAGE, total: 0, pages: 0 }, false);
      })
      .catch(function () {
        renderError();
      });
  }

  // Pages through the list endpoint (per_page=100, the backend's max)
  // using only real, supported query params, up to BATCH_PAGE_CAP pages,
  // and returns every row collected. Used for (a) client-side search
  // and (b) deriving real sub-category options - never for inventing
  // data, always the actual rows the API returned.
  function fetchAllFiltered(filters) {
    var collected = [];

    function fetchPage(page) {
      var params = new URLSearchParams();
      params.set("page", String(page));
      params.set("per_page", String(BATCH_PER_PAGE));
      if (filters.sector) params.set("sector", filters.sector);
      if (filters.category) params.set("category", filters.category);
      if (filters.sub_category) params.set("sub_category", filters.sub_category);

      return fetchJSON(API_BASE + "?" + params.toString()).then(function (result) {
        if (!result.ok) throw new Error("policy fetch failed");
        var body = result.data || {};
        var rows = body.data || [];
        collected = collected.concat(rows);

        var pagination = body.pagination || {};
        var hasMore = pagination.pages && page < pagination.pages && page < BATCH_PAGE_CAP;
        if (hasMore) {
          return fetchPage(page + 1);
        }
        return collected;
      });
    }

    return fetchPage(1);
  }

  /* ------------------------------------------------------------------
     RENDERING - all API-provided text is set via textContent, never
     innerHTML, so nothing from the database can inject markup.
     ------------------------------------------------------------------ */

  function renderLoading() {
    els.count.textContent = "";
    els.pagination.innerHTML = "";
    els.grid.innerHTML = "";
    var loading = document.createElement("div");
    loading.className = "card policies-empty";
    loading.textContent = "Loading policies...";
    els.grid.appendChild(loading);
  }

  function renderError() {
    els.count.textContent = "";
    els.pagination.innerHTML = "";
    els.grid.innerHTML = "";
    var errorBox = document.createElement("div");
    errorBox.className = "card policies-empty policies-error";
    errorBox.textContent = "Unable to load policies right now. Please try again.";
    els.grid.appendChild(errorBox);
  }

  function renderResults(items, pagination, isSearchMode) {
    els.grid.innerHTML = "";

    if (!items.length) {
      renderCount(0, isSearchMode);
      var empty = document.createElement("div");
      empty.className = "card policies-empty";

      var msg = document.createElement("p");
      msg.textContent = "No policies found.";
      empty.appendChild(msg);

      var clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "btn btn-secondary";
      clearBtn.textContent = "Clear Filters";
      clearBtn.addEventListener("click", function () { els.clearBtn.click(); });
      empty.appendChild(clearBtn);

      els.grid.appendChild(empty);
      renderPagination(pagination);
      return;
    }

    renderCount(pagination.total, isSearchMode);
    items.forEach(function (policy) {
      els.grid.appendChild(buildPolicyCard(policy));
    });
    renderPagination(pagination);
  }

  function renderCount(total, isSearchMode) {
    if (!total) {
      els.count.textContent = "";
      return;
    }
    var label = total + (total === 1 ? " policy" : " policies");
    if (isSearchMode) {
      label += " matching your search across all filtered results";
    }
    els.count.textContent = label;
  }

  function buildPolicyCard(policy) {
    var card = document.createElement("div");
    card.className = "card policy-card";

    var title = document.createElement("div");
    title.className = "policy-card__title";
    title.textContent = policy.name || "Unnamed policy";
    card.appendChild(title);

    var badges = document.createElement("div");
    badges.className = "policy-card__badges";
    [policy.sector, policy.category, policy.sub_category].forEach(function (val) {
      if (!val) return;
      var badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = formatLabel(val);
      badges.appendChild(badge);
    });
    card.appendChild(badges);

    if (policy.impact) {
      var desc = document.createElement("p");
      desc.className = "policy-card__desc";
      desc.textContent = truncate(policy.impact, 140);
      card.appendChild(desc);
    }

    var actions = document.createElement("div");
    actions.className = "policy-card__actions";

    var detailsBtn = document.createElement("button");
    detailsBtn.type = "button";
    detailsBtn.className = "btn btn-secondary";
    detailsBtn.textContent = "View Details";
    detailsBtn.addEventListener("click", function () { openDetail(policy.id); });
    actions.appendChild(detailsBtn);

    card.appendChild(actions);

    return card;
  }

  function truncate(text, maxLen) {
    if (text.length <= maxLen) return text;
    return text.slice(0, maxLen).trim() + "…";
  }

  function renderPagination(pagination) {
    els.pagination.innerHTML = "";
    if (!pagination || !pagination.pages) return;

    var prevBtn = document.createElement("button");
    prevBtn.type = "button";
    prevBtn.className = "btn btn-ghost";
    prevBtn.textContent = "Previous";
    prevBtn.disabled = pagination.page <= 1;
    prevBtn.addEventListener("click", function () {
      state.page = Math.max(1, state.page - 1);
      fetchAndRenderPolicies();
    });

    var label = document.createElement("span");
    label.className = "policies-pagination__label";
    label.textContent = "Page " + pagination.page + " of " + pagination.pages;

    var nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.className = "btn btn-ghost";
    nextBtn.textContent = "Next";
    nextBtn.disabled = pagination.page >= pagination.pages;
    nextBtn.addEventListener("click", function () {
      state.page = Math.min(pagination.pages, state.page + 1);
      fetchAndRenderPolicies();
    });

    els.pagination.appendChild(prevBtn);
    els.pagination.appendChild(label);
    els.pagination.appendChild(nextBtn);
  }

  /* ------------------------------------------------------------------
     DETAIL MODAL
     ------------------------------------------------------------------ */

  function openDetail(id) {
    els.modalBody.innerHTML = "";
    var loading = document.createElement("p");
    loading.textContent = "Loading policy...";
    els.modalBody.appendChild(loading);
    showModal();

    fetchJSON(API_BASE + "/" + encodeURIComponent(id))
      .then(function (result) {
        if (!result.ok) {
          var message = (result.data && result.data.error) || "Unable to load this policy right now. Please try again.";
          renderModalError(message);
          return;
        }
        renderModalDetail(result.data.data);
      })
      .catch(function () {
        renderModalError("Unable to load this policy right now. Please try again.");
      });
  }

  function renderModalError(message) {
    els.modalBody.innerHTML = "";
    var p = document.createElement("p");
    p.textContent = message;
    els.modalBody.appendChild(p);
  }

  function renderModalDetail(policy) {
    els.modalBody.innerHTML = "";

    var title = document.createElement("h2");
    title.id = "policyModalTitle";
    title.textContent = policy.name || "Unnamed policy";
    els.modalBody.appendChild(title);

    var badges = document.createElement("div");
    badges.className = "policy-card__badges";
    [policy.sector, policy.category, policy.sub_category].forEach(function (val) {
      if (!val) return;
      var badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = formatLabel(val);
      badges.appendChild(badge);
    });
    els.modalBody.appendChild(badges);

    // Only fields the API actually returns - never invented ones
    // (eligibility, benefits, official link, etc. do not exist on this
    // model - see backend/models.py's Policy class docstring).
    var fields = [
      ["Change", policy.change],
      ["Impact", policy.impact],
      ["Source file", policy.source_file],
      ["Created", formatDate(policy.created_at)],
      ["Last updated", formatDate(policy.updated_at)],
    ];

    fields.forEach(function (pair) {
      if (!pair[1]) return;
      var section = document.createElement("div");
      section.className = "policy-modal__field";

      var label = document.createElement("h4");
      label.textContent = pair[0];
      section.appendChild(label);

      var value = document.createElement("p");
      value.textContent = pair[1];
      section.appendChild(value);

      els.modalBody.appendChild(section);
    });

    var askBtn = document.createElement("button");
    askBtn.type = "button";
    askBtn.className = "btn btn-primary";
    askBtn.textContent = "Ask AI About This Policy";
    askBtn.addEventListener("click", function () { askAiAbout(policy.name); });
    els.modalBody.appendChild(askBtn);
  }

  function formatDate(isoString) {
    if (!isoString) return "";
    var d = new Date(isoString);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleDateString();
  }

  function showModal() {
    els.modalOverlay.hidden = false;
  }

  function closeModal() {
    els.modalOverlay.hidden = true;
  }

  /* ------------------------------------------------------------------
     ASK AI (hands off to the existing chatbot on index.html - does not
     create a second chatbot or touch chatbot.py/retrieval.py)
     ------------------------------------------------------------------ */

  function askAiAbout(policyName) {
    var question = 'Explain the policy "' + policyName + '" and its impact.';
    window.location.href = "index.html?ask=" + encodeURIComponent(question);
  }

  /* ------------------------------------------------------------------
     FETCH HELPER
     ------------------------------------------------------------------ */

  function fetchJSON(url) {
    return fetch(url)
      .then(function (response) {
        return response.json()
          .catch(function () { return null; })
          .then(function (data) {
            return { ok: response.ok, status: response.status, data: data };
          });
      });
  }
})();
