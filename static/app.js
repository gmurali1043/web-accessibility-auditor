// ============================================================================
// Shared helpers used by both the URL Audit tab and the Crawl tab. Each tab
// keeps its own state (urls / discovered pages / batch id) so a run in one
// tab never touches the other tab's results.
// ============================================================================

const STATUS_ORDER = ["FAIL", "REVIEW", "PASS"];
const STATUS_LABELS = { FAIL: "Fail", REVIEW: "Manual Review Required", PASS: "Pass" };
const TIER_LABELS = { automated: "Automated", partial: "Partially Automated", manual: "Manual Review Required" };

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function computeOverall(reports) {
  const totals = { pass: 0, fail: 0, review: 0 };
  reports.forEach(r => {
    totals.pass += r.summary.pass || 0;
    totals.fail += r.summary.fail || 0;
    totals.review += r.summary.review || 0;
  });
  const determinable = totals.pass + totals.fail;
  totals.score_pct = determinable ? Math.round((100 * totals.pass) / determinable) : null;
  totals.determinable = determinable;
  return totals;
}

function renderOverview(container, reports) {
  container.innerHTML = "";
  const totals = computeOverall(reports);

  const overallChip = document.createElement("div");
  overallChip.className = "chip overall-chip";
  const scoreText = totals.score_pct !== null ? `${totals.score_pct}%` : "n/a";
  overallChip.textContent = `Overall conformance score (${reports.length} page${reports.length === 1 ? "" : "s"}): ${scoreText}`;
  container.appendChild(overallChip);

  const chipDefs = [
    ["pass", "Pass", "var(--pass-bg)", "var(--pass)"],
    ["fail", "Fail", "var(--fail-bg)", "var(--fail)"],
    ["review", "Manual review required", "var(--review-bg)", "var(--review)"],
  ];
  chipDefs.forEach(([key, label, bg, color]) => {
    const chip = document.createElement("div");
    chip.className = "chip";
    chip.style.background = bg;
    chip.style.color = color;
    chip.textContent = `${label}: ${totals[key]}`;
    container.appendChild(chip);
  });
}

