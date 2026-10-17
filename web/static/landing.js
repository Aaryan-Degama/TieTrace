// Landing page: reveal on scroll, light parallax, and the hand-off into the chat view.
// Shares app.js's top-level names (form, storyBox, keyBox, keyInput); load it after app.js.
"use strict";
(() => {
  const landing = document.getElementById("landing"), appRoot = document.getElementById("app");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Reveal blocks as they enter the viewport; the graph also draws its edges then.
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }, { threshold: 0.15 });
  landing.querySelectorAll(".rv").forEach((n) => io.observe(n));

  // Parallax: cards with data-speed drift against the scroll; reveals keep their own transform via --py.
  const movers = [...landing.querySelectorAll("[data-speed]")];
  function parallax() {
    const mid = innerHeight / 2;
    for (const n of movers) {
      const r = n.getBoundingClientRect();
      n.style.setProperty("--py", `${((r.top + r.height / 2 - mid) * -Number(n.dataset.speed)).toFixed(1)}px`);
    }
  }
  if (!reduce) { addEventListener("scroll", parallax, { passive: true }); parallax(); }

  landing.querySelectorAll(".lp-chip").forEach((b) => b.addEventListener("click", () => {
    const t = b.closest("section").querySelector("textarea"); t.value = b.dataset.story; t.focus();
  }));

  // Submitting a story swaps to the chat interface and runs the check there.
  landing.querySelectorAll("[data-landing-form]").forEach((f) => f.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const story = f.querySelector("textarea").value.trim();
    if (!story) return;
    landing.hidden = true; appRoot.hidden = false; document.body.classList.remove("is-landing");
    scrollTo(0, 0);
    storyBox.value = story;
    // A key is needed and missing: leave the story in the box and ask for the key first.
    if (!keyBox.hidden && !keyInput.value) keyInput.focus(); else form.requestSubmit();
  }));
})();
