/* Counts quick taps on the race countdown. Each tap within 0.6 s of the last; the count
   is read 0.9 s after the last tap. No visible reaction: the server decides what a count means. */
(() => {
  "use strict";
  const GAP_MS = 600, READ_MS = 900;
  window.Knock = {
    attach(el, onCount) {
      let n = 0, last = 0, timer = null;
      el.addEventListener("pointerdown", (e) => {
        if (e.button !== undefined && e.button !== 0) return;
        const now = performance.now();
        n = now - last <= GAP_MS ? n + 1 : 1;
        last = now;
        clearTimeout(timer);
        timer = setTimeout(() => { const c = n; n = 0; if (c >= 5 && c <= 7) onCount(c); }, READ_MS);
      });
      el.addEventListener("mousedown", (e) => { if (e.detail > 1) e.preventDefault(); }); // no text selection
    },
  };
})();