function renderResultsInto(idPrefix, data) {
  const resultsSection = document.getElementById(`${idPrefix}-results`);
  resultsSection.hidden = false;

  renderOverview(document.getElementById(`${idPrefix}-overview`), data.reports);

  const list = document.getElementById(`${idPrefix}-reportList`);
  list.innerHTML = "";
  data.reports.forEach((report, idx) => list.appendChild(renderPageAccordion(report, idx, idPrefix)));

  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ----------------------------------------------------------------------------
// Page accordion (APG accordion pattern): only the heading button toggles the
// page. The score is plain text; the fail / review / pass counts are separate
// buttons that open the page (if needed) and jump to that status group.
// ----------------------------------------------------------------------------
const STATUS_GROUP_LABELS = { FAIL: "Fail", REVIEW: "Manual review required", PASS: "Pass" };
const STATUS_JUMP_TEXT = { FAIL: "fail", REVIEW: "review", PASS: "pass" };

// WCAG success-criterion names, collected from the test cases in the
// current results so axe issues (which carry only "1.4.3") can show names.
const WCAG_NAMES = {};

function rememberWcag(rows) {
  (rows || []).forEach(r => (r.wcag || []).forEach(w => { WCAG_NAMES[w.sc] = w; }));
}

function wcagLabel(sc) {
  const w = WCAG_NAMES[sc];
  return w ? `${w.sc} ${w.name} (${w.level})` : sc;
}

function setExpanded(btn, panel, open) {
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  panel.hidden = !open;
}

function renderPageAccordion(report, idx, idPrefix) {
  const groupId = `${idPrefix}-page-${idx}`;
  const s = report.summary;
  const score = s.score_pct !== null && s.score_pct !== undefined ? `${s.score_pct}%` : "n/a";
  const counts = { FAIL: s.fail, REVIEW: s.review, PASS: s.pass };
  rememberWcag(report.rows);

  const section = document.createElement("section");
  section.className = "page-acc";
  section.setAttribute("aria-labelledby", `${groupId}-btn`);

  const header = document.createElement("div");
  header.className = "page-acc-header";
  header.innerHTML =
    `<h3 class="page-acc-heading">` +
    `<button type="button" class="page-acc-toggle" id="${groupId}-btn" aria-expanded="false" aria-controls="${groupId}-panel">` +
    `<span class="chevron" aria-hidden="true"></span>` +
    `<span class="page-acc-title">${escapeHtml(report.display_name)}<small>${escapeHtml(report.url)}</small></span>` +
    `</button></h3>` +
    `<div class="page-acc-stats">` +
    `<span class="score-badge">Score: ${escapeHtml(score)}</span>` +
    `<span class="jump-counts" role="group" aria-label="Jump to results">` +
    STATUS_ORDER.map(st => counts[st]
      ? `<button type="button" class="jump-btn ${st.toLowerCase()}" data-status="${st}">${counts[st]} ${STATUS_JUMP_TEXT[st]}` +
        `<span class="visually-hidden">: go to ${STATUS_GROUP_LABELS[st].toLowerCase()} test cases for ${escapeHtml(report.display_name)}</span></button>`
      : `<span class="jump-btn ${st.toLowerCase()} is-empty">0 ${STATUS_JUMP_TEXT[st]}</span>`
    ).join("") +
    `</span></div>`;
  section.appendChild(header);

  const panel = document.createElement("div");
  panel.className = "page-acc-body";
  panel.id = `${groupId}-panel`;
  panel.setAttribute("role", "region");
  panel.setAttribute("aria-labelledby", `${groupId}-btn`);
  section.appendChild(panel);

  const toggle = header.querySelector(".page-acc-toggle");
  setExpanded(toggle, panel, idx === 0);
  toggle.addEventListener("click", () => setExpanded(toggle, panel, panel.hidden));

  const meta = document.createElement("div");
  meta.className = "pdf-meta";
  const bits = [];
  if (report.title) bits.push(`Page title: ${escapeHtml(report.title)}`);
  bits.push(`${s.automated_tier} automated &middot; ${s.partial_tier} partially automated &middot; ${s.manual_tier} manual-only`);
  if (report.errors && report.errors.length) bits.push(`Notes: ${report.errors.map(escapeHtml).join("; ")}`);
  meta.innerHTML = bits.join(" &middot; ");
  panel.appendChild(meta);

  if (!report.rows || !report.rows.length) {
    const p = document.createElement("p");
    p.textContent = "This page could not be analyzed.";
    panel.appendChild(p);
    return section;
  }

  // Toolbar: status filter + expand/collapse all test cases.
  const toolbar = document.createElement("div");
  toolbar.className = "results-toolbar";
  const filterRow = document.createElement("div");
  filterRow.className = "filter-row";
  filterRow.setAttribute("role", "group");
  filterRow.setAttribute("aria-label", `Filter results for ${report.display_name}`);
  filterRow.appendChild(mkFilterChip("all", "All", true));
  STATUS_ORDER.forEach(st => filterRow.appendChild(mkFilterChip(st, STATUS_LABELS[st], false)));
  const bulk = document.createElement("div");
  bulk.className = "bulk-row";
  bulk.innerHTML =
    `<button type="button" class="filter-chip" data-bulk="open">Expand all test cases</button>` +
    `<button type="button" class="filter-chip" data-bulk="close">Collapse all test cases</button>`;
  toolbar.append(filterRow, bulk);
  panel.appendChild(toolbar);

  const groupsEl = document.createElement("div");
  groupsEl.className = "rows-container";
  panel.appendChild(groupsEl);

  STATUS_ORDER.forEach(st => {
    const rows = report.rows.filter(r => r.status === st);
    if (!rows.length) return;
    const group = document.createElement("div");
    group.className = `status-group ${st.toLowerCase()}`;
    group.dataset.status = st;
    const h = document.createElement("h4");
    h.className = "status-group-heading";
    h.id = `${groupId}-grp-${st.toLowerCase()}`;
    h.tabIndex = -1;
    h.textContent = `${STATUS_GROUP_LABELS[st]} (${rows.length})`;
    group.appendChild(h);
    rows.forEach((row, i) => group.appendChild(renderTestRow(row, `${groupId}-${st.toLowerCase()}-${i}`)));
    groupsEl.appendChild(group);
  });

  function applyFilter(status) {
    filterRow.querySelectorAll(".filter-chip").forEach(c => {
      const on = c.dataset.status === status;
      c.classList.toggle("active", on);
      c.setAttribute("aria-pressed", on ? "true" : "false");
    });
    groupsEl.querySelectorAll(".status-group").forEach(g => {
      g.hidden = !(status === "all" || g.dataset.status === status);
    });
  }
  filterRow.querySelectorAll(".filter-chip").forEach(chip =>
    chip.addEventListener("click", () => applyFilter(chip.dataset.status)));

  bulk.querySelectorAll("[data-bulk]").forEach(b => b.addEventListener("click", () => {
    const open = b.dataset.bulk === "open";
    groupsEl.querySelectorAll(".test-row-toggle").forEach(t =>
      setExpanded(t, document.getElementById(t.getAttribute("aria-controls")), open));
  }));

  header.querySelectorAll("button.jump-btn").forEach(btn => btn.addEventListener("click", () => {
    const st = btn.dataset.status;
    setExpanded(toggle, panel, true);
    const group = groupsEl.querySelector(`.status-group[data-status="${st}"]`);
    if (!group) return;
    if (group.hidden) applyFilter("all");
    const heading = group.querySelector(".status-group-heading");
    heading.scrollIntoView({ block: "start" });
    heading.focus({ preventScroll: true });
  }));

  return section;
}

function mkFilterChip(status, label, active) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "filter-chip" + (active ? " active" : "");
  chip.dataset.status = status;
  chip.setAttribute("aria-pressed", active ? "true" : "false");
  chip.textContent = label;
  return chip;
}

