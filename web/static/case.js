// TieTrace "Create a case" wizard (decided by Aaryan, Oct 18). Plain JS, no build step; uses el() and $() from app.js.
// Every step is a human click handled by plain server code; no model is involved. Three gates, as in
// .claude/skills/case-builder: GATE 0 tick the family and topic, GATE 1 read the estimate, GATE 2 confirm the build.
// Everything from the server is put on the page with textContent, never innerHTML.
"use strict";
(() => {
  const wiz = $("wizard"), wzStatus = $("wz-status"), describe = $("wz-describe");
  const pickBox = $("wz-pick"), estBox = $("wz-estimate"), buildBox = $("wz-build");
  const company = $("wz-company"), topic = $("wz-topic"), slugIn = $("wz-slug"), y0 = $("wz-y0"), y1 = $("wz-y1");
  let slugTouched = false, wzBusy = false;

  async function post(url, body) {
    try {
      const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      return await r.json().catch(() => ({ ok: false, error: { message: `The server said ${r.status}.` } }));
    } catch (e) { return { ok: false, error: { message: "Could not reach the TieTrace web server." } }; }
  }
  const errOf = (j) => (j.error && (j.error.message || j.error.code)) || (Array.isArray(j.detail) ? "Some input is not valid." : "Refused.");
  const fail = (box, j) => box.append(el("p", { class: "msg error", role: "alert", text: errOf(j) }));

  function show(on) {
    document.body.classList.toggle("wiz-on", on);
    wiz.hidden = !on;
    $("top-title").textContent = on ? "New case" : (current ? String(current.title) : "");
    if (on) { closeDrawer(); company.focus(); }
  }
  $("new-case").addEventListener("click", () => {
    if (busy) { statusEl.textContent = "Wait for the running check to finish first."; return; }
    show(true);
  });
  $("new-check").addEventListener("click", () => show(false));
  $("history").addEventListener("click", (ev) => { if (ev.target.closest("button.open")) show(false); });

  // ---------- step 1: describe ----------
  y0.value = 2010; y1.value = new Date().getFullYear();
  const slugify = (s) => s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  function suggest() {
    if (slugTouched) return;
    const c = slugify(company.value).split("-").slice(0, 2).join("-"), t = slugify(topic.value).split("-")[0];
    slugIn.value = [c, t].filter(Boolean).join("-").slice(0, 40).replace(/-+$/, "");
  }
  company.addEventListener("input", suggest); topic.addEventListener("input", suggest);
  slugIn.addEventListener("input", () => { slugTouched = slugIn.value !== ""; });

  function reset(...boxes) { for (const b of boxes) { b.replaceChildren(); b.hidden = true; } }

  describe.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (wzBusy) return;
    reset(pickBox, estBox, buildBox);
    wzBusy = true; $("wz-find").disabled = true;
    wzStatus.textContent = "Searching OpenAlex for funders, institutions and topics…";
    const j = await post("/api/case/discover", { slug: slugIn.value.trim(), company: company.value.trim(), topic: topic.value.trim() });
    wzBusy = false; $("wz-find").disabled = false; wzStatus.textContent = "";
    pickBox.hidden = false;
    if (!j.ok) { fail(pickBox, j); return; }
    renderPick(j);
  });

  // ---------- step 2 (GATE 0): the human ticks candidates; step 3: write case.json ----------
  function select(options, value, label) {
    const s = el("select", { "aria-label": label });
    for (const o of options) s.append(el("option", { value: o, text: o }));
    s.value = value;
    return s;
  }
  function candidate(kind, c, extra) {
    const id = `wz-${kind}-${c.id}`;
    const box = el("input", { type: "checkbox", id, value: c.id });
    const bits = [c.id, c.type, c.country, c.works_count != null && `${c.works_count} works`, c.subfield].filter(Boolean).join(", ");
    const desc = c.description ? String(c.description).slice(0, 240) : "";
    const fam = [...(c.parents || []).map((p) => `parent: ${p.name} (${p.id})`), ...(c.children || []).map((p) => `child: ${p.name} (${p.id})`)].join("; ");
    const row = el("div", { class: "wz-cand" }, box,
      el("label", { for: id }, el("strong", { text: c.name || c.id }), el("small", { text: bits }),
        desc ? el("small", { text: desc }) : null, fam ? el("small", { text: fam }) : null),
      extra || el("span"));
    row.dataset.kind = kind;
    return row;
  }
  function renderPick(j) {
    const rows = [];
    pickBox.append(el("h2", { text: "Pick the company family and the topic (GATE 0)" }),
      el("p", { class: "basis", text: "Nothing is ticked for you. Tick only what belongs to this company, and give each a role. Leave out anything you are not sure of." }),
      el("p", { class: "basis", text: "Author discovery is skipped here: experts are resolved later, in a check (or run discover.py authors in a terminal)." }),
      el("p", { class: "basis", text: `This discovery made ${j.api_calls} OpenAlex calls, about $${j.cost_usd}. ${j.cost_note}` }));
    const groups = [["funders", "Funders (money on papers)"], ["institutions", "Institutions (author affiliations; exactly one parent)"], ["topics", "Topics (the loader keeps works whose primary topic is ticked)"]];
    for (const [kind, title] of groups) {
      pickBox.append(el("h3", { text: title }));
      const list = j[kind] || [];
      if (!list.length) pickBox.append(el("p", { class: "basis", text: "No candidates found." }));
      for (const c of list) {
        const extra = kind === "funders" ? select(["company", "foundation"], /foundation/i.test(c.name || "") ? "foundation" : "company", `Kind of ${c.name}`)
          : kind === "institutions" ? select(["parent", "subsidiary", "bottler"], "subsidiary", `Role of ${c.name}`) : null;
        const row = candidate(kind, c, extra);
        rows.push(row); pickBox.append(row);
      }
    }
    if (j.phrase_works != null) pickBox.append(el("p", { class: "basis", text: `Works with the exact phrase in title or abstract: ${j.phrase_works} (for scale only; the loader filters by topic id).` }));
    const d = j.defaults || {};
    pickBox.append(el("h3", { text: "Thresholds (copied from philip-morris-lung)" }),
      el("dl", {}, ...Object.entries(d).flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: String(v) })]), el("dt", { text: "public_ids" }), el("dd", { text: "none (not a public case)" })));
    const rid = "wz-reviewer";
    const reviewer = el("input", { type: "text", id: rid, required: "", maxlength: "60", autocomplete: "name" });
    const go = el("button", { type: "submit", class: "primary", text: "Approve and write case.json" });
    const f = el("form", { class: "inline-form" }, el("label", { for: rid, text: "Your name (recorded in decided_by for every decision)" }), reviewer, el("div", { class: "row" }, go));
    pickBox.append(f);
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (wzBusy || !reviewer.value.trim()) return;
      const picked = (kind) => rows.filter((r) => r.dataset.kind === kind && r.querySelector("input").checked).map((r) => {
        const s = r.querySelector("select"), id = r.querySelector("input").value;
        return kind === "funders" ? { id, kind: s.value } : kind === "institutions" ? { id, rel: s.value } : { id };
      });
      const body = { slug: slugIn.value.trim(), company: company.value.trim(), years: [Number(y0.value), Number(y1.value)],
        reviewer: reviewer.value.trim(), funders: picked("funders"), institutions: picked("institutions"), topics: picked("topics") };
      wzBusy = true; go.disabled = true;
      const out = await post("/api/case/write", body);
      wzBusy = false; go.disabled = false;
      f.querySelectorAll(".msg.error").forEach((x) => x.remove());
      if (!out.ok) { fail(f, out); return; }
      go.disabled = true; describe.querySelectorAll("input, button").forEach((x) => { x.disabled = true; });
      rows.forEach((r) => r.querySelectorAll("input, select").forEach((x) => { x.disabled = true; }));
      f.append(el("p", { class: "basis", text: `Wrote ${out.path}:` }), el("pre", { class: "md", text: JSON.stringify(out.case, null, 2) }));
      renderEstimate(out.slug);
    });
  }

  // ---------- step 4 (GATE 1): the dry-run estimate ----------
  function renderEstimate(slug) {
    estBox.hidden = false;
    const go = el("button", { type: "button", class: "primary", text: "Estimate the build (dry run)" });
    estBox.append(el("h2", { text: "Read the estimate (GATE 1)" }),
      el("p", { class: "basis", text: "The dry run makes a few OpenAlex count calls and writes nothing." }), el("div", { class: "row" }, go));
    go.addEventListener("click", async () => {
      if (wzBusy) return;
      wzBusy = true; go.disabled = true; wzStatus.textContent = "Counting works in OpenAlex…";
      const j = await post("/api/case/estimate", { slug });
      wzBusy = false; wzStatus.textContent = "";
      if (!j.ok) { go.disabled = false; fail(estBox, j); return; }
      const e = j.estimate || {}, r = j.ram || {};
      estBox.append(el("dl", {},
        el("dt", { text: "Works (before duplicates merge)" }), el("dd", { text: String(e.works) }),
        el("dt", { text: "OpenAlex API calls" }), el("dd", { text: `about ${e.api_calls} (max_api_calls ${e.max_api_calls})` }),
        el("dt", { text: "Cost" }), el("dd", { text: `about $${e.cost_usd}` }),
        el("dt", { text: "Projected graph RAM" }), el("dd", { text: `about ${e.graph_mb} MB` }),
        el("dt", { text: "RAM check" }), el("dd", { text: r.problem ? `fails: ${r.problem}` : `passes (new graph about ${r.estimate_mb} MB, from ${r.source})` }),
        el("dt", { text: "This dry run used" }), el("dd", { text: `${e.dry_run_calls} API calls` })));
      renderBuild(slug, !r.problem);
    });
  }

  // ---------- step 5 (GATE 2): confirm, build, follow the log ----------
  function renderBuild(slug, ramOk) {
    reset(buildBox); buildBox.hidden = false;
    const go = el("button", { type: "button", class: "primary", text: "Build…" });
    buildBox.append(el("h2", { text: "Build the graph (GATE 2)" }),
      el("p", { class: "basis", text: ramOk ? "The build downloads the works from OpenAlex and loads them into FalkorDB. It can take a while; one build runs at a time."
        : "The RAM check fails, so the server will refuse the build. Free RAM first, then estimate again." }), el("div", { class: "row" }, go));
    go.disabled = !ramOk;
    go.addEventListener("click", async () => {
      go.disabled = true;
      const c = await post("/api/confirm", { action: "build_case", params: { slug } });
      if (!c.ok) { go.disabled = false; fail(buildBox, c); return; }
      const yes = el("button", { type: "button", class: "primary", text: "Yes, build it" }), no = el("button", { type: "button", text: "Cancel" });
      const box = el("div", { class: "inline-form", role: "group", "aria-label": "Confirm the build" },
        el("p", { class: "confirm-text", text: `Build the research graph for case ${slug} now? This spends OpenAlex API calls (see the estimate).` }), el("div", { class: "row" }, yes, no));
      buildBox.append(box); yes.focus();
      no.addEventListener("click", () => { box.remove(); go.disabled = false; });
      yes.addEventListener("click", async () => {
        yes.disabled = no.disabled = true;
        const out = await post("/api/case/build", { slug, confirm: c.token });
        box.remove();
        if (!out.ok) { go.disabled = false; fail(buildBox, out); return; }
        follow(slug);
      });
    });
  }
  function follow(slug) {
    const state = el("p", { class: "basis", role: "status", text: "Build started…" }), log = el("pre", { class: "md" });
    buildBox.append(state, log);
    const tick = async () => {
      let j;
      try { j = await (await fetch(`/api/case/status?slug=${encodeURIComponent(slug)}`)).json(); } catch (e) { j = null; }
      if (!j || !j.ok) { state.textContent = "Lost the connection to the TieTrace web server; trying again…"; setTimeout(tick, 4000); return; }
      log.textContent = (j.log || []).join("\n"); log.scrollTop = log.scrollHeight;
      if (j.running) { state.textContent = `Building… (status: ${j.status || "starting"})`; setTimeout(tick, 2000); return; }
      if (j.returncode === 0 && j.listed) state.textContent = `Case ready. ${slug} now appears in list_cases with its graph loaded (${j.data_version || "data version unknown"}). Start a new check to use it.`;
      else if (j.returncode === 0) state.textContent = `The build ended, but list_cases does not show the graph of ${slug} yet. Read the log.`;
      else state.textContent = `The build failed (exit code ${j.returncode}${j.error ? `: ${j.error}` : ""}). Read the log; the case.json stays.`;
    };
    tick();
  }
})();
