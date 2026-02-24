(function () {
  "use strict";

  const PAGE_SIZE = 50;

  // Region code to name mapping
  const REGION_NAMES = {
    LON: "London",
    CHI: "Chichester (South East)",
    MAN: "Manchester (North West)",
    BIR: "Birmingham (Midlands)",
    CAM: "Cambridge (East)",
    HAV: "Havant (Southern)",
    NS: "NS",
    TR: "TR",
    NT: "NT",
    VG: "VG",
    NAT: "National",
    GB: "GB",
    RC: "RC",
    WAL: "Wales",
    Unknown: "Unknown",
  };

  let allDecisions = [];
  let filteredDecisions = [];
  let stats = {};
  let currentPage = 1;
  let currentSort = "date-desc";

  // DOM elements
  const els = {};

  function $(id) {
    return document.getElementById(id);
  }

  function init() {
    els.loading = $("loading");
    els.error = $("error");
    els.content = $("content");
    els.statTotal = $("stat-total");
    els.statCategories = $("stat-categories");
    els.statDateRange = $("stat-date-range");
    els.statRegions = $("stat-regions");
    els.categoryChart = $("category-chart");
    els.searchInput = $("search-input");
    els.searchBtn = $("search-btn");
    els.filterCategory = $("filter-category");
    els.filterSubcategory = $("filter-subcategory");
    els.filterRegion = $("filter-region");
    els.filterYearFrom = $("filter-year-from");
    els.filterYearTo = $("filter-year-to");
    els.clearFilters = $("clear-filters");
    els.resultsCount = $("results-count");
    els.sortBy = $("sort-by");
    els.resultsBody = $("results-body");
    els.pagination = $("pagination");
    els.pagePrev = $("page-prev");
    els.pageNext = $("page-next");
    els.pageInfo = $("page-info");

    bindEvents();
    loadData();
  }

  function bindEvents() {
    els.searchBtn.addEventListener("click", applyFilters);
    els.searchInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") applyFilters();
    });
    els.filterCategory.addEventListener("change", onCategoryChange);
    els.filterSubcategory.addEventListener("change", applyFilters);
    els.filterRegion.addEventListener("change", applyFilters);
    els.filterYearFrom.addEventListener("change", applyFilters);
    els.filterYearTo.addEventListener("change", applyFilters);
    els.clearFilters.addEventListener("click", clearFilters);
    els.sortBy.addEventListener("change", onSortChange);
    els.pagePrev.addEventListener("click", prevPage);
    els.pageNext.addEventListener("click", nextPage);

    // Sortable column headers
    document.querySelectorAll("th.sortable").forEach(function (th) {
      th.addEventListener("click", function () {
        var field = th.dataset.sort;
        var current = els.sortBy.value;
        if (current === field + "-desc") {
          els.sortBy.value = field + "-asc";
        } else {
          els.sortBy.value = field + "-desc";
        }
        onSortChange();
      });
    });
  }

  function loadData() {
    fetch("data/decisions.json")
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        stats = data.stats;
        allDecisions = data.decisions;
        els.loading.hidden = true;
        els.content.hidden = false;
        var sc = document.getElementById("static-content"); if (sc) sc.hidden = true;
        renderStats();
        populateFilters();
        applyFilters();
      })
      .catch(function (err) {
        console.error("Failed to load data:", err);
        els.loading.hidden = true;
        els.error.hidden = false;
      });
  }

  function renderStats() {
    els.statTotal.textContent = stats.total.toLocaleString();
    els.statCategories.textContent = Object.keys(stats.categories).length;
    els.statRegions.textContent = Object.keys(stats.regions).length;

    // Date range — filter out obvious data errors (future years beyond current + 1)
    var earliest = stats.date_range.earliest;
    var latest = stats.date_range.latest;
    var maxReasonableYear = new Date().getFullYear() + 1;
    // Find the actual latest reasonable date
    var sortedDates = allDecisions
      .map(function (d) { return d.decision_date; })
      .filter(function (d) { return d && parseInt(d.substring(0, 4)) <= maxReasonableYear; })
      .sort();
    if (sortedDates.length > 0) {
      latest = sortedDates[sortedDates.length - 1];
    }
    els.statDateRange.textContent = formatYear(earliest) + " – " + formatYear(latest);

    // Category bar chart
    var maxCount = 0;
    var catEntries = Object.entries(stats.categories);
    catEntries.forEach(function (entry) {
      if (entry[1] > maxCount) maxCount = entry[1];
    });

    var chartHTML = "";
    catEntries.forEach(function (entry) {
      var label = entry[0];
      var count = entry[1];
      var pct = (count / maxCount * 100).toFixed(1);
      chartHTML +=
        '<div class="bar-row">' +
        '<span class="bar-label" title="' + escapeHTML(label) + '">' + escapeHTML(label) + '</span>' +
        '<div class="bar-track"><div class="bar-fill" style="width:' + pct + '%"></div></div>' +
        '<span class="bar-count">' + count.toLocaleString() + '</span>' +
        '</div>';
    });
    els.categoryChart.innerHTML = chartHTML;
  }

  function populateFilters() {
    // Categories
    var cats = Object.keys(stats.categories).sort();
    cats.forEach(function (cat) {
      var opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = cat + " (" + stats.categories[cat].toLocaleString() + ")";
      els.filterCategory.appendChild(opt);
    });

    // Regions
    var regionEntries = Object.entries(stats.regions).sort(function (a, b) { return b[1] - a[1]; });
    regionEntries.forEach(function (entry) {
      var code = entry[0];
      var count = entry[1];
      var opt = document.createElement("option");
      opt.value = code;
      var name = REGION_NAMES[code] || code;
      opt.textContent = code + " — " + name + " (" + count.toLocaleString() + ")";
      els.filterRegion.appendChild(opt);
    });

    // Years — filter out unreasonable years
    var maxReasonableYear = new Date().getFullYear() + 1;
    var years = Object.keys(stats.years)
      .map(Number)
      .filter(function (y) { return y >= 1990 && y <= maxReasonableYear; })
      .sort();
    years.forEach(function (y) {
      var optFrom = document.createElement("option");
      optFrom.value = y;
      optFrom.textContent = y;
      els.filterYearFrom.appendChild(optFrom);

      var optTo = document.createElement("option");
      optTo.value = y;
      optTo.textContent = y;
      els.filterYearTo.appendChild(optTo);
    });
  }

  function onCategoryChange() {
    var selectedCat = els.filterCategory.value;

    // Reset sub-category dropdown
    els.filterSubcategory.innerHTML = '<option value="">All sub-categories</option>';

    if (selectedCat && stats.category_hierarchy[selectedCat]) {
      var subs = stats.category_hierarchy[selectedCat];
      subs.forEach(function (sub) {
        var count = stats.sub_categories[sub] || 0;
        var opt = document.createElement("option");
        opt.value = sub;
        opt.textContent = sub + " (" + count.toLocaleString() + ")";
        els.filterSubcategory.appendChild(opt);
      });
    }

    applyFilters();
  }

  function applyFilters() {
    var query = els.searchInput.value.trim().toLowerCase();
    var cat = els.filterCategory.value;
    var subcat = els.filterSubcategory.value;
    var region = els.filterRegion.value;
    var yearFrom = els.filterYearFrom.value ? parseInt(els.filterYearFrom.value) : 0;
    var yearTo = els.filterYearTo.value ? parseInt(els.filterYearTo.value) : 9999;

    // Split query into tokens for multi-word search
    var tokens = query ? query.split(/\s+/).filter(Boolean) : [];

    filteredDecisions = allDecisions.filter(function (d) {
      // Category filter
      if (cat && d.category_label !== cat) return false;

      // Sub-category filter
      if (subcat && d.sub_category_label !== subcat) return false;

      // Region filter
      var dRegion = d.region_code || "Unknown";
      if (region && dRegion !== region) return false;

      // Year filter
      if (d.decision_date) {
        var year = parseInt(d.decision_date.substring(0, 4));
        if (year < yearFrom || year > yearTo) return false;
      } else if (yearFrom > 0) {
        return false;
      }

      // Text search — all tokens must match somewhere
      if (tokens.length > 0) {
        var searchable = (
          (d.property_address || "") + " " +
          (d.case_reference || "") + " " +
          (d.description || "") + " " +
          (d.applicant || "") + " " +
          (d.respondent || "") + " " +
          (d.presiding_judge || "") + " " +
          (d.decision_outcome || "") + " " +
          (d.search_keywords || "")
        ).toLowerCase();
        for (var i = 0; i < tokens.length; i++) {
          if (searchable.indexOf(tokens[i]) === -1) return false;
        }
      }

      return true;
    });

    sortDecisions();
    currentPage = 1;
    renderResults();
  }

  function clearFilters() {
    els.searchInput.value = "";
    els.filterCategory.value = "";
    els.filterSubcategory.innerHTML = '<option value="">All sub-categories</option>';
    els.filterRegion.value = "";
    els.filterYearFrom.value = "";
    els.filterYearTo.value = "";
    applyFilters();
  }

  function onSortChange() {
    currentSort = els.sortBy.value;
    sortDecisions();
    currentPage = 1;
    renderResults();
  }

  function sortDecisions() {
    var sort = currentSort;
    filteredDecisions.sort(function (a, b) {
      switch (sort) {
        case "date-desc":
          return (b.decision_date || "").localeCompare(a.decision_date || "");
        case "date-asc":
          return (a.decision_date || "").localeCompare(b.decision_date || "");
        case "address-asc":
          return (a.property_address || "").localeCompare(b.property_address || "");
        case "address-desc":
          return (b.property_address || "").localeCompare(a.property_address || "");
        default:
          return 0;
      }
    });
  }

  function renderResults() {
    var total = filteredDecisions.length;
    var totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (currentPage > totalPages) currentPage = totalPages;

    var start = (currentPage - 1) * PAGE_SIZE;
    var end = Math.min(start + PAGE_SIZE, total);
    var page = filteredDecisions.slice(start, end);

    els.resultsCount.textContent = "(" + total.toLocaleString() + " decision" + (total !== 1 ? "s" : "") + ")";

    // Build table rows
    var html = "";
    if (page.length === 0) {
      html = '<tr><td colspan="6" style="text-align:center;padding:2rem;color:#5a6872;">No decisions match your search criteria.</td></tr>';
    } else {
      page.forEach(function (d) {
        var date = d.decision_date || "—";
        var ref = escapeHTML(d.case_reference || "—");
        var addr = escapeHTML(truncate(d.property_address || "—", 120));
        var addrFull = escapeHTML(d.property_address || "");
        var cat = escapeHTML(d.sub_category_label || d.category_label || "—");
        var region = escapeHTML(d.region_code || "—");
        var url = d.url || "";

        html +=
          "<tr>" +
          '<td class="col-date">' + date + "</td>" +
          '<td class="col-ref">' + ref + "</td>" +
          '<td class="col-address" title="' + addrFull + '">' + addr + "</td>" +
          '<td class="col-category" title="' + escapeHTML(d.sub_category_label || "") + '">' + cat + "</td>" +
          '<td class="col-region">' + region + "</td>" +
          '<td class="col-link">' + (url ? '<a href="' + escapeHTML(url) + '" target="_blank" rel="noopener" title="View on GOV.UK">View</a>' : "—") + "</td>" +
          "</tr>";
      });
    }
    els.resultsBody.innerHTML = html;

    // Pagination
    els.pagePrev.disabled = currentPage <= 1;
    els.pageNext.disabled = currentPage >= totalPages;
    els.pageInfo.textContent = "Page " + currentPage + " of " + totalPages.toLocaleString();

    // Scroll results into view if not first load
    if (document.activeElement && document.activeElement !== document.body) {
      els.resultsBody.closest("section").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function prevPage() {
    if (currentPage > 1) {
      currentPage--;
      renderResults();
    }
  }

  function nextPage() {
    var totalPages = Math.ceil(filteredDecisions.length / PAGE_SIZE);
    if (currentPage < totalPages) {
      currentPage++;
      renderResults();
    }
  }

  // Utilities

  function escapeHTML(str) {
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  function truncate(str, max) {
    if (str.length <= max) return str;
    return str.substring(0, max) + "…";
  }

  function formatYear(dateStr) {
    if (!dateStr) return "—";
    return dateStr.substring(0, 4);
  }

  // Start
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
