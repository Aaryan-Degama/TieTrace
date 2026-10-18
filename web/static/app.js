// TieTrace chat page (WebApp.md W4, W5; chat layout and browser-only history added by Aaryan, Oct 18). Plain JS, no build step.
// Everything from the server is put on the page with textContent, never innerHTML.
"use strict";

const $ = (id) => document.getElementById(id);
const messages = $("messages"), steps = $("steps"), form = $("ask-form"), storyBox = $("story");
const sendBtn = $("send"), statusEl = $("status");
let sessionId = null;   // claude session, so a name pick continues the same check
let busy = false;
// Agent mode (A5): the visitor's API key stays in this input only. It is sent in the X-Provider-Key header, never stored
// anywhere (no browser storage, no cookie, no URL), and is cleared when the page is left or the server rejects it.
const provSel = $("provider"), keyBox = $("keybox"), keyInput = $("apikey"), ownKey = $("own-key");
let hostGemini = false;   // the server may use its own Gemini key; then the key box is optional
// Public demo (TIETRACE_PUBLIC=1): the server refuses approvals, verdicts and the case wizard; the page just says so.
let publicDemo = false;
const APPROVALS_OFF = "Approvals are turned off on the public demo";
function syncKeyBox() {
  const p = provSel.value, optional = p === "gemini" && hostGemini && !keyInput.value;
  keyBox.hidden = p === "local" || (optional && ownKey.dataset.open !== "1");
  ownKey.hidden = !(p === "gemini" && hostGemini && keyBox.hidden);
}
provSel.addEventListener("change", () => { sessionId = null; keyInput.value = ""; ownKey.dataset.open = ""; syncKeyBox(); });
ownKey.addEventListener("click", () => { ownKey.dataset.open = "1"; syncKeyBox(); keyInput.focus(); });
addEventListener("pagehide", () => { keyInput.value = ""; });
fetch("/api/agent/providers").then((r) => r.json()).then((j) => {
  hostGemini = j.host_gemini === true;
  publicDemo = j.public === true;
  $("public-banner").hidden = !publicDemo;
  $("new-case").hidden = publicDemo;
  for (const p of j.providers || []) {
    // A provider the page has no option for yet (e.g. groq) gets one, so new providers appear by themselves.
    const o = [...provSel.options].find((x) => x.value === p.id) || provSel.appendChild(el("option", { value: p.id }));
    o.textContent = `${p.label} (${p.model})`;
  }
  if (j.local_claude === true) {
    provSel.append(el("option", { value: "local", text: "Local (Claude Code login)" }));
    if (!hostGemini) provSel.value = "local";   // default: Gemini when the host key is on, else Local when offered
  }
  syncKeyBox();
}).catch(() => {});
// A rejected or missing key: forget it and put the cursor back in the key box.
function keyFail(code) {
  if (code === "BAD_KEY" || code === "NO_KEY") { keyInput.value = ""; ownKey.dataset.open = "1"; syncKeyBox(); keyInput.focus(); }
}

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
  b.addEventListener("click", () => { storyBox.value = b.dataset.story; closeDrawer(); storyBox.focus(); }));

form.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const story = storyBox.value.trim();
  if (!story || busy) return;
  // An old check the server can no longer continue: this message starts a new check instead.
  if (current && current.expired) newCheck();
  storyBox.value = ""; ask(story, sessionId);
});
// Enter sends, Shift+Enter starts a new line (as in most chat apps).
storyBox.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); form.requestSubmit(); }
});

function userMsg(text) { messages.append(el("div", { class: "msg user", text })); }
function newReply() {
  const reply = { box: el("div", { class: "msg reply" }), cards: el("div", { class: "cards" }), byId: Object.create(null) };
  reply.box.append(reply.cards);   // byId has no prototype: an expert_id of "__proto__" stays a plain key
  messages.append(reply.box);
  return reply;
}

