(function () {
  "use strict";

  var PAGE_SIZE = 50;
  var SEARCH_INDEX_PATH = "data/search/";

  // Region code to name mapping. Codes that map to themselves are ones whose
  // meaning isn't documented upstream; they're shown as-is rather than guessed.
  var REGION_NAMES = {
    LON: "London",
    CHI: "Chichester (South East)",
    MAN: "Manchester (North West)",
    BIR: "Birmingham (Midlands)",
    CAM: "Cambridge (East)",
    HAV: "Havant (Southern)",
    NAT: "National",
    WAL: "Wales",
    Unknown: "Unknown",
  };

  var allDecisions = [];
  var haystacks = [];
  var filteredDecisions = [];
  var stats = {};
  var currentPage = 1;
  var currentSort = "date-desc";
  var availableShards = null;
  var shardCache = {};
  var searchToken = 0;

  var els = {};

  function $(id) {
    return document.getElementById(id);
  }

  function init() {
    [
      "loading", "error", "error-message", "retry-btn", "content",
      "stat-total", "stat-categories", "stat-date-range", "stat-regions",
      "category-chart", "search-input", "search-btn", "filter-category",
      "filter-subcategory", "filter-region", "filter-year-from", "filter-year-to",
      "clear-filters", "results-count", "results-status", "sort-by", "results-body",
      "results-section", "pagination", "page-first", "page-prev", "page-next",
      "page-last", "page-info", "page-jump",
    ].forEach(function (id) {
      els[id.replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); })] = $(id);
    });

    bindEvents();
    loadData();
  }

  function bindEvents() {
    els.searchBtn.addEventListener("click", function () { applyFilters(); });
    els.searchInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") applyFilters();
    });
    // The native clear button (ⓧ) and Escape both fire "search"/"input" but not
    // "keydown" with Enter, so clearing the box used to leave results filtered.
    els.searchInput.addEventListener("search", function () { applyFilters(); });
    els.searchInput.addEventListener("input", function () {
      if (els.searchInput.value === "") applyFilters();
    });

    els.filterCategory.addEventListener("change", onCategoryChange);
    ["filterSubcategory", "filterRegion", "filterYearFrom", "filterYearTo"].forEach(function (k) {
      els[k].addEventListener("change", function () { applyFilters(); });
    });
    els.clearFilters.addEventListener("click", clearFilters);
    els.sortBy.addEventListener("change", onSortChange);

    els.pageFirst.addEventListener("click", function () { goToPage(1); });
    els.pagePrev.addEventListener("click", function () { goToPage(currentPage - 1); });
    els.pageNext.addEventListener("click", function () { goToPage(currentPage + 1); });
    els.pageLast.addEventListener("click", function () { goToPage(totalPages()); });
    els.pageJump.addEventListener("change", function () {
      goToPage(parseInt(els.pageJump.value, 10) || 1);
    });

    els.retryBtn.addEventListener("click", function () {
      els.error.hidden = true;
      els.loading.hidden = false;
      loadData();
    });

    // Sortable headers need to work from the keyboard, not just the mouse.
    document.querySelectorAll("th.sortable").forEach(function (th) {
      th.addEventListener("click", function () { toggleSort(th); });
      th.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggleSort(th);
        }
      });
    });

    window.addEventListener("popstate", function () {
      readStateFromURL();
      applyFilters({ pushState: false, keepPage: true });
    });
  }

  function toggleSort(th) {
    var field = th.dataset.sort;
    els.sortBy.value = els.sortBy.value === field + "-desc" ? field + "-asc" : field + "-desc";
    onSortChange();
  }

  // --- data loading ---------------------------------------------------------

  function loadData() {
    fetch("data/decisions.json")
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        stats = data.stats;
        allDecisions = data.decisions;
        buildHaystacks();
        els.loading.hidden = true;
        els.content.hidden = false;
        // Render separately from the fetch chain, so a bug in rendering is not
        // reported to the user as a network failure they could fix by reloading.
        try {
          renderStats();
          populateFilters();
          readStateFromURL();
          applyFilters({ pushState: false, keepPage: true });
        } catch (err) {
          showError("The decisions data loaded but could not be displayed. "
            + "This is a bug in the site, not your connection.", err);
        }
      })
      .catch(function (err) {
        showError("Could not download the decisions data. Check your connection "
          + "and try again.", err);
      });
  }

  function showError(message, err) {
    if (err) console.error(message, err);
    els.loading.hidden = true;
    els.errorMessage.textContent = message;
    els.error.hidden = false;
  }

  // One lowercased string per record, built once at load instead of rebuilt for
  // all 17,000 records on every single query.
  function buildHaystacks() {
    haystacks = allDecisions.map(function (d) {
      return [
        d.property_address, d.case_reference, d.description, d.applicant,
        d.respondent, d.presiding_judge, d.decision_outcome,
      ].filter(Boolean).join(" ").toLowerCase();
    });
  }

  // --- full-text search index ----------------------------------------------

  function loadShardList() {
    if (availableShards) return Promise.resolve(availableShards);
    return fetch(SEARCH_INDEX_PATH + "shards.json")
      .then(function (res) { return res.ok ? res.json() : []; })
      .catch(function () { return []; })
      .then(function (list) {
        availableShards = list;
        return list;
      });
  }

  function loadShard(prefix) {
    if (Object.prototype.hasOwnProperty.call(shardCache, prefix)) {
      return Promise.resolve(shardCache[prefix]);
    }
    return fetch(SEARCH_INDEX_PATH + prefix + ".json")
      .then(function (res) { return res.ok ? res.json() : null; })
      .catch(function () { return null; })
      .then(function (shard) {
        shardCache[prefix] = shard;
        return shard;
      });
  }

  // Postings are base-36 deltas joined by "." — see encode_postings in
  // scripts/build_site_data.py.
  function decodePostings(encoded, into) {
    var parts = encoded.split(".");
    var id = 0;
    for (var i = 0; i < parts.length; i++) {
      id += parseInt(parts[i], 36);
      into.add(id);
    }
  }

  // Tokens this long or longer also match words that merely start with them, so
  // "lease" finds "leasehold". Below it, only exact matches count — otherwise
  // "section 20" matches every decision mentioning 2004, 2019 or 20th.
  var PREFIX_MATCH_MIN_LENGTH = 4;

  // Doc ids whose full text contains the term, or null when the index cannot
  // answer (no such shard, or the token is a single character).
  function fullTextMatches(token) {
    if (token.length < 2) return Promise.resolve(null);
    var prefix = token.slice(0, 2);
    return loadShardList().then(function (list) {
      if (list.indexOf(prefix) === -1) return null;
      return loadShard(prefix).then(function (shard) {
        if (!shard) return null;
        var ids = new Set();
        if (token.length < PREFIX_MATCH_MIN_LENGTH) {
          if (shard[token]) decodePostings(shard[token], ids);
          return ids;
        }
        for (var term in shard) {
          if (term.indexOf(token) === 0) decodePostings(shard[term], ids);
        }
        return ids;
      });
    });
  }

  // --- rendering ------------------------------------------------------------

  function renderStats() {
    els.statTotal.textContent = stats.total.toLocaleString();
    els.statCategories.textContent = Object.keys(stats.categories).length;
    els.statRegions.textContent = Object.keys(stats.regions).length;
    els.statDateRange.textContent =
      stats.date_range.earliest.slice(0, 4) + " – " + stats.date_range.latest.slice(0, 4);

    var entries = Object.entries(stats.categories);
    var maxCount = entries.reduce(function (m, e) { return Math.max(m, e[1]); }, 0);

    var frag = document.createDocumentFragment();
    entries.forEach(function (entry) {
      var row = el("div", "bar-row");
      var label = el("span", "bar-label", entry[0]);
      label.title = entry[0];
      var track = el("div", "bar-track");
      var fill = el("div", "bar-fill");
      fill.style.width = (entry[1] / maxCount * 100).toFixed(1) + "%";
      track.appendChild(fill);
      row.appendChild(label);
      row.appendChild(track);
      row.appendChild(el("span", "bar-count", entry[1].toLocaleString()));
      frag.appendChild(row);
    });
    els.categoryChart.replaceChildren(frag);
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function populateFilters() {
    Object.keys(stats.categories).sort().forEach(function (cat) {
      els.filterCategory.appendChild(
        option(cat, cat + " (" + stats.categories[cat].toLocaleString() + ")"));
    });

    Object.entries(stats.regions)
      .sort(function (a, b) { return b[1] - a[1]; })
      .forEach(function (entry) {
        var name = REGION_NAMES[entry[0]] || entry[0];
        els.filterRegion.appendChild(
          option(entry[0], entry[0] + " — " + name + " (" + entry[1].toLocaleString() + ")"));
      });

    Object.keys(stats.years).map(Number)
      .sort(function (a, b) { return a - b; })
      .forEach(function (y) {
        els.filterYearFrom.appendChild(option(y, y));
        els.filterYearTo.appendChild(option(y, y));
      });
  }

  function option(value, text) {
    var opt = document.createElement("option");
    opt.value = value;
    opt.textContent = text;
    return opt;
  }

  function onCategoryChange() {
    var selected = els.filterCategory.value;
    els.filterSubcategory.replaceChildren(option("", "All sub-categories"));
    if (selected && stats.category_hierarchy[selected]) {
      stats.category_hierarchy[selected].forEach(function (sub) {
        var count = stats.sub_categories[sub] || 0;
        els.filterSubcategory.appendChild(
          option(sub, sub + " (" + count.toLocaleString() + ")"));
      });
    }
    applyFilters();
  }

  // --- filtering ------------------------------------------------------------

  function applyFilters(opts) {
    var options = opts || {};
    var query = els.searchInput.value.trim().toLowerCase();
    var tokens = query ? query.split(/\s+/).filter(Boolean) : [];

    // Guard against an earlier, slower search resolving after a later one.
    var token = ++searchToken;

    if (tokens.length === 0) {
      finishFiltering(null, options);
      return;
    }

    els.resultsStatus.textContent = "Searching…";
    resolveQuery(tokens)
      .then(function (plan) {
        if (token !== searchToken) return;
        finishFiltering(plan, options);
      })
      .catch(function (err) {
        if (token !== searchToken) return;
        console.error("Search index unavailable, falling back to metadata only", err);
        finishFiltering(
          { tokens: tokens, matchSets: tokens.map(function () { return null; }), phrase: false },
          options);
      });
  }

  function finishFiltering(search, options) {
    var cat = els.filterCategory.value;
    var subcat = els.filterSubcategory.value;
    var region = els.filterRegion.value;
    var yearFrom = els.filterYearFrom.value ? parseInt(els.filterYearFrom.value, 10) : 0;
    var yearTo = els.filterYearTo.value ? parseInt(els.filterYearTo.value, 10) : 9999;

    filteredDecisions = [];
    for (var i = 0; i < allDecisions.length; i++) {
      var d = allDecisions[i];

      if (cat && d.category_label !== cat) continue;
      if (subcat && d.sub_category_label !== subcat) continue;
      if (region && (d.region_code || "Unknown") !== region) continue;

      if (d.decision_date) {
        var year = +d.decision_date.slice(0, 4);
        if (year < yearFrom || year > yearTo) continue;
      } else if (yearFrom > 0 || yearTo < 9999) {
        // Treat a dateless record the same way for both bounds — it used to be
        // excluded by "year from" but kept by "year to".
        continue;
      }

      if (search && !matchesSearch(i, search)) continue;

      filteredDecisions.push(d);
    }

    sortDecisions();
    currentPage = options.keepPage ? currentPage : 1;
    renderResults();
    if (options.pushState !== false) writeStateToURL();
  }

  // Turn a list of query words into the sets that must all match.
  //
  // A two-word query is first tried as a phrase: the index carries the most
  // frequent adjacent pairs joined by "_", so "service charge" resolves to one
  // posting list of decisions where those words are actually adjacent. Without
  // it the query is an AND over two words that separately appear in almost
  // every decision, which is technically correct and completely useless.
  function resolveQuery(tokens) {
    var phrase = tokens.length === 2 ? tokens.join("_") : null;
    var lookup = phrase ? exactTerm(phrase) : Promise.resolve(null);

    return lookup.then(function (phraseMatches) {
      if (phraseMatches && phraseMatches.size > 0) {
        return { tokens: [tokens.join(" ")], matchSets: [phraseMatches], phrase: true };
      }
      return Promise.all(tokens.map(fullTextMatches)).then(function (matchSets) {
        return { tokens: tokens, matchSets: matchSets, phrase: false };
      });
    });
  }

  // Exact-term lookup, no prefix expansion — used for phrase keys.
  function exactTerm(term) {
    var prefix = term.slice(0, 2);
    return loadShardList().then(function (list) {
      if (list.indexOf(prefix) === -1) return null;
      return loadShard(prefix).then(function (shard) {
        if (!shard || !shard[term]) return null;
        var ids = new Set();
        decodePostings(shard[term], ids);
        return ids;
      });
    });
  }

  // Every token must match, via either the record's metadata or its full text.
  function matchesSearch(idx, search) {
    var haystack = haystacks[idx];
    for (var t = 0; t < search.tokens.length; t++) {
      var set = search.matchSets[t];
      if (haystack.indexOf(search.tokens[t]) !== -1) continue;
      if (set && set.has(idx)) continue;
      return false;
    }
    return true;
  }

  function clearFilters() {
    els.searchInput.value = "";
    els.filterCategory.value = "";
    els.filterSubcategory.replaceChildren(option("", "All sub-categories"));
    els.filterRegion.value = "";
    els.filterYearFrom.value = "";
    els.filterYearTo.value = "";
    applyFilters();
  }

  function onSortChange() {
    currentSort = els.sortBy.value;
    updateSortIndicators();
    sortDecisions();
    currentPage = 1;
    renderResults();
    writeStateToURL();
  }

  function updateSortIndicators() {
    var parts = currentSort.split("-");
    document.querySelectorAll("th.sortable").forEach(function (th) {
      var active = th.dataset.sort === parts[0];
      th.setAttribute("aria-sort", active ? (parts[1] === "asc" ? "ascending" : "descending") : "none");
      var indicator = th.querySelector(".sort-indicator");
      if (indicator) indicator.textContent = active ? (parts[1] === "asc" ? " ▲" : " ▼") : "";
    });
  }

  function sortDecisions() {
    // decision_date is ISO-8601, so plain comparison sorts it correctly and is
    // roughly 3x faster than localeCompare over 17,000 records.
    var sort = currentSort;
    filteredDecisions.sort(function (a, b) {
      switch (sort) {
        case "date-desc": return cmp(b.decision_date, a.decision_date);
        case "date-asc": return cmp(a.decision_date, b.decision_date);
        case "address-asc":
          return (a.property_address || "").localeCompare(b.property_address || "");
        case "address-desc":
          return (b.property_address || "").localeCompare(a.property_address || "");
        default: return 0;
      }
    });
  }

  function cmp(a, b) {
    a = a || "";
    b = b || "";
    return a < b ? -1 : a > b ? 1 : 0;
  }

  function totalPages() {
    return Math.max(1, Math.ceil(filteredDecisions.length / PAGE_SIZE));
  }

  function goToPage(page) {
    var pages = totalPages();
    currentPage = Math.min(Math.max(1, page), pages);
    renderResults();
    writeStateToURL();
    // Move focus to the results region rather than scrolling the page and
    // leaving focus on a button that just moved off screen.
    els.resultsSection.focus({ preventScroll: true });
  }

  function renderResults() {
    var total = filteredDecisions.length;
    var pages = totalPages();
    if (currentPage > pages) currentPage = pages;

    var start = (currentPage - 1) * PAGE_SIZE;
    var page = filteredDecisions.slice(start, start + PAGE_SIZE);

    els.resultsCount.textContent =
      "(" + total.toLocaleString() + " decision" + (total !== 1 ? "s" : "") + ")";
    els.resultsStatus.textContent =
      total === 0
        ? "No decisions match your search criteria."
        : total.toLocaleString() + " decisions found. Showing page "
          + currentPage + " of " + pages + ".";

    var frag = document.createDocumentFragment();
    if (page.length === 0) {
      var emptyRow = document.createElement("tr");
      var emptyCell = el("td", "empty-results", "No decisions match your search criteria.");
      emptyCell.colSpan = 6;
      emptyRow.appendChild(emptyCell);
      frag.appendChild(emptyRow);
    } else {
      page.forEach(function (d) {
        frag.appendChild(renderRow(d));
      });
    }
    els.resultsBody.replaceChildren(frag);

    els.pageFirst.disabled = els.pagePrev.disabled = currentPage <= 1;
    els.pageNext.disabled = els.pageLast.disabled = currentPage >= pages;
    els.pageInfo.textContent = "Page " + currentPage + " of " + pages.toLocaleString();
    els.pageJump.max = pages;
    els.pageJump.value = currentPage;
  }

  // Built with DOM nodes rather than an HTML string. The previous escaper
  // serialised a text node, which escapes & < > but NOT quotes — and its output
  // was interpolated into title="..." and href="..." attributes, so any address
  // containing a double quote broke out of the attribute. textContent and
  // setAttribute cannot have that class of bug at all.
  function renderRow(d) {
    var tr = document.createElement("tr");

    var dateText = d.decision_date || "—";
    var dateCell = el("td", "col-date", dateText);
    if (d.decision_date_approximate) {
      dateCell.textContent = dateText + " ~";
      dateCell.title = "Month and year only — the Wales tribunal does not publish an exact day";
    }
    tr.appendChild(dateCell);

    tr.appendChild(el("td", "col-ref", d.case_reference || "—"));

    var address = d.property_address || "—";
    var addressCell = el("td", "col-address", truncate(address, 120));
    addressCell.title = address;
    tr.appendChild(addressCell);

    var category = d.sub_category_label || d.category_label || "—";
    var categoryCell = el("td", "col-category", category);
    categoryCell.title = category;
    tr.appendChild(categoryCell);

    tr.appendChild(el("td", "col-region", d.region_code || "—"));

    var linkCell = el("td", "col-link");
    if (d.url && /^https?:\/\//i.test(d.url)) {
      var a = el("a", null, "View");
      a.href = d.url;
      a.target = "_blank";
      a.rel = "noopener";
      a.title = "View the full decision at its official source";
      linkCell.appendChild(a);
    } else {
      linkCell.textContent = "—";
    }
    tr.appendChild(linkCell);

    return tr;
  }

  function truncate(str, max) {
    return str.length <= max ? str : str.slice(0, max) + "…";
  }

  // --- URL state ------------------------------------------------------------
  // Filters live in the query string so results can be shared and bookmarked,
  // and so Back undoes a filter instead of leaving the site.

  var URL_KEYS = {
    q: "searchInput",
    category: "filterCategory",
    sub: "filterSubcategory",
    region: "filterRegion",
    from: "filterYearFrom",
    to: "filterYearTo",
    sort: "sortBy",
  };

  function writeStateToURL() {
    var params = new URLSearchParams();
    Object.keys(URL_KEYS).forEach(function (key) {
      var value = els[URL_KEYS[key]].value;
      if (value && !(key === "sort" && value === "date-desc")) params.set(key, value);
    });
    if (currentPage > 1) params.set("page", currentPage);
    var query = params.toString();
    var url = query ? "?" + query : location.pathname;
    history.replaceState(null, "", url);
  }

  function readStateFromURL() {
    var params = new URLSearchParams(location.search);
    els.searchInput.value = params.get("q") || "";
    els.filterCategory.value = params.get("category") || "";
    onCategoryOptionsForURL(params.get("category"));
    els.filterSubcategory.value = params.get("sub") || "";
    els.filterRegion.value = params.get("region") || "";
    els.filterYearFrom.value = params.get("from") || "";
    els.filterYearTo.value = params.get("to") || "";
    els.sortBy.value = params.get("sort") || "date-desc";
    currentSort = els.sortBy.value;
    currentPage = parseInt(params.get("page"), 10) || 1;
    updateSortIndicators();
  }

  function onCategoryOptionsForURL(category) {
    els.filterSubcategory.replaceChildren(option("", "All sub-categories"));
    if (category && stats.category_hierarchy && stats.category_hierarchy[category]) {
      stats.category_hierarchy[category].forEach(function (sub) {
        var count = stats.sub_categories[sub] || 0;
        els.filterSubcategory.appendChild(
          option(sub, sub + " (" + count.toLocaleString() + ")"));
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
