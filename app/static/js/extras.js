/* Display-only extras (docs/PRANK_MODE.md). Nothing here is sent back to the server
   except through the normal log form, which records exactly what was typed. */
(() => {
  "use strict";

  function bigDay(ex) {
    return ex && ex.big_day ? ex.big_day : null;
  }

  // Spin: the real number is already in the page; a layer on top rolls through two or three
  // numbers and is removed after about one second. First open of the day only.
  function spin(ex, kmEl, rid, today) {
    if (!ex || !ex.spin || !kmEl) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const key = `spin-${rid}-${today}`;
    try { if (localStorage.getItem(key)) return; localStorage.setItem(key, "1"); } catch (e) { return; }
    const layer = document.createElement("div");
    layer.className = "spin-layer km";
    layer.setAttribute("aria-hidden", "true");
    kmEl.appendChild(layer);
    const unit = '<span class="unit">km</span>';
    ex.spin.forEach((n, i) => setTimeout(() => { layer.innerHTML = `<span class="num">${n}</span>${unit}`; }, i * 300));
    setTimeout(() => layer.remove(), 1000);
  }

  // Optional sound on "I did it", at most once a day, only after a click, low volume.
  function sound(ex) {
    if (!ex || !ex.sound) return;
    const key = `sound-${new Date().toDateString()}`;
    try { if (localStorage.getItem(key)) return; localStorage.setItem(key, "1"); } catch (e) { return; }
    const a = new Audio(`/static/sounds/${ex.sound}.m4a`);
    a.volume = 0.35;
    a.play().catch(() => {});
  }

  window.Extras = { bigDay, spin, sound };
})();