// "1) Do this 2) Do that" / "1. Do this 2. Do that" -> ["Do this", "Do that"].
function splitSteps(text) {
  const t = (text || "").trim();
  const marks = [...t.matchAll(/(?:^|\s)(\d{1,2})[.)]\s*(?=[A-Za-z"'])/g)];
  const sequential = marks.length >= 2 && marks.every((m, i) => Number(m[1]) === i + 1);
  if (!sequential) return t ? [t] : [];
  const steps = [];
  const lead = t.slice(0, marks[0].index).trim();
  if (lead) steps.push(lead);
  marks.forEach((m, i) => {
    const start = m.index + m[0].length;
    const end = i + 1 < marks.length ? marks[i + 1].index : t.length;
    steps.push(t.slice(start, end).trim());
  });
  return steps.filter(Boolean);
}

function renderWcagLine(row) {
  if (row.id === "GEN-4-1") {
    return `<span class="ref-label">WCAG:</span> all WCAG 2.2 A/AA rules in axe-core &mdash; each issue below lists its own criterion`;
  }
  if (!row.wcag || !row.wcag.length) {
    return `<span class="ref-label">WCAG:</span> best practice (no direct WCAG 2.2 success criterion)`;
  }
  return `<span class="ref-label">WCAG:</span> ` +
    row.wcag.map(w => `<span class="wcag-tag">${escapeHtml(`${w.sc} ${w.name}`)} <span class="wcag-level">${escapeHtml(w.level)}</span></span>`).join(" ");
}

function renderManualSteps(row) {
  if (!row.procedure && !row.expectation) return "";
  const steps = splitSteps(row.procedure);
  let html = `<div class="manual-steps">`;
  if (row.tool) html += `<p><span class="ref-label">Tool:</span> ${escapeHtml(row.tool)}</p>`;
  if (row.expectation) html += `<p><span class="ref-label">Requirement:</span> ${escapeHtml(row.expectation)}</p>`;
  if (steps.length) {
    html += `<p class="ref-label">Steps to test:</p><ol>` + steps.map(st => `<li>${escapeHtml(st)}</li>`).join("") + `</ol>`;
  }
  if (row.outcomes) html += `<p><span class="ref-label">Pass / fail criteria:</span></p><p class="outcomes">${escapeHtml(row.outcomes)}</p>`;
  html += `</div>`;
  return html;
}

function renderIssue(issue, row, n, total) {
  const wcag = issue.wcag && issue.wcag.length
    ? issue.wcag.map(wcagLabel).join(", ")
    : issue.best_practice ? "Best practice (no WCAG criterion)"
    : (row.wcag || []).length ? row.wcag.map(w => wcagLabel(w.sc)).join(", ") : "Best practice";
  let html = `<div class="issue-card">`;
  html += `<div class="issue-refs"><span class="issue-num">Issue ${n} of ${total}</span>` +
    `<span><span class="ref-label">VGAR:</span> ${escapeHtml(row.id)}</span>` +
    `<span><span class="ref-label">WCAG:</span> ${escapeHtml(wcag)}</span></div>`;
  html += `<div><strong>Location:</strong> <code>${escapeHtml(issue.selector || "(page-level)")}</code></div>`;
  html += `<div>${escapeHtml(issue.message)}</div>`;
  if (issue.html) html += `<div><code>${escapeHtml(issue.html.slice(0, 220))}</code></div>`;
  if (issue.fix) html += `<div class="fix"><strong>Fix:</strong> ${escapeHtml(issue.fix)}</div>`;
  if (issue.help_url) html += `<div><a href="${escapeHtml(issue.help_url)}" target="_blank" rel="noopener">axe rule: ${escapeHtml(issue.rule_id || "details")}<span class="visually-hidden"> (opens in a new tab)</span></a></div>`;
  html += `</div>`;
  return html;
}

function renderTestRow(row, rowId) {
  const div = document.createElement("div");
  const statusClass = row.status.toLowerCase();
  div.className = `test-row ${statusClass}`;
  div.dataset.status = row.status;
  const issues = row.issues || [];
  const countText = issues.length ? `${issues.length} issue${issues.length === 1 ? "" : "s"}` : "";

  let html =
    `<h5 class="test-row-heading"><button type="button" class="test-row-toggle" aria-expanded="false" aria-controls="${rowId}-body">` +
    `<span class="chevron" aria-hidden="true"></span>` +
    `<span class="tr-name">${escapeHtml(row.id)} &mdash; ${escapeHtml(row.title)}</span>` +
    `<span class="badge ${statusClass}">${escapeHtml(STATUS_LABELS[row.status])}</span>` +
    `<span class="tierbadge ${row.automation}">${escapeHtml(TIER_LABELS[row.automation])}</span>` +
    (countText ? `<span class="issue-count">${countText}</span>` : "") +
    `</button></h5>`;

  html += `<div class="test-row-body" id="${rowId}-body" hidden>`;
  html += `<div class="row-refs"><div><span class="ref-label">VGAR ID:</span> ${escapeHtml(row.id)} &middot; ` +
    `<span class="ref-label">Category:</span> ${escapeHtml(row.category)}</div><div>${renderWcagLine(row)}</div></div>`;
  html += `<div class="row-finding"><span class="ref-label">How the tool checks this:</span> ${escapeHtml(row.method)}</div>`;

  if (row.status === "REVIEW") {
    html += `<h6 class="manual-heading">How to test manually</h6>` + renderManualSteps(row);
  }
  if (issues.length) {
    html += `<h6 class="manual-heading">Issues found</h6>`;
    issues.forEach((issue, i) => { html += renderIssue(issue, row, i + 1, issues.length); });
    if (row.issue_count > issues.length) {
      html += `<p class="row-finding"><em>Showing the first ${issues.length} of ${row.issue_count} issues.</em></p>`;
    }
  } else if (row.status === "REVIEW") {
    html += `<div class="row-finding"><em>No automated evidence either way &mdash; confirm manually using the steps above.</em></div>`;
  }
  if (row.status !== "REVIEW" && (row.procedure || row.expectation)) {
    html += `<details class="manual-more"><summary>Manual test steps for ${escapeHtml(row.id)}</summary>${renderManualSteps(row)}</details>`;
  }
  html += `</div>`;
  div.innerHTML = html;

  const btn = div.querySelector(".test-row-toggle");
  const body = div.querySelector(".test-row-body");
  btn.addEventListener("click", () => setExpanded(btn, body, body.hidden));
  return div;
}

function showStatusIn(id, msg, isError) {
  const el = document.getElementById(id);
  el.hidden = false;
  el.textContent = msg;
  el.classList.toggle("error", !!isError);
  el.setAttribute("role", isError ? "alert" : "status");
}
function hideStatusIn(id) {
  document.getElementById(id).hidden = true;
}

// Audits and crawls run as background jobs on the server: POST starts one
// and returns a job id, then we poll until it finishes. onProgress is only
// called when the page count changes, so the aria-live status region isn't
// re-announced on every poll.
const JOB_POLL_MS = 1500;
const MAX_URLS = Number(document.body.dataset.maxUrls) || 25;

async function runJob(endpoint, body, onProgress) {
  const resp = await fetch(endpoint, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const started = await resp.json();
  if (!resp.ok) throw new Error(started.error || "Request failed");

  let lastDone = null;
  for (;;) {
    await new Promise(resolve => setTimeout(resolve, JOB_POLL_MS));
    const pollResp = await fetch(`/api/jobs/${started.job_id}`);
    const job = await pollResp.json();
    if (!pollResp.ok) throw new Error(job.error || "Request failed");
    if (job.status === "done") return job.result;
    if (job.status === "error") throw new Error(job.error || "Request failed");
    if (onProgress && job.progress && job.progress.done !== lastDone) {
      lastDone = job.progress.done;
      onProgress(job.progress);
    }
  }
}

function auditProgressMessage(progress) {
  return `Audited ${progress.done} of ${progress.total} page(s)... this can take a while.`;
}

function wireDownloadButtons(scopeEl, getBatchId) {
  scopeEl.querySelectorAll(".dl-btn").forEach(btn => {
    btn.onclick = () => {
      const batchId = getBatchId();
      if (!batchId) return;
      window.location.href = `/api/download/${batchId}/${btn.dataset.fmt}`;
    };
  });
}

// ============================================================================
// Accessible tabs (roving tabindex, arrow-key navigation)
// ============================================================================
(function initTabs() {
  const tabs = Array.from(document.querySelectorAll(".tab-btn"));
  function activate(tab) {
    tabs.forEach(t => {
      const selected = t === tab;
      t.classList.toggle("active", selected);
      t.setAttribute("aria-selected", selected ? "true" : "false");
      t.tabIndex = selected ? 0 : -1;
      const panel = document.getElementById("tab-" + t.dataset.tab);
      panel.hidden = !selected;
      panel.classList.toggle("active", selected);
    });
    tab.focus();
  }
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", e => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const dir = e.key === "ArrowRight" ? 1 : -1;
        const next = tabs[(i + dir + tabs.length) % tabs.length];
        activate(next);
      }
    });
  });
})();

