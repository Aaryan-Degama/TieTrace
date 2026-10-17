// TieTrace chat page (WebApp.md W4, W5). Plain JS, no build step.
// Everything from the server is put on the page with textContent, never innerHTML.
"use strict";

const $ = (id) => document.getElementById(id);
const messages = $("messages"), steps = $("steps"), form = $("ask-form"), storyBox = $("story");
const sendBtn = $("send"), statusEl = $("status");
let sessionId = null;   // claude session, so a name pick continues the same check
let busy = false;

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else e.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) e.append(k);
  return e;
}
const pct = (x) => (typeof x === "number" ? `${Math.round(x * 1000) / 10}%` : "unknown");
function doiLink(doi) {
  let href;
  try { href = "https://doi.org/" + encodeURI(String(doi)); } catch (e) { return document.createTextNode(String(doi)); } // lone surrogate: plain text
  return el("a", { href, target: "_blank", rel: "noopener", text: doi });
}
const scrollDown = () => { messages.scrollTop = messages.scrollHeight; };

// ---------- asking ----------

document.querySelectorAll(".example").forEach((b) =>
  b.addEventListener("click", () => { storyBox.value = b.dataset.story; storyBox.focus(); }));

form.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const story = storyBox.value.trim();
  if (story && !busy) { storyBox.value = ""; ask(story, sessionId); }
});

async function ask(story, sid) {
  let reader = null;
  busy = true; sendBtn.disabled = true; statusEl.textContent = "Checking… this can take a minute or two.";
  messages.append(el("div", { class: "msg user", text: story }));
  const reply = { box: el("div", { class: "msg reply" }), cards: el("div", { class: "cards" }), byId: {} };
  reply.box.append(reply.cards);
  messages.append(reply.box); scrollDown();
  try {
    const res = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ story, session_id: sid }) });
    if (!res.ok || !res.body) {
      const j = await res.json().catch(() => ({}));
      showError(reply, (j.error && j.error.message) || `The server said ${res.status}.`);
      return;
    }
    reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = /^event: (.*)$/m.exec(frame), data = /^data: (.*)$/m.exec(frame);
        if (ev && data) handle(reply, ev[1], JSON.parse(data[1]));
      }
    }
  } catch (e) {
    showError(reply, "Lost the connection to the TieTrace web server. Is it still running?");
  } finally {
    if (reader) reader.cancel().catch(() => {});  // stop reading (and let the server stop claude) on any exit
    busy = false; sendBtn.disabled = false; statusEl.textContent = ""; scrollDown();
  }
}

function handle(reply, event, d) {
  if (event === "step") step(d);
  else if (event === "expert") card(reply, d);
  else if (event === "ambiguous") picker(reply, d);
  else if (event === "note") note(reply, d);
  else if (event === "text") replyText(reply, d.text);
  else if (event === "error") showError(reply, d.message);
  else if (event === "warning") reply.box.append(el("div", { class: "msg error", role: "status", text: d.message }));
  else if (event === "done") sessionId = d.session_id || sessionId;
  scrollDown();
}

function showError(reply, message) { reply.box.append(el("div", { class: "msg error", role: "alert", text: message })); }

function replyText(reply, text) {
  for (const para of text.split(/\n{2,}/)) {
    const p = el("p");
    // DOIs become links; everything else stays plain text
    for (const part of para.split(/(10\.\d{4,9}\/[^\s,;)\]]+)/)) p.append(/^10\.\d{4,9}\//.test(part) ? doiLink(part) : part);
    reply.box.append(p);
  }
}

// ---------- evidence panel ----------

const running = {};   // tool -> list of <li> still running
function step(d) {
  $("ev-empty").hidden = true;
  if (d.status === "running") {
    const li = el("li", { class: "step running" }, el("span", { class: "name", text: d.tool }), el("span", { class: "state", text: "running" }));
    (running[d.tool] = running[d.tool] || []).push(li);
    steps.append(li);
    return;
  }
  const li = (running[d.tool] || []).shift() || steps.appendChild(el("li", { class: "step" }, el("span", { class: "name", text: d.tool }), el("span", { class: "state" })));
  li.classList.remove("running");
  const state = li.querySelector(".state");
  if (d.ok) state.textContent = "done";
  else { li.classList.add("failed"); state.textContent = "failed: " + ((d.error && (d.error.code + " " + (d.error.message || ""))) || d.error || ""); }
  const s = d.scope || {};
  const bits = [s.graph, s.data_version, s.hops != null && `${s.hops} hops`, s.years,
    s.funder_coverage != null && `funder coverage ${pct(s.funder_coverage)}`].filter(Boolean);
  if (bits.length) li.append(el("p", { class: "line", text: "Scope: " + bits.join(" · ") }));
  if (d.verdicts_applied && d.verdicts_applied.length) li.append(el("p", { class: "line", text: "Verdicts applied: " + d.verdicts_applied.join(", ") }));
  for (const w of d.warnings || []) li.append(el("p", { class: "line", text: "Warning: " + w }));
  const cy = d.cypher || [];
  if (cy.length) {
    const det = el("details", {}, el("summary", { text: `Cypher (${cy.length} quer${cy.length === 1 ? "y" : "ies"})` }));
    for (const c of cy) det.append(el("pre", { class: "cypher", text: `// ${c.graph}${c.runs ? `, ${c.runs} run(s)` : ""}\n${c.query}\n// params ${c.params}` }));
    det.open = cy.length <= 3;
    li.append(det);
  }
}