async function ask(story, sid) {
  let reader = null;
  busy = true; sendBtn.disabled = true; sendBtn.classList.add("busy"); sendBtn.setAttribute("aria-busy", "true");
  statusEl.textContent = "Checking… this can take a minute or two.";
  setEmpty(false);
  // History: the story and every event shown are kept with this conversation (never the API key).
  const conv = current || startConv(story);
  conv.provider = provSel.value;
  conv.items.push({ role: "user", text: story });
  const rec = { role: "reply", events: [] };
  conv.items.push(rec);
  saveHistory(); renderHistory();
  userMsg(story);
  const reply = newReply();
  reply.events = rec.events;
  const prog = reply.progress = progress();   // page only: it is not an event, so it never reaches history
  scrollDown();
  try {
    const prov = provSel.value, headers = { "Content-Type": "application/json" };
    const key = keyInput.value.trim();
    if (prov !== "local" && key) headers["X-Provider-Key"] = key;
    const res = prov === "local"
      ? await fetch("/api/ask", { method: "POST", headers, body: JSON.stringify({ story, session_id: sid }) })
      : await fetch("/api/agent/ask", { method: "POST", headers, body: JSON.stringify({ provider: prov, story, session_id: sid }) });
    if (!res.ok || !res.body) {
      const j = await res.json().catch(() => ({}));
      handle(reply, "error", { message: (j.error && j.error.message) || `The server said ${res.status}.`, code: j.error && j.error.code });
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
    handle(reply, "error", { message: "Lost the connection to the TieTrace web server. Is it still running?" });
  } finally {
    if (reader) reader.cancel().catch(() => {});  // stop reading (and let the server stop claude) on any exit
    if (rec.events.some((e) => e[0] === "error" && e[1] && e[1].code === "SESSION_EXPIRED")) {
      conv.expired = true; sessionId = null;   // the server no longer holds this session's names to hide
    }
    prog.end();   // also on an abnormal end of the stream
    conv.session_id = conv.expired ? null : sessionId; saveHistory();
    busy = false; sendBtn.disabled = false; sendBtn.classList.remove("busy"); sendBtn.removeAttribute("aria-busy"); statusEl.textContent = ""; scrollDown();
  }
}

let replaying = false;   // true while a saved conversation is redrawn: no key handling, nothing saved again
function handle(reply, event, d) {
  if (reply.events && !replaying) { reply.events.push([event, d]); saveHistory(); }
  if (event === "step") { autoEvidence(); step(d); if (reply.progress) reply.progress.step(d); }
  else if (event === "expert") card(reply, d);
  else if (event === "ambiguous") picker(reply, d);
  else if (event === "note") note(reply, d);
  else if (event === "text") replyText(reply, d.text);
  else if (event === "error") {
    showError(reply, d.message);
    if (d.code === "SESSION_EXPIRED") {
      const b = el("button", { type: "button", class: "primary", text: "Start a new check" });
      b.addEventListener("click", () => { if (newCheck()) storyBox.focus(); });
      reply.box.append(b);
    }
    if (d.code === "AUTH" && provSel.value === "local") authHelp(reply.box);
    if (!replaying) keyFail(d.code);
  }
  else if (event === "warning") reply.box.append(el("div", { class: "msg error", role: "status", text: d.message }));
  else if (event === "done") sessionId = d.session_id || sessionId;
  if ((event === "done" || event === "error") && reply.progress) reply.progress.end();
  scrollDown();
}

// Shown whenever Claude Code is not logged in: steps, a Log in button (opens the sign-in page on this machine) and a
// "check again" button. The sign-in itself always happens in the person's own browser; nothing is typed here.
function authHelp(parent) {
  const note = el("p", { class: "status", role: "status" });
  const link = el("a", { href: "#", hidden: "", target: "_blank", rel: "noopener", text: "Open the sign-in page" });
  const login = el("button", { type: "button", class: "primary", text: "Log in with Claude" });
  const again = el("button", { type: "button", text: "I have logged in: check again" });
  login.addEventListener("click", async () => {
    note.textContent = "Starting the sign-in… a browser tab should open. Finish signing in there.";
    try {
      const res = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const j = await res.json();
      if (!j.ok) { note.textContent = (j.error && j.error.message) || "Could not start the sign-in. Use the terminal steps below."; return; }
      if (j.url) { link.href = j.url; link.hidden = false; note.textContent += " If no tab opened, use the link."; }
    } catch (e) { note.textContent = "Could not reach the TieTrace web server."; }
  });
  again.addEventListener("click", async () => {
    try {
      const j = await (await fetch("/api/auth")).json();
      note.textContent = j.logged_in === true ? "Logged in. Send your story again."
        : j.logged_in === false ? "Still not logged in. Finish the sign-in, then check again."
        : "Could not check (is the `claude` command installed?).";
    } catch (e) { note.textContent = "Could not reach the TieTrace web server."; }
  });
  parent.append(el("div", { class: "msg auth" },
    el("strong", { text: "Sign in to Claude Code to continue" }),
    el("ol", {}, el("li", { text: "Press Log in with Claude. A browser tab opens; sign in there." }),
      el("li", { text: "Come back here and press check again." }),
      el("li", { text: "Send your story again." })),
    el("p", { text: "Or, in a terminal in this folder, run:" }), el("code", { text: "claude auth login" }),
    el("div", { class: "row" }, login, again), link, note));
}

// ---------- progress row in the thread while a check runs (Aaryan, Oct 18) ----------
// Plain words for the tool steps; the row is removed on done, error or any end of the stream.
const STEP_WORDS = {
  list_cases: "Finding the research case", resolve_experts: "Looking up the names in the research record",
  open_case: "Opening the case", check_case: "Checking ties for each expert", tie_report: "Checking ties for each expert",
  check_no_tie: "Checking the search scope", bridges: "Looking for co-author paths",
  recall: "Reading the newsroom memory", recheck: "Reading the newsroom memory", draft_note: "Drafting the sourcing note",
};
const stepWords = (tool) => STEP_WORDS[String(tool).split("__").pop()] || "Working";
const dots = () => el("span", { class: "dots", "aria-hidden": "true" }, el("i"), el("i"), el("i"));
function progress() {
  const line = el("span", { class: "pg-line", text: "Reading the story" });
  const secs = el("span", { class: "pg-secs", "aria-hidden": "true", text: "0 s" });   // hidden: no announcement every second
  const done = el("ul", { class: "pg-done" });
  const row = el("div", { class: "msg progress", role: "status", "aria-live": "polite" },
    el("div", { class: "pg-head" }, dots(), line, secs), done);
  messages.append(row);
  const t0 = Date.now();
  const timer = setInterval(() => { secs.textContent = `${Math.round((Date.now() - t0) / 1000)} s`; }, 1000);
  return {
    step(d) {
      const words = stepWords(d.tool);
      if (d.status === "running") { line.textContent = words; return; }
      const last = done.lastElementChild;
      if (last && last.dataset.words === words) return;   // many check_case calls: one line is enough
      const li = el("li", { "data-words": words }, el("span", { class: "tick", "aria-hidden": "true", text: d.ok ? "✓" : "×" }), words);
      if (!d.ok) li.classList.add("failed");
      done.append(li);
      while (done.children.length > 4) done.firstElementChild.remove();   // keep it compact
    },
    end() { clearInterval(timer); row.remove(); },
  };
}

function showError(reply, message) { reply.box.append(el("div", { class: "msg error", role: "alert", text: message })); }

// A small, safe markdown subset: paragraphs, "- " / "1. " lists, **bold**, `code`, "#" headings shown bold, and DOI
// links. Built from DOM nodes with textContent only: no HTML is ever parsed, so nothing in the text can inject markup.
function inline(parent, text) {
  for (const part of text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/)) {
    if (/^`[^`]+`$/.test(part)) parent.append(el("code", { text: part.slice(1, -1) }));
    else if (/^\*\*[^*]+\*\*$/.test(part)) { const b = el("strong"); inline(b, part.slice(2, -2)); parent.append(b); }
    else for (const bit of part.split(/(10\.\d{4,9}\/[^\s,;)\]`*]*[^\s,;)\]`*.:])/)) parent.append(/^10\.\d{4,9}\//.test(bit) ? doiLink(bit) : bit);
  }
}

function replyText(reply, text) {
  let list = null;
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const item = /^\s*(?:[-*•]|(\d+)\.)\s+(.*)$/.exec(line);
    if (item) {
      const kind = item[1] ? "OL" : "UL";
      if (!list || list.tagName !== kind) { list = el(kind.toLowerCase()); reply.box.append(list); }
      const li = el("li"); inline(li, item[2]); list.append(li);
      continue;
    }
    list = null;
    if (!line.trim()) continue;
    const head = /^#{1,6}\s+(.*)$/.exec(line);
    const p = el("p");
    if (head) { const b = el("strong"); inline(b, head[1]); p.append(b); } else inline(p, line);
    reply.box.append(p);
  }
}

// ---------- evidence panel ----------

const running = {};   // tool -> list of <li> still running
function step(d) {
  $("ev-empty").hidden = true;
  if (d.status === "running") {
    const li = el("li", { class: "step running" }, el("span", { class: "name", text: d.tool }),
      el("span", { class: "state" }, dots(), el("span", { class: "sr-only", text: "running" })));
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
  if (bits.length) li.append(el("p", { class: "line", text: "Scope: " + bits.join("; ") }));
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

const checkCards = Object.create(null);   // expert_id -> every expert event of this check, merged (the case graph's only data)
function card(reply, d) {
  checkCards[d.expert_id] = Object.assign(checkCards[d.expert_id] || {}, d); syncGraphBtn();
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
      const li = el("li", { class: "ev" }, el("span", { class: "ev-text" }, doiLink(e.doi), el("span", { class: "meta", text: `, ${e.kind === "FUNDED_BY" ? "funded by" : "affiliation with"} ${e.via}, tier ${e.tier}, ${e.topic_label}, ${e.year}` })));
      if (!publicDemo) li.append(excludeButton(li, e.doi, c.case || null));
      ul.append(li);
    }
    n.append(el("p", { class: "basis", text: `Evidence (${c.evidence.length} item${c.evidence.length === 1 ? "" : "s"}):` }), ul);
    if (publicDemo) n.append(el("p", { class: "basis", text: APPROVALS_OFF }));  // append(null) would print "null"
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
      ` OpenAlex ${c.expert_id}, ${c.works_on_topic} works on the topic`));
  }
  const box = el("form", { class: "picker" }, fs);
  if ((d.candidates || []).length) {
    const btn = el("button", { type: "submit", class: "primary", text: "Use this author" });
    box.append(btn);
    box.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const chosen = box.querySelector("input:checked");
      if (!chosen || busy) return;
      btn.disabled = true;   // with the busy flag (set at once by ask), a double click cannot start two runs
      // An expired check cannot be continued: the new session would not know the story.
      if (current && current.expired) { btn.textContent = "Too old to continue: start a new check"; return; }
      btn.textContent = "Checking…"; btn.classList.add("busy");
      ask(`For ${d.name_ref}, use OpenAlex author ${chosen.value}. Continue the check.`, sessionId)
        .finally(() => { btn.classList.remove("busy"); btn.textContent = "Author chosen"; });
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
  if (d.status === "verified" && publicDemo) n.append(el("p", { class: "basis", text: APPROVALS_OFF }));
  else if (d.status === "verified") {
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

// ---------- history: past conversations, kept in this browser only ----------
// Each conversation is { id, title, created, provider, session_id, items }, where items are the reporter's messages
// ({ role: "user", text }) and the server events each reply showed ({ role: "reply", events: [[event, data], ...] }).
// Only what the page already shows is kept (experts are "Expert #n"; the server strips ORCIDs and institutions).
// The API key is never part of it: nothing in this section touches the key box. Every storage call is in try/catch,
// so the page works the same when storage is off or full.
const HISTORY_KEY = "tietrace.history.v1", HISTORY_MAX = 50;  // ponytail: newest 50 kept; raise if reporters need more
const historyList = $("history"), historyEmpty = $("history-empty"), search = $("sb-search");
let convs = loadHistory(), current = null;

function loadHistory() {
  try {
    const v = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    if (!Array.isArray(v)) return [];
    return v.filter((c) => c && typeof c === "object" && typeof c.id === "string" && Array.isArray(c.items))
      .map((c) => ({ ...c, title: typeof c.title === "string" ? c.title : "Story check" }));
  } catch (e) { return []; }
}
function saveHistory() {
  convs = convs.slice(0, HISTORY_MAX);
  for (;;) {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(convs)); return; }
    catch (e) { if (convs.length <= 1) return; convs.pop(); }   // full: drop the oldest and try again
  }
}

// A safe title: the company and topic the story names (never a person's name), else a plain "Story check".
// ponytail: matches the loaded cases only; add a pair here when a new case is built.
const TITLE_COMPANIES = [[/coca[\s-]?cola|\bcoke\b/i, "Coca-Cola"], [/philip\s+morris|\bpmi\b/i, "Philip Morris"]];
const TITLE_TOPICS = [[/obes|weight|diet/i, "obesity"], [/lung\s+cancer/i, "lung cancer"], [/tobacco|smok/i, "tobacco"]];
function titleOf(story) {
  const find = (list) => (list.find(([re]) => re.test(story)) || [])[1];
  const company = find(TITLE_COMPANIES), topic = find(TITLE_TOPICS);
  return company && topic ? `${company} × ${topic}` : company || (topic ? `Story on ${topic}` : "Story check");
}
function startConv(story) {
  const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  current = { id, title: titleOf(story), created: Date.now(), provider: provSel.value, session_id: null, items: [] };
  convs.unshift(current);
  return current;
}

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
function ago(t) {
  const s = (Number(t) - Date.now()) / 1000;
  for (const [unit, n] of [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]])
    if (Math.abs(s) >= n) return rtf.format(Math.round(s / n), unit);
  return "just now";
}

function renderHistory() {
  const q = search.value.trim().toLowerCase();
  const shown = convs.filter((c) => !q || String(c.title).toLowerCase().includes(q));
  historyList.replaceChildren(...shown.map((c) => {
    const title = String(c.title), when = ago(c.created);
    const open = el("button", { type: "button", class: "open", title: `${title} · ${when}`, "aria-label": `${title}, ${when}`, text: title });
    open.addEventListener("click", () => openConv(c));
    const del = el("button", { type: "button", class: "del", text: "×", "aria-label": `Delete the check ${title}` });
    del.addEventListener("click", () => deleteConv(c));
    const li = el("li", {}, open, del);
    if (c === current) li.setAttribute("aria-current", "true");
    return li;
  }));
  historyEmpty.hidden = shown.length > 0;
  historyEmpty.textContent = convs.length ? "No checks match." : "No saved checks yet.";
  $("top-title").textContent = current ? String(current.title) : "";
}

function setEmpty(on) { document.body.classList.toggle("is-empty", on); $("hero").hidden = !on; }
function clearView() {
  messages.replaceChildren($("hero"));
  steps.replaceChildren(); $("ev-empty").hidden = false;
  for (const k of Object.keys(running)) delete running[k];
  for (const k of Object.keys(checkCards)) delete checkCards[k];
  syncGraphBtn(); if (gDlg.open) gDlg.close();
  setEmpty(true);
}
// Returns false while a check is running: switching then would mix two runs on one page.
function newCheck() {
  if (busy) { statusEl.textContent = "Wait for the running check to finish first."; return false; }
  current = null; sessionId = null; clearView(); renderHistory(); closeDrawer(); storyBox.focus();
  return true;
}
function openConv(c) {
  if (busy) { statusEl.textContent = "Wait for the running check to finish first."; return; }
  clearView(); current = c;
  // The saved provider comes back; a key typed for another provider is never sent to this one.
  if (c.provider && c.provider !== provSel.value && [...provSel.options].some((o) => o.value === c.provider)) {
    provSel.value = c.provider; provSel.dispatchEvent(new Event("change"));   // the change handler clears the key box
  }
  replaying = true;
  try {
    for (const it of c.items) {
      if (it.role === "user") userMsg(String(it.text));
      else if (it.role === "reply" && Array.isArray(it.events)) {
        const r = newReply();
        for (const ev of it.events) {
          try { if (Array.isArray(ev)) handle(r, String(ev[0]), ev[1] || {}); }
          catch (e) { /* an odd stored shape: skip that one event, draw the rest */ }
        }
      }
    }
  } finally { replaying = false; }
  sessionId = c.session_id || null;   // a follow-up continues the same session
  setEmpty(c.items.length === 0);
  renderHistory(); closeDrawer(); scrollDown();
}
function deleteConv(c) {
  if (busy) { statusEl.textContent = "Wait for the running check to finish first."; return; }
  if (!confirm(`Delete "${c.title}" from this browser?`)) return;
  convs = convs.filter((x) => x !== c); saveHistory();
  if (c === current) { if (!newCheck()) current = null; }
  renderHistory();
}
$("clear-all").addEventListener("click", () => {
  if (busy) { statusEl.textContent = "Wait for the running check to finish first."; return; }
  if (!convs.length || !confirm("Delete every saved check from this browser?")) return;
  convs = [];
  try { localStorage.removeItem(HISTORY_KEY); } catch (e) { /* storage off: nothing was saved */ }
  if (!newCheck()) current = null;
  renderHistory();
});
$("new-check").addEventListener("click", newCheck);
search.addEventListener("input", renderHistory);

// ---------- sidebar and evidence panel toggles ----------
const narrow = matchMedia("(max-width: 900px)");
const sbToggle = $("sb-toggle"), sbOpen = $("sb-open"), evPanel = $("evidence"), evToggle = $("ev-toggle");
function closeDrawer() { if (narrow.matches) setSidebar(false); }
function setSidebar(open) {
  if (narrow.matches) document.body.classList.toggle("sb-open", open);
  else document.body.classList.toggle("sb-collapsed", !open);
  $("scrim").hidden = !(narrow.matches && open);
  $("sidebar").inert = narrow.matches && !open;   // a closed drawer is out of the Tab order
  sbOpen.setAttribute("aria-expanded", String(open)); sbToggle.setAttribute("aria-expanded", String(open));
}
sbToggle.addEventListener("click", () => { setSidebar(false); sbOpen.focus(); });
sbOpen.addEventListener("click", () => { setSidebar(true); sbToggle.focus(); });
$("scrim").addEventListener("click", () => setSidebar(false));
narrow.addEventListener("change", () => { document.body.classList.remove("sb-open", "sb-collapsed"); setSidebar(!narrow.matches); });
setSidebar(!narrow.matches);
addEventListener("keydown", (ev) => { if (ev.key === "Escape" && document.body.classList.contains("sb-open")) setSidebar(false); });
let evTouched = false;
function setEvidence(open) {
  evPanel.hidden = !open;
  evToggle.setAttribute("aria-expanded", String(open));
  evToggle.setAttribute("aria-label", open ? "Hide the evidence panel" : "Show the evidence panel");
}
evToggle.addEventListener("click", () => { evTouched = true; setEvidence(evPanel.hidden); });
$("ev-close").addEventListener("click", () => { evTouched = true; setEvidence(false); evToggle.focus(); });
// The first live tool step opens the panel on a wide screen, unless the reporter has already opened or closed it.
function autoEvidence() { if (!replaying && !evTouched && !narrow.matches) { evTouched = true; setEvidence(true); } }

// ---------- case graph (Aaryan, Oct 18) ----------
// Drawn only from the expert events this check already showed (checkCards): no new request and no new query, so every
// node and edge is an expert card or one of its evidence rows. Built with createElementNS and textContent only.
const SVGNS = "http://www.w3.org/2000/svg";
function sv(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) { if (k === "text") e.textContent = v; else e.setAttribute(k, v); }
  for (const k of kids) if (k != null) e.append(k);
  return e;
}
const gDlg = $("graph"), gSvg = $("graph-svg"), gSide = $("graph-side"), gBack = $("graph-back"), gOpen = $("graph-open");
const TIERS = ["A1", "A2", "B", "C", "NONE"];
const cut = (s, n = 28) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const papersWord = (n) => `${n} paper${n === 1 ? "" : "s"}`;
const kindWord = (k) => (k === "FUNDED_BY" ? "funding" : "affiliation");
const kindsText = (set) => [...set].sort().join(" and ");
let gModel = null, gView = "overview", gLast = null;

function syncGraphBtn() {
  const n = Object.values(checkCards).filter((c) => c && !c.error).length;
  gOpen.disabled = n === 0;
  gOpen.title = n ? "Case graph" : "Case graph: no expert results in this check yet";
}

// Nodes: the check (centre), one per distinct via (funder or institution), one per expert, one per DOI.
// rows: one per evidence row (expert, via, paper, kind), so a drill-down links only what a row says.
function graphModel() {
  const experts = [], vias = new Map(), papers = new Map(), rows = [], cases = new Set();
  for (const c of Object.values(checkCards)) {
    if (!c || c.error) continue;   // a failed check says nothing about the expert: no node
    if (c.case) cases.add(String(c.case));
    const label = /^Expert #\d+$/.test(String(c.label)) ? String(c.label) : "Expert #?";   // never anything but Expert #n
    const tier = TIERS.includes(c.tier) ? c.tier : (c.no_tie ? "NONE" : "?");
    const x = { type: "expert", label, tier, basis: c.tier_basis ? String(c.tier_basis) : "", noTie: c.no_tie || null,
      listed: Array.isArray(c.evidence), links: new Map() };
    experts.push(x);
    for (const e of Array.isArray(c.evidence) ? c.evidence : []) {
      if (!e || e.doi == null || e.via == null) continue;
      const name = String(e.via), doi = String(e.doi), kind = kindWord(e.kind);
      let v = vias.get(name);
      if (!v) vias.set(name, v = { type: "via", label: name, kinds: new Set(), links: new Map() });
      let p = papers.get(doi);
      if (!p) papers.set(doi, p = { type: "paper", doi, year: e.year == null ? "year unknown" : String(e.year),
        topic: e.topic_label == null ? "topic unknown" : String(e.topic_label) });
      let l = x.links.get(v);
      if (!l) { l = { kinds: new Set(), dois: new Set() }; x.links.set(v, l); v.links.set(x, l); }
      v.kinds.add(kind); l.kinds.add(kind); l.dois.add(doi);
      rows.push({ x, v, p, kind, tier: e.tier == null ? "?" : String(e.tier) });
    }
  }
  const viaList = [...vias.values()];
  const firstVia = (x) => { const i = viaList.findIndex((v) => x.links.has(v)); return i < 0 ? 1e9 : i; };
  const num = (x) => Number(x.label.slice(8)) || 1e9;
  experts.sort((a, b) => firstVia(a) - firstVia(b) || num(a) - num(b));   // tied experts sit near their funders
  return { title: current ? String(current.title) : "This check", cases: [...cases], experts, vias: viaList, papers, rows };
}

function gNode(n, x, y, r, cls, lines, aria, act) {
  const g = sv("g", { class: `gn ${cls}`, role: "button", tabindex: "0", "aria-label": aria, transform: `translate(${x} ${y})` },
    sv("circle", { r }), ...lines.map((t, i) => sv("text", { y: r + 24 + i * 21, class: i ? "sub" : "", text: t })));
  const go = (ev) => { ev.stopPropagation(); facts(n); if (act) act(); };
  g.addEventListener("click", go);
  g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(ev); } });
  g.addEventListener("focus", () => facts(n));
  n.el = g;
  return g;
}
function gEdge(x1, y1, x2, y2, cls, label) {
  const g = sv("g", {}, sv("line", { class: `ge ${cls}`, x1, y1, x2, y2 }));
  // the label sits nearer the far end (the expert or the paper side), so it never hides under the centre node
  if (label) g.append(sv("text", { class: "gl", x: x1 + (x2 - x1) * 0.62, y: y1 + (y2 - y1) * 0.62 + 5, text: label }));
  return g;
}
const edgeCls = (kinds) => (kinds.has("funding") ? "fund" : "aff");
const edgeText = (l) => `${papersWord(l.dois.size)}, ${kindsText(l.kinds)}`;
const expertNote = (x) => (x.tier === "NONE" ? "no tie found (see scope)"
  : x.links.size ? `Tier ${x.tier}` : `Tier ${x.tier}, no paper list here`);

function gPaint(w, h, edges, nodes, back) {
  gSvg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  gSvg.style.minWidth = `${Math.round(w * 0.7)}px`;   // labels stay readable; the view scrolls on a phone
  const bg = sv("rect", { class: "g-bg", x: 0, y: 0, width: w, height: h });
  if (back) bg.addEventListener("click", overview);   // a click on the background goes back
  gSvg.replaceChildren(bg, sv("g", { class: "g-layer" }, ...edges, ...nodes));
}

// The abstract view: the check in the centre, funders and institutions in the middle ring, experts in the outer ring.
function overview() {
  const m = gModel, C = 450, R1 = 205, R2 = 370;
  gView = "overview"; gBack.hidden = true;
  const at = (i, n, r) => { const a = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(n, 1); return [C + r * Math.cos(a), C + r * Math.sin(a)]; };
  const edges = [], nodes = [], centre = { type: "case", label: m.title };
  m.vias.forEach((v, i) => { v.at = at(i, m.vias.length, R1); edges.push(gEdge(C, C, ...v.at, "hub")); });
  m.experts.forEach((x, i) => {
    x.at = at(i + (m.vias.length ? 0.5 : 0), m.experts.length, R2);
    for (const [v, l] of x.links) edges.push(gEdge(...v.at, ...x.at, edgeCls(l.kinds), edgeText(l)));
  });
  nodes.push(gNode(centre, C, C, 44, "case", [cut(m.title, 32)], `${m.title}: this check`, null));
  for (const v of m.vias) {
    const n = v.links.size;
    nodes.push(gNode(v, ...v.at, 26, "via", [cut(v.label), kindsText(v.kinds)],
      `${v.label}, ${kindsText(v.kinds)}, linked to ${n} expert${n === 1 ? "" : "s"}. Press to show only its experts and papers.`, () => drill(v)));
  }
  for (const x of m.experts)
    nodes.push(gNode(x, ...x.at, 30, `expert t-${x.tier}`, [x.label, expertNote(x)],
      `${x.label}, ${expertNote(x)}.${x.links.size ? " Press to show the papers." : ""}`, () => drill(x)));
  gPaint(900, 900, edges, nodes, false);
  const back = gLast && gSvg.contains(gLast.el) ? gLast : centre;   // back on the node that was opened
  back.el.focus(); facts(back);
}

// One expert (its funders and institutions, then its papers), or one funder or institution (its experts, then their papers).
function drill(n) {
  const rows = gModel.rows.filter((r) => (n.type === "expert" ? r.x : r.v) === n);
  if (!rows.length) return;   // nothing to expand: the side panel already shows the facts
  gView = "drill"; gLast = n; gBack.hidden = false;
  const other = (r) => (n.type === "expert" ? r.v : r.x);
  const mids = [...new Set(rows.map(other))], papers = [];
  for (const mid of mids) for (const r of rows) if (other(r) === mid && !papers.includes(r.p)) papers.push(r.p);
  const H = 64, top = 60, h = Math.max(520, top * 2 + papers.length * H), X0 = 110, X1 = 400, X2 = 640;
  papers.forEach((p, i) => { p.at = [X2, top + i * H + H / 2]; });
  for (const mid of mids) {
    const ys = rows.filter((r) => other(r) === mid).map((r) => r.p.at[1]);
    mid.at = [X1, (Math.min(...ys) + Math.max(...ys)) / 2];
  }
  const fy = h / 2, edges = [], nodes = [], seen = new Set();
  for (const mid of mids) {
    const l = n.type === "expert" ? n.links.get(mid) : mid.links.get(n);
    edges.push(gEdge(X0, fy, ...mid.at, edgeCls(l.kinds), edgeText(l)));
  }
  for (const r of rows) {
    const k = `${papers.indexOf(r.p)}:${mids.indexOf(other(r))}:${r.kind}`;
    if (!seen.has(k)) { seen.add(k); edges.push(gEdge(...other(r).at, ...r.p.at, r.kind === "funding" ? "fund thin" : "aff thin")); }
  }
  nodes.push(gNode(n, X0, fy, 30, n.type === "expert" ? `expert t-${n.tier}` : "via",
    n.type === "expert" ? [n.label, `Tier ${n.tier}`] : [cut(n.label, 22), kindsText(n.kinds)], `${n.label}: ${papersWord(papers.length)} shown`, null));
  for (const mid of mids)
    nodes.push(gNode(mid, ...mid.at, 22, mid.type === "expert" ? `expert t-${mid.tier}` : "via",
      [mid.type === "expert" ? mid.label : cut(mid.label, 24)], mid.type === "expert" ? `${mid.label}, Tier ${mid.tier}` : mid.label, null));
  for (const p of papers) nodes.push(paperNode(p, rows.filter((r) => r.p === p)));
  gPaint(1040, h, edges, nodes, true);
  n.el.focus(); facts(n);
}

function paperNode(p, rows) {
  let href = null;
  try { href = "https://doi.org/" + encodeURI(p.doi); } catch (e) { /* lone surrogate: no link */ }
  const kinds = kindsText(new Set(rows.map((r) => r.kind)));
  const aria = `Paper ${p.doi}, ${p.year}, ${p.topic}, ${kinds}.${href ? " Opens doi.org in a new tab." : ""}`;
  const g = sv("g", { class: "gn paper", role: "link", tabindex: "0", "aria-label": aria, transform: `translate(${p.at[0]} ${p.at[1]})` },
    sv("circle", { r: 9 }), sv("text", { x: 20, y: -3, text: cut(p.doi, 40) }), sv("text", { x: 20, y: 18, class: "sub", text: `${p.year}, ${p.topic}` }));
  if (href) g.dataset.href = href;
  const open = (ev) => { ev.stopPropagation(); facts(p, rows); if (href) window.open(href, "_blank", "noopener"); };
  g.addEventListener("click", open);
  g.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(ev); } });
  g.addEventListener("focus", () => facts(p, rows));
  p.el = g;
  return g;
}

// The side panel: the selected node's facts, in text.
function facts(n, rows) {
  for (const s of gSvg.querySelectorAll(".gn.sel")) s.classList.remove("sel");
  if (n.el) n.el.classList.add("sel");
  const dl = el("dl"), m = gModel;
  const add = (k, v) => dl.append(el("dt", { text: k }), el("dd", {}, v));
  const count = (pick) => String(new Set(m.rows.filter(pick).map((r) => r.p)).size);
  let head = n.label;
  if (n.type === "case") {
    if (m.cases.length) add("Research case", m.cases.join(", "));
    add("Experts", String(m.experts.length));
    add("Funders and institutions", String(m.vias.length));
    add("Papers", String(m.papers.size));
  } else if (n.type === "via") {
    add("Link", kindsText(n.kinds));
    add("Experts", [...n.links.keys()].map((x) => x.label).join(", "));
    add("Papers", count((r) => r.v === n));
  } else if (n.type === "expert") {
    add("Tier", n.tier);
    if (n.basis) add("Basis", n.basis);
    add("Papers", n.listed ? count((r) => r.x === n) : "no paper list in this check");
    const t = n.noTie;
    if (t && t.result === "NO_TIE_FOUND")
      add("Scope", `No tie found in the research record within ${t.hops} co-author hops, years ${t.years} (${t.query_name}). Absence is not proof.`);
    else if (t && t.result)
      add("Scope", `A Tier-A person is within ${t.nearest ? t.nearest.hops : "?"} co-author hops (${t.query_name}; searched ${t.hops} hops, years ${t.years}). Network context only.`);
    else if (t) add("Scope", "The no-tie search failed; nothing is said about this expert.");
  } else {
    head = "Paper";
    add("DOI", doiLink(n.doi));
    add("Year", n.year);
    add("Topic", n.topic);
    for (const r of rows || []) add("Evidence", `${r.kind === "funding" ? "funded by" : "affiliation with"} ${r.v.label}, ${r.x.label}, tier ${r.tier}`);
  }
  gSide.replaceChildren(el("h3", { text: head }), dl,
    el("p", { class: "g-note", text: "Built only from the results this check showed. Ties found in the research record; a missing tie is not proof." }));
}

gOpen.addEventListener("click", () => {
  gModel = graphModel(); gLast = null;
  if (!gDlg.open) gDlg.showModal();
  overview();
});
const focusOpener = () => { if (!gOpen.disabled) gOpen.focus(); };
$("graph-close").addEventListener("click", () => { gDlg.close(); focusOpener(); });
gBack.addEventListener("click", overview);
// Escape in a drill-down goes back to the overview; Escape on the overview closes the graph.
gDlg.addEventListener("cancel", (ev) => { if (gView !== "overview") { ev.preventDefault(); overview(); } });
gDlg.addEventListener("close", () => { gView = "overview"; focusOpener(); });

renderHistory();
