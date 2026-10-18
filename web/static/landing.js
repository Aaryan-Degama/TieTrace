// Abdul's sections below the fold of the empty state: reveal on scroll, light parallax, and the last form hands its
// story to the main composer. Shares app.js's top-level names (form, storyBox, newCheck); load it after app.js.
"use strict";
(() => {
  const more = document.getElementById("more"), main = document.getElementById("main");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }, { threshold: 0.15 });
  more.querySelectorAll(".rv").forEach((n) => io.observe(n));

  // Parallax: cards with data-speed drift against the scroll (the main column scrolls, not the window).
  const movers = [...more.querySelectorAll("[data-speed]")];
  function parallax() {
    const mid = innerHeight / 2;
    for (const n of movers) {
      const r = n.getBoundingClientRect();
      n.style.setProperty("--py", `${((r.top + r.height / 2 - mid) * -Number(n.dataset.speed)).toFixed(1)}px`);
    }
  }
  if (!reduce) { main.addEventListener("scroll", parallax, { passive: true }); parallax(); }

  // "Ready to verify your story?" form: run the check in the main composer.
  more.querySelectorAll("[data-landing-form]").forEach((f) => f.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const t = f.querySelector("textarea"), story = t.value.trim();
    if (!story) return;
    t.value = ""; main.scrollTo(0, 0); storyBox.value = story;
    if (!keyBox.hidden && !keyInput.value) keyInput.focus(); else form.requestSubmit();
  }));

  // Sidebar links to a section (Methodology, Boundaries): the sections live on the empty state.
  document.querySelectorAll("a[data-more]").forEach((a) => a.addEventListener("click", (ev) => {
    if (!document.body.classList.contains("is-empty") && !newCheck()) { ev.preventDefault(); return; }
    ev.preventDefault();
    closeDrawer();
    document.querySelector(a.getAttribute("href")).scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
  }));
})();