// ---------- expert cards (Expert #n only: the server never sends a name) ----------

function card(reply, d) {
  const c = reply.byId[d.expert_id] = Object.assign(reply.byId[d.expert_id] || {}, d);
  if (!c.node) { c.node = el("article", { class: "card" }); reply.cards.append(c.node); }
  const n = c.node; n.replaceChildren();
  const tier = c.error ? "ERR" : (c.tier || (c.no_tie ? "NONE" : "?"));
  n.append(el("h3", {}, el("span", { class: `chip ${tier}`, text: c.error ? "not checked" : tier }), el("span", { text: c.label || "Expert #?" })));
  if (c.error) n.append(el("p", { class: "basis", text: `The check failed (${c.error}); nothing is said about this expert.` }));
  if (c.tier_basis) n.append(el("p", { class: "basis", text: c.tier_basis }));
  if (c.evidence && c.evidence.length) {
    const ul = el("ul");
    for (const e of c.evidence) {
      const li = el("li", {}, doiLink(e.doi), el("span", { class: "meta", text: ` · ${e.kind === "FUNDED_BY" ? "funded by" : "affiliation with"} ${e.via} · tier ${e.tier} · ${e.topic_label} · ${e.year}` }), " ");
      li.append(excludeButton(li, e.doi, c.case || null));
      ul.append(li);
    }
    n.append(el("p", { class: "basis", text: `Evidence (${c.evidence.length} item${c.evidence.length === 1 ? "" : "s"}):` }), ul);
  } else if (c.tier === "A1" || c.tier === "A2") {
    n.append(el("p", { class: "basis", text: "The DOIs come with the detailed report for this expert." }));
  }
  if (c.no_tie) {
    const t = c.no_tie;
    if (t.error || !t.result) n.append(el("div", { class: "scope", text: `The no-tie search failed (${t.error || "no result"}); nothing is said about this expert.` }));
    else if (t.result === "NO_TIE_FOUND")
      n.append(el("div", { class: "scope", text: `No tie found in the research record within ${t.hops} co-author hops, years ${t.years} (${t.query_name}). Funder coverage: ${pct(t.funder_coverage)} of topic works in the graph list a funder; ${pct(t.expert_funder_coverage)} of this expert's works do (${pct(t.coverage)} list a funder or an affiliation). Absence is not proof.` }));
    else
      n.append(el("div", { class: "scope", text: `A Tier-A person is within ${t.nearest ? t.nearest.hops : "?"} co-author hops (${t.query_name}; searched ${t.hops} hops, years ${t.years}; funder coverage ${pct(t.funder_coverage)} of topic works, ${pct(t.expert_funder_coverage)} of this expert's works). Network context only.` }));
  }
}

// ---------- ambiguous names: the reporter picks ----------

function picker(reply, d) {
  const name = `pick-${Math.random().toString(36).slice(2)}`;
  const fs = el("fieldset", {}, el("legend", { text: d.status === "NOT_FOUND" ? `${d.name_ref}: not found in this graph` : `${d.name_ref}: which author is meant?` }));
  for (const c of d.candidates || []) {
    const id = `${name}-${c.expert_id}`;
    fs.append(el("label", { for: id }, el("input", { type: "radio", name, id, value: c.expert_id }),
      ` OpenAlex ${c.expert_id} · ${c.works_on_topic} works on the topic`));
  }
  const box = el("form", { class: "picker" }, fs);
  if ((d.candidates || []).length) {
    const btn = el("button", { type: "submit", class: "primary", text: "Use this author" });
    box.append(btn);
    box.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const chosen = box.querySelector("input:checked");
      if (!chosen || busy) return;
      btn.disabled = true;
      ask(`For ${d.name_ref}, use OpenAlex author ${chosen.value}. Continue the check.`, sessionId);
    });
  }
  reply.box.append(box);
}