// ============================================================================
// Info-icon tooltips (Max pages / Max depth). CSS alone shows the bubble on
// hover/keyboard focus (and aria-describedby means screen readers announce
// it whenever the button receives focus, independent of any click). This
// JS layers in click-to-toggle "pinning" plus Escape/outside-click to
// dismiss, for users who click the icon rather than just tabbing to it.
// ============================================================================
(function initInfoTooltips() {
  const tips = Array.from(document.querySelectorAll(".info-tip"));
  function closeAll(except) {
    tips.forEach(tip => {
      if (tip === except) return;
      tip.classList.remove("is-open");
      tip.querySelector(".info-btn").setAttribute("aria-expanded", "false");
    });
  }
  tips.forEach(tip => {
    const btn = tip.querySelector(".info-btn");
    btn.addEventListener("click", () => {
      const isOpen = tip.classList.contains("is-open");
      closeAll(tip);
      tip.classList.toggle("is-open", !isOpen);
      btn.setAttribute("aria-expanded", String(!isOpen));
    });
    btn.addEventListener("keydown", e => {
      if (e.key === "Escape" && tip.classList.contains("is-open")) {
        e.preventDefault();
        tip.classList.remove("is-open");
        btn.setAttribute("aria-expanded", "false");
      }
    });
  });
  document.addEventListener("click", e => {
    if (!e.target.closest(".info-tip")) closeAll();
  });
})();

