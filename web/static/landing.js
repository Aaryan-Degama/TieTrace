// Abdul's sections live in the "About TieTrace" panel, a native modal <dialog> (Aaryan, Oct 18): showModal moves focus
// into it, Escape closes it and the browser returns focus to the button that opened it. The panel's last form hands its
// story to the main composer. Shares app.js's top-level names (form, storyBox, keyBox, keyInput, closeDrawer); load it after app.js.
"use strict";
(() => {
  const about = document.getElementById("about"), opener = document.getElementById("about-open");
  function openAbout(sel) {
    closeDrawer();
    if (!about.open) about.showModal();
    const target = sel && about.querySelector(sel);
    if (target) target.scrollIntoView(); else about.scrollTop = 0;
  }
  opener.addEventListener("click", () => openAbout());
  document.getElementById("about-close").addEventListener("click", () => about.close());
  about.addEventListener("close", () => { if (document.activeElement === document.body) opener.focus(); });

  // Sidebar links to a section (Methodology, Boundaries) open the panel at that section.
  document.querySelectorAll("a[data-more]").forEach((a) => a.addEventListener("click", (ev) => {
    ev.preventDefault();
    openAbout(a.getAttribute("href"));
  }));

  // "Ready to verify your story?" form: run the check in the main composer.
  about.querySelectorAll("[data-landing-form]").forEach((f) => f.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const t = f.querySelector("textarea"), story = t.value.trim();
    if (!story) return;
    if (!document.body.classList.contains("is-empty") && !newCheck()) return;
    t.value = ""; about.close(); storyBox.value = story;
    if (!keyBox.hidden && !keyInput.value) keyInput.focus(); else form.requestSubmit();
  }));
})();