// ---------- human gates: click, read, confirm (a one-time server token per action) ----------

async function gate(container, action, params, summary, onDone) {
  const res = await fetch("/api/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, params }) });
  const j = await res.json();
  if (!j.ok) { container.append(el("p", { class: "msg error", text: j.error.message })); return; }
  const yes = el("button", { type: "button", class: "primary", text: "Yes, do it" });
  const no = el("button", { type: "button", text: "Cancel" });
  const box = el("div", { class: "inline-form", role: "group", "aria-label": "Confirm" }, el("p", { class: "confirm-text", text: summary }),
    j.preview ? el("pre", { class: "md", text: j.preview }) : null, el("div", { class: "row" }, yes, no));
  container.append(box); yes.focus();
  no.addEventListener("click", () => box.remove());
  yes.addEventListener("click", async () => {
    yes.disabled = no.disabled = true;
    const r = await fetch(`/api/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...params, confirm: j.token }) });
    const out = await r.json();
    box.remove();
    if (!out.ok) container.append(el("p", { class: "msg error", text: (out.error && `${out.error.code}: ${out.error.message}`) || "Refused." }));
    else onDone(out);
  });
}

function reviewerForm(container, buttonText, extra, start) {
  const id = `rev-${Math.random().toString(36).slice(2)}`;
  const reviewer = el("input", { type: "text", id, required: "", maxlength: "100", autocomplete: "name" });
  const go = el("button", { type: "submit", text: buttonText });
  const f = el("form", { class: "inline-form" }, ...extra, el("label", { for: id, text: "Your name or role (printed in the record)" }), reviewer, el("div", { class: "row" }, go));
  f.addEventListener("submit", (ev) => { ev.preventDefault(); if (reviewer.value.trim()) { go.disabled = true; start(reviewer.value.trim(), f); } });
  container.append(f); reviewer.focus();
}

function note(reply, d) {
  const n = el("div", { class: "card" }, el("h3", { text: `Sourcing note ${d.note_id || ""}` }),
    el("p", { class: "basis", text: `Status: ${d.status}` }));
  for (const r of d.rejections || []) n.append(el("p", { class: "basis", text: `Claim ${r.claim_index} rejected: ${r.reason}` }));
  if (d.preview) n.append(el("p", { class: "basis", text: "The note as it will be approved (read it before approving):" }), el("pre", { class: "md", text: d.preview }));
  if (d.status === "verified") {
    const b = el("button", { type: "button", text: "Approve note…" });
    b.addEventListener("click", () => {
      b.disabled = true;
      reviewerForm(n, "Review approval", [], (reviewer, f) =>
        gate(n, "approve_note", { note_id: d.note_id, reviewer }, `Approve sourcing note ${d.note_id} as "${reviewer}"? This is recorded in the newsroom and cannot be undone.`, (out) => {
          f.remove();
          n.append(el("p", { class: "basis", text: `Approved by ${out.data.approved_by} at ${out.data.approved_at}.` }), el("pre", { class: "md", text: out.data.markdown }));
        }));
    });
    n.append(b);
  }
  reply.box.append(n);
}

function excludeButton(li, doi, kase) {
  const b = el("button", { type: "button", class: "danger", text: "Exclude paper…", "aria-label": `Exclude paper ${doi} from all tie evidence` });
  b.addEventListener("click", () => {
    b.disabled = true;
    const rid = `reason-${Math.random().toString(36).slice(2)}`;
    const reason = el("input", { type: "text", id: rid, required: "", maxlength: "1000" });
    reviewerForm(li, "Review verdict", [el("label", { for: rid, text: `Why should ${doi} be left out of all tie evidence?` }), reason], (reviewer, f) => {
      if (!reason.value.trim()) { f.querySelector("button").disabled = false; reason.focus(); return; }
      gate(li, "record_verdict", { type: "PAPER_EXCLUDED", target: doi, reason: reason.value.trim(), reviewer, case: kase },
        `Exclude ${doi} from all tie evidence in this newsroom, as "${reviewer}"? Every saved check is run again with it.`, (out) => {
          f.remove();
          const im = out.data.impact;
          li.append(el("p", { class: "basis", text: `Verdict ${out.data.verdict_id} recorded. ${im.checks_rechecked} saved check(s) run again; ${im.checks_changed} changed.` }));
          for (const x of im.details) li.append(el("p", { class: "basis", text: `${x.label}: tier ${x.tier_before} → ${x.tier_after}` }));
        });
    });
  });
  return b;
}