// ============================================================================
// URL Audit tab
// ============================================================================
const AuditTab = (() => {
  let batchId = null;
  const urlInput = document.getElementById("urlInput");
  const runBtn = document.getElementById("runAuditBtn");
  const resultsEl = document.getElementById("audit-results");

  runBtn.addEventListener("click", async () => {
    const urls = urlInput.value.split("\n").map(s => s.trim()).filter(Boolean);
    if (!urls.length) { showStatusIn("audit-status", "Enter at least one URL.", true); return; }
    resultsEl.hidden = true;
    runBtn.disabled = true;
    showStatusIn("audit-status", `Auditing ${urls.length} page(s)... this runs axe-core plus ~45 custom checks per page, including keyboard tests of menus and accordions, and can take a while.`);
    try {
      const data = await runJob("/api/audit", { urls },
        progress => progress.done && showStatusIn("audit-status", auditProgressMessage(progress)));
      hideStatusIn("audit-status");
      batchId = data.batch_id;
      renderResultsInto("audit", data);
    } catch (e) {
      showStatusIn("audit-status", "Error: " + e.message, true);
    } finally {
      runBtn.disabled = false;
    }
  });

  wireDownloadButtons(resultsEl, () => batchId);
  return {};
})();

// ============================================================================
// Crawl tab: discover pages -> user selects -> run audit on selection only
// ============================================================================
const CrawlTab = (() => {
  let discovered = []; // [{url, title}]
  let batchId = null;

  const discoverBtn = document.getElementById("runDiscoverBtn");
  const domainInput = document.getElementById("domainInput");
  const maxPagesInput = document.getElementById("maxPagesInput");
  const maxDepthInput = document.getElementById("maxDepthInput");
  const useSitemapInput = document.getElementById("useSitemapInput");
  const pickerSection = document.getElementById("crawl-picker");
  const checklist = document.getElementById("pageCheckList");
  const selectAll = document.getElementById("selectAllPages");
  const countLabel = document.getElementById("page-selected-count");
  const runCheckBtn = document.getElementById("runCrawlAuditBtn");
  const resultsEl = document.getElementById("crawl-results");

  discoverBtn.addEventListener("click", async () => {
    const url = domainInput.value.trim();
    if (!url) { showStatusIn("crawl-discover-status", "Enter a domain or URL.", true); return; }

    pickerSection.hidden = true;
    document.getElementById("crawl-results").hidden = true;
    discoverBtn.disabled = true;
    showStatusIn("crawl-discover-status", `Crawling ${url}... each page is rendered in a real browser, so this can take a while for a large Max pages value.`);
    try {
      const data = await runJob("/api/crawl-discover", {
        url,
        max_pages: Number(maxPagesInput.value) || 40,
        max_depth: Number(maxDepthInput.value) || 3,
        use_sitemap: useSitemapInput.checked,
      });

      discovered = data.pages || [];
      const sitemapCount = data.sitemap_seed_count || 0;
      const foundVia = sitemapCount
        ? `Found ${discovered.length} page(s) — ${sitemapCount} from sitemap.xml, ${discovered.length - sitemapCount} discovered via links.`
        : useSitemapInput.checked
          ? `Found ${discovered.length} page(s). No sitemap.xml was found (or it had no usable entries), so this is a link-following crawl only.`
          : `Found ${discovered.length} page(s) via link-following.`;
      if (data.errors && data.errors.length) {
        const msgs = data.errors.slice(0, 5).map(e => `${e.url}: ${e.error}`).join(" | ");
        showStatusIn("crawl-discover-status", `${foundVia} Some links had issues: ${msgs}`, discovered.length === 0);
      } else {
        showStatusIn("crawl-discover-status", foundVia);
      }
      renderChecklist();
    } catch (e) {
      showStatusIn("crawl-discover-status", "Error: " + e.message, true);
    } finally {
      discoverBtn.disabled = false;
    }
  });

  function renderChecklist() {
    checklist.innerHTML = "";
    if (!discovered.length) {
      pickerSection.hidden = true;
      return;
    }
    discovered.forEach((item, i) => {
      const li = document.createElement("li");
      const id = `pagecb-${i}`;
      const sourceTag = item.source === "sitemap" ? " &middot; via sitemap.xml" : "";
      li.innerHTML =
        `<label for="${id}">` +
        `<input type="checkbox" id="${id}" class="pdf-cb" data-idx="${i}">` +
        `<span class="pdf-cb-name">${escapeHtml(item.title)}</span>` +
        `<span class="pdf-cb-meta">${escapeHtml(item.url)}${sourceTag}</span>` +
        `</label>`;
      checklist.appendChild(li);
    });
    pickerSection.hidden = false;
    selectAll.checked = false;
    selectAll.indeterminate = false;
    checklist.querySelectorAll(".pdf-cb").forEach(cb => cb.addEventListener("change", updateSelectionState));
    updateSelectionState();
    pickerSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  selectAll.addEventListener("change", () => {
    checklist.querySelectorAll(".pdf-cb").forEach(cb => { cb.checked = selectAll.checked; });
    updateSelectionState();
  });

  function getCheckboxes() {
    return Array.from(checklist.querySelectorAll(".pdf-cb"));
  }

  function updateSelectionState() {
    const boxes = getCheckboxes();
    const checked = boxes.filter(cb => cb.checked);
    countLabel.textContent = `${checked.length} of ${boxes.length} selected`;
    runCheckBtn.disabled = checked.length === 0;
    const allChecked = boxes.length > 0 && checked.length === boxes.length;
    const someChecked = checked.length > 0 && checked.length < boxes.length;
    selectAll.checked = allChecked;
    selectAll.indeterminate = someChecked;
    // The DOM `indeterminate` property alone isn't reliably exposed to every
    // screen reader/browser combo, so mirror it explicitly as aria-checked
    // ("mixed" = partially checked). Remove the attribute otherwise so the
    // native checked/unchecked state (which is announced automatically)
    // isn't overridden by a stale aria-checked value.
    if (someChecked) {
      selectAll.setAttribute("aria-checked", "mixed");
    } else {
      selectAll.removeAttribute("aria-checked");
    }
  }

  runCheckBtn.addEventListener("click", async () => {
    const boxes = getCheckboxes().filter(cb => cb.checked);
    const urls = boxes.map(cb => discovered[Number(cb.dataset.idx)].url);
    if (!urls.length) return;
    if (urls.length > MAX_URLS) {
      showStatusIn("crawl-status", `${urls.length} pages selected, but a single audit run is capped at ${MAX_URLS}. Please select ${MAX_URLS} or fewer.`, true);
      return;
    }

    document.getElementById("crawl-results").hidden = true;
    runCheckBtn.disabled = true;
    showStatusIn("crawl-status", `Auditing ${urls.length} page(s)... this can take a while.`);
    try {
      const data = await runJob("/api/audit", { urls },
        progress => progress.done && showStatusIn("crawl-status", auditProgressMessage(progress)));
      hideStatusIn("crawl-status");
      batchId = data.batch_id;
      renderResultsInto("crawl", data);
    } catch (e) {
      showStatusIn("crawl-status", "Error: " + e.message, true);
    } finally {
      runCheckBtn.disabled = false;
    }
  });

  wireDownloadButtons(resultsEl, () => batchId);
  return {};
})();

// ============================================================================
// Test case mapping dialog
// ============================================================================
(function initTestCasesDialog() {
  const link = document.getElementById("footerTestCasesLink");
  const dialog = document.getElementById("testCasesDialog");
  const closeBtn = document.getElementById("closeTestCasesDialog");
  let loaded = false;

  link.addEventListener("click", async (e) => {
    e.preventDefault();
    if (!loaded) {
      try {
        const resp = await fetch("/api/test-cases");
        const data = await resp.json();
        renderTestCases(data);
        loaded = true;
      } catch (e) {
        document.getElementById("testCasesTableWrap").textContent = "Could not load test cases: " + e.message;
      }
    }
    dialog.showModal();
  });
  closeBtn.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });

  function renderTestCases(data) {
    const summary = document.getElementById("testCasesSummary");
    const c = data.counts;
    summary.innerHTML =
      `<div class="chip overall-chip">${c.total} test cases total</div>` +
      `<div class="chip" style="background:var(--automated-bg);color:var(--automated)">Automated: ${c.automated}</div>` +
      `<div class="chip" style="background:var(--partial-bg);color:var(--partial)">Partially automated: ${c.partial}</div>` +
      `<div class="chip" style="background:var(--manual-bg);color:var(--manual)">Manual review required: ${c.manual}</div>`;

    const wrap = document.getElementById("testCasesTableWrap");
    let html = `<table class="tc-table"><thead><tr><th>VGAR ID</th><th>Requirement</th><th>WCAG 2.2</th><th>Category</th><th>Automation</th><th>Method / rationale</th></tr></thead><tbody>`;
    data.cases.forEach(tc => {
      const wcag = tc.id === "GEN-4-1" ? "All axe-core A/AA rules"
        : (tc.wcag || []).length ? tc.wcag.map(w => `${w.sc} ${w.name} (${w.level})`).join("; ") : "Best practice";
      html += `<tr><td>${escapeHtml(tc.id)}</td><td>${escapeHtml(tc.title)}</td><td>${escapeHtml(wcag)}</td><td>${escapeHtml(tc.category)}</td>` +
        `<td><span class="tierbadge ${tc.automation}">${escapeHtml(TIER_LABELS[tc.automation])}</span></td>` +
        `<td>${escapeHtml(tc.method)}</td></tr>`;
    });
    html += `</tbody></table>`;
    wrap.innerHTML = html;
  }
})();
