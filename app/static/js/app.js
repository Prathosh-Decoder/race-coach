/* Race Coach screens. Plain JavaScript; every number shown comes from the server. */
(() => {
  "use strict";

  const $ = (sel, el = document) => el.querySelector(sel);
  const main = $("#main");
  const S = { rid: null, today: null, runners: [], flash: null, poll: null };

  // ---------- helpers ----------

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function store(key, val) {
    try { if (val === undefined) return localStorage.getItem(key); localStorage.setItem(key, val); }
    catch (e) { return null; }
  }

  async function api(path, body) {
    const opt = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const res = await fetch(path, opt);
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok && res.status !== 422) {
      const msg = typeof data.detail === "string" ? data.detail : "Something went wrong. Try again.";
      throw new Error(msg);
    }
    return data;
  }

  const ymd = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
  const iso = (dt) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
  const addDays = (s, n) => { const d = ymd(s); d.setDate(d.getDate() + n); return iso(d); };
  const monday = (s) => { const d = ymd(s); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return iso(d); };
  const fmtDay = (s, opts = { weekday: "short", day: "numeric", month: "short" }) =>
    ymd(s).toLocaleDateString("en-GB", opts);
  const km = (n) => (Math.round(n * 10) / 10).toString();
  const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const FACES = ["😌", "😌", "🙂", "🙂", "😐", "😐", "😣", "😣", "🥵", "🥵"];

  // ---------- the sky band (Singapore time) ----------

  let clockOffset = 0;  // server minus browser, in ms

  function sgtParts() {
    const p = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Singapore", weekday: "short", day: "numeric", month: "short",
      hour: "numeric", minute: "2-digit", hour12: true }).formatToParts(new Date(Date.now() + clockOffset));
    const g = (t) => (p.find((x) => x.type === t) || {}).value;
    const h24 = Number(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Singapore", hour: "2-digit", hourCycle: "h23" }).format(new Date(Date.now() + clockOffset)));
    const min = Number(g("minute"));
    return { label: `${g("weekday")} ${g("day")} ${g("month")}, ${g("hour")}:${g("minute")} ${(g("dayPeriod") || "").toLowerCase()}`,
             hour: h24 + min / 60 };
  }

  function paintSky() {
    const { label, hour } = sgtParts();
    $("#when").textContent = `${label} Singapore time`;
    const sky = $("#sky");
    let phase = "day";
    if (hour < 5.5 || hour >= 19.5) phase = "night";
    else if (hour < 7.5) phase = "dawn";
    else if (hour >= 17.5) phase = "dusk";
    sky.dataset.phase = phase;
    let t;
    if (phase === "night") {
      t = ((hour - 19.5 + 24) % 24) / 10;          // 19:30 -> 05:30
    } else {
      t = Math.min(1, Math.max(0, (hour - 5.5) / 14)); // 05:30 -> 19:30
    }
    // An arc across the gap between the name and the countdown.
    sky.style.setProperty("--sun-x", (0.38 + t * 0.3).toFixed(3));
    sky.style.setProperty("--sun-y", `${Math.round(64 - Math.sin(Math.PI * t) * 40)}px`);
  }

  // ---------- header ----------

  function paintHeader() {
    const t = S.today;
    const who = $("#who");
    const ex = t && t.extras;
    if (!t) { who.hidden = true; $("#countdown").textContent = ""; $("#tabs").hidden = true; return; }
    who.hidden = false;
    who.textContent = (ex && ex.nickname) || t.runner.name;
    who.setAttribute("aria-label", `${t.runner.name}. Switch runner`);
    $("#countdown").textContent = (ex && ex.countdown) ||
      (t.days_to_race > 1 ? `${t.days_to_race} days to race` : t.days_to_race === 1 ? "1 day to race" :
        t.days_to_race === 0 ? "Race day" : "");
    $("#tabs").hidden = t.gate !== "ok";
    $("#review-tab").hidden = !t.pending;
    $("#review-count").textContent = t.pending || "";
    const tab = (location.hash.split("/")[1] || "today").split("?")[0];
    document.querySelectorAll("#tabs a").forEach((a) => {
      if (a.dataset.tab === tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }

  async function refreshToday() {
    S.today = await api(`/api/today?rid=${S.rid}`);
    const off = new Date(S.today.clock.iso).getTime() - Date.now();
    clockOffset = Math.abs(off) > 120000 ? off : 0;
    paintSky();
    paintHeader();
    watchCoach();
    return S.today;
  }

  function watchCoach() {
    clearTimeout(S.poll);
    if (S.today && S.today.coach && S.today.coach.state === "working") {
      S.poll = setTimeout(async () => {
        const st = await api(`/api/status?rid=${S.rid}`).catch(() => null);
        if (st && st.coach && st.coach.state !== "working") route();
        else watchCoach();
      }, 8000);
    }
  }

  async function checkOnline() {
    const st = await api("/api/status").catch(() => ({ online: false }));
    $("#offline").hidden = !!st.online;
  }

  function flash(msg, warn) { S.flash = { msg, warn }; }
  function takeFlash() {
    if (!S.flash) return "";
    const f = S.flash; S.flash = null;
    return `<p class="toast${f.warn ? " warn" : ""}" role="status">${esc(f.msg)}</p>`;
  }

  // ---------- screens ----------

  async function screenWho() {
    S.rid = null; S.today = null; paintHeader();
    S.runners = await api("/api/runners");
    main.innerHTML = `
      <h1>Who is running?</h1>
      <div class="pick">
        ${S.runners.map((r) => `
          <button type="button" data-rid="${r.id}">
            <span class="name">${esc(r.name)}</span>
            <span class="sub">${r.gate === "ok" ? "Open today's session" : "Answer the health questions first"}</span>
          </button>`).join("")}
      </div>`;
    main.querySelectorAll("[data-rid]").forEach((b) => b.addEventListener("click", () => {
      S.rid = Number(b.dataset.rid); store("rid", S.rid); location.hash = "#/today"; route();
    }));
  }

  async function screenSetup() {
    const p = await api(`/api/runners/${S.rid}/profile`);
    const yn = (name, label, hint = "") => `
      <fieldset class="field">
        <legend>${label}</legend>${hint ? `<p class="hint">${hint}</p>` : ""}
        <div class="choices">
          <label class="choice"><input type="radio" name="${name}" value="1" required><span>Yes</span></label>
          <label class="choice"><input type="radio" name="${name}" value="0"><span>No</span></label>
        </div>
      </fieldset>`;
    main.innerHTML = `
      <h1>Before your plan</h1>
      <p class="lede">A few questions so the plan fits you. Your answers stay on this laptop.</p>
      <form id="setup">
        ${p.diet_confirmed ? "" : `
          <fieldset class="field">
            <legend>Do you follow a diet?</legend>
            <p class="hint">This picks the snacks suggested for long runs.</p>
            <div class="choices">
              ${Object.entries(p.diets).map(([k, v]) => `
                <label class="choice"><input type="radio" name="diet" value="${k}" required><span>${esc(v)}</span></label>`).join("")}
            </div>
          </fieldset>`}
        <h2 style="margin:32px 0 16px">Health questions</h2>
        ${yn("q1", "In the last 3 months, have you exercised for 30 minutes or more on at least 3 days a week?")}
        ${yn("q2", "Has a doctor ever told you that you have heart disease, diabetes or another metabolic disease, or kidney disease?")}
        ${yn("q3", "Do you ever have chest pain or pressure, breathlessness at rest or with light activity, dizziness or fainting, swollen ankles, a racing or irregular heartbeat, leg pain when walking, or unusual tiredness?")}
        <p class="error" id="err" role="alert"></p>
        <button class="btn primary" type="submit">Save answers</button>
      </form>`;
    $("#setup").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      try {
        if (f.get("diet")) await api(`/api/runners/${S.rid}/profile`, { diet: f.get("diet") });
        await api(`/api/runners/${S.rid}/screening`, {
          active_3x_week: f.get("q1") === "1", known_disease: f.get("q2") === "1", symptoms: f.get("q3") === "1" });
        route();
      } catch (err) { $("#err").textContent = err.message; }
    });
  }

  function screenLocked() {
    main.innerHTML = `
      <h1>Your plan is paused</h1>
      <p class="lede">You answered yes to one of the health questions. Please see a doctor before you start running.
      When a doctor says you can, enter the date here and your plan will open.</p>
      <form id="clear" class="field">
        <label for="cd">Date the doctor cleared you</label>
        <input class="input short" type="date" id="cd" required>
        <div class="btn-row"><button class="btn primary" type="submit">Open my plan</button></div>
        <p class="error" id="err" role="alert"></p>
      </form>`;
    $("#clear").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await api(`/api/runners/${S.rid}/clearance`, { doctor_cleared_on: $("#cd").value }); route(); }
      catch (err) { $("#err").textContent = err.message; }
    });
  }

  // The session card. `big` is Big Day's display-only rewrite, if any.
  function cardHtml(c, { big = null, actions = "", context = "" } = {}) {
    const isRun = c.distance_km > 0;
    const shownKm = big ? big.km : c.distance_km;
    const title = big ? big.title : c.title;
    const steps = big ? big.steps : c.steps;
    const minutes = big ? big.minutes : c.minutes;
    const facts = [];
    if (isRun || big) facts.push(["Distance", `${km(shownKm)} km`, minutes ? `about ${minutes} min` : ""]);
    if (c.pace) facts.push(["Pace", c.pace, c.speed || ""]);
    if (c.where && c.where !== "—") facts.push(["Where", c.where, c.location === "outdoor" ? "flat route" : ""]);
    if (c.breaks && c.breaks !== "—") facts.push(["Breaks", c.breaks, ""]);
    facts.push(["Effort", c.effort.label, c.effort.score !== "—" ? `${c.effort.score}, ${c.effort.cue}` : c.effort.cue]);
    const ruleTxt = (ids) => ids && ids.length ? ` <span class="rules">(${ids.map(esc).join(", ")})</span>` : "";
    return `
      <article class="session" data-type="${esc(big ? "long" : c.type)}">
        <div class="session-head">
          ${isRun || big
            ? `<div class="km" id="km" aria-label="${km(shownKm)} kilometres"><span class="num">${km(shownKm)}</span><span class="unit">km</span></div>`
            : `<div class="km word">${esc(c.type_label)}</div>`}
          <div class="session-title">
            <h1>${esc(title)}</h1>
            <p>${esc(context)}</p>
          </div>
        </div>
        <dl class="facts" style="--n:${facts.length}">
          ${facts.map(([k, v, s]) => `<div><dt>${k}</dt><dd>${esc(v)}${s ? `<small>${esc(s)}</small>` : ""}</dd></div>`).join("")}
        </dl>
        ${steps && steps.length ? `
          <h2 class="sr-steps" style="margin-top:28px;font-size:1.1rem">Steps</h2>
          <ol class="steps${steps.length < 2 ? " plain" : ""}">${steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : ""}
        ${c.guide ? `<p class="why">${esc(c.guide)}</p>` : ""}
        ${c.water ? `<p class="why">${esc(c.water)}</p>` : ""}
        ${c.fuel ? `<p class="note fuel"><strong>Fuel.</strong> ${esc(c.fuel.text)} From your list: ${c.fuel.items.map(esc).join(", ")}.</p>` : ""}
        ${c.heat ? `<p class="note heat">${esc(c.heat)}</p>` : ""}
        ${c.blocked === "pain" ? `<p class="note pain">If it is not better in 3 days or gets worse, see a doctor or physiotherapist.</p>` : ""}
        ${c.reason ? `<p class="why">Why this session: ${esc(c.reason)}${ruleTxt(c.rule_ids)}</p>`
          : c.rule_ids && c.rule_ids.length ? `<p class="why">Rules:${ruleTxt(c.rule_ids)}</p>` : ""}
        ${actions}
      </article>`;
  }

  function riskHtml(c) {
    if (!c.elevated_risk || c.runner_ok_at) return "";
    return `
      <div class="risk">
        <p><strong>This run is longer than your recent runs.</strong> Longer jumps carry more injury risk, so it needs your OK first (R-04).</p>
        <div class="btn-row" style="margin-top:0">
          <button class="btn sun small" type="button" data-ok="${esc(c.day)}">OK, I'll do it</button>
          <a class="btn quiet small" href="#/coach?ask=${encodeURIComponent(`Please make my run on ${fmtDay(c.day)} shorter`)}">Ask for a shorter run</a>
        </div>
      </div>`;
  }

  function bindOk(after) {
    main.querySelectorAll("[data-ok]").forEach((b) => b.addEventListener("click", async () => {
      await api("/api/plan/ok", { rid: S.rid, day: b.dataset.ok });
      after();
    }));
  }

  async function screenToday() {
    const t = S.today;
    const ex = t.extras || {};
    const c = t.card;
    if (!c) return route();
    const big = window.Extras ? window.Extras.bigDay(ex) : null;
    const isRun = c.distance_km > 0 || big;
    const ctx = [
      isRun ? `${c.type === "long" || big ? "Long" : c.effort.label} session` : null,
      t.week_label,
    ].filter(Boolean).join(", ");
    const needsOk = c.elevated_risk && !c.runner_ok_at && !big;
    let actions = "";
    if (c.blocked) {
      actions = c.blocked === "pain" ? `
        <div class="actions" style="grid-template-columns:1fr">
          <button class="btn primary" type="button" id="pain-gone">The pain is gone</button>
        </div>` : "";
    } else if (t.logged_today) {
      actions = `<p class="note plain">Logged for today. <a href="#/log/${t.today}">Change it</a></p>`;
    } else if (c.type === "rest") {
      actions = `<p class="after">Rest days count towards your streak when you rest.</p>`;
    } else {
      actions = `
        ${riskHtml(c)}
        <div class="actions">
          <a class="btn primary" href="#/log/${t.today}?s=done" ${needsOk ? 'aria-disabled="true" tabindex="-1" style="opacity:.5;pointer-events:none"' : ""}>I did it</a>
          <button class="btn" type="button" id="couldnt">I couldn't</button>
          <a class="btn danger" href="#/pain">I have pain</a>
        </div>
        <p class="after">After saving, the coach checks whether the rest of the week should change.</p>`;
    }
    const working = t.coach && t.coach.state === "working";
    main.innerHTML = `
      ${takeFlash()}
      ${ex.greeting ? `<p class="greeting">${esc(ex.greeting)}</p>` : ""}
      ${working ? `<p class="working">${esc(ex.loading || "Working on your new plan…")}</p>` : ""}
      ${t.pending ? `<p class="toast">The coach has suggested changes. <a href="#/review">Review changes</a></p>` : ""}
      ${t.unanswered_cards.map((u) => `
        <div class="ask">
          <p><strong>Did you do it?</strong> ${esc(fmtDay(u.day, { weekday: "long", day: "numeric", month: "short" }))}: ${esc(u.title)}${u.km && !String(u.title).includes(" km") ? `, ${km(u.km)} km` : ""}</p>
          <div class="btn-row" style="margin-top:0">
            <a class="btn small" href="#/log/${u.day}?s=done">Yes, log it</a>
            <button class="btn quiet small" type="button" data-missed="${u.day}">No, I missed it</button>
          </div>
        </div>`).join("")}
      ${t.flags.length ? `<ul class="flags">${t.flags.map((f) => `<li data-kind="${esc(f.kind)}">${esc(f.text)}</li>`).join("")}</ul>` : ""}
      ${cardHtml(c, { big, actions, context: ctx })}
      ${ex.side_quest ? `<p class="why" style="margin-top:20px"><strong>${esc(ex.side_quest.label)}:</strong> ${esc(ex.side_quest.text)}</p>` : ""}
      <p class="why" style="margin-top:20px">Ran something that wasn't in the plan? <a href="#/extra">Log an extra run</a></p>
      ${t.streak > 1 ? `<p class="streak"><strong>${t.streak}</strong> days in a row on plan</p>` : ""}`;

    const couldnt = $("#couldnt");
    if (couldnt) couldnt.addEventListener("click", () => logMissed(t.today));
    main.querySelectorAll("[data-missed]").forEach((b) => b.addEventListener("click", () => logMissed(b.dataset.missed)));
    const gone = $("#pain-gone");
    if (gone) gone.addEventListener("click", async () => {
      const r = await api("/api/pain/gone", { rid: S.rid }); flash(r.message); route();
    });
    bindOk(route);
    if (window.Extras && !big) window.Extras.spin(ex, $("#km"), S.rid, t.today);
  }

  async function logMissed(day) {
    const r = await api("/api/log", { rid: S.rid, day, status: "missed" });
    flash(r.message);
    route();
  }

  // "31:20", "1:05:30" or "31" (minutes) -> seconds
  function parseTime(v) {
    const parts = String(v || "").trim().split(":").map((x) => x.trim());
    if (!parts[0] || parts.some((x) => !/^\d+$/.test(x)) || parts.length > 3) return null;
    const n = parts.map(Number);
    if (n.length === 1) return n[0] * 60;
    if (n.slice(1).some((x) => x > 59)) return null;
    return n.length === 2 ? n[0] * 60 + n[1] : n[0] * 3600 + n[1] * 60 + n[2];
  }
  const clockText = (secs) => {
    const s = Math.round(secs), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
  };

  async function screenLog(day, preset, extra = false) {
    const t = S.today;
    const d = day === "today" ? t.today : day;
    const info = await api(`/api/day?rid=${S.rid}&day=${d}`);
    const c = info.card;
    const ex = t.extras || {};
    const log = extra ? null : info.log;
    const armed = d === t.today && ex.big_day;  // Big Day: leave distance blank so the log is what she ran
    const prevKm = log && log.distance_km != null ? log.distance_km : (armed || extra ? "" : (c.distance_km || ""));
    const status = extra ? "done" : preset || (log && log.status) || "done";
    const opt = (name, val, label, cur, cls = "") => `
      <label class="choice ${cls}"><input type="radio" name="${name}" value="${val}" ${String(cur) === String(val) ? "checked" : ""}><span>${label}</span></label>`;
    const rw = !extra && c.run_walk && c.run_walk !== "continuous";
    const breakOpts = rw
      ? [["planned", `Kept to ${c.breaks}`], ["some", "A few extra walks"], ["many", "Many extra walks"]]
      : [["none", "No breaks"], ["some", "A few short walks"], ["many", "Many walks or stops"]];
    const minAgo = addDays(t.today, -14);
    main.innerHTML = `
      <h1>${extra ? "Log an extra run" : "Log a run"}</h1>
      <p class="lede">${extra
        ? "A run that wasn't in your plan. It counts towards your weekly total, so the safety checks see your real running."
        : `${esc(fmtDay(d, { weekday: "long", day: "numeric", month: "long" }))}: ${esc(c.title)}`}</p>
      <form id="log" novalidate>
        ${extra ? `
          <div class="field"><label for="when-run">Day</label>
            <input class="input short" id="when-run" name="day" type="date" value="${t.today}" min="${minAgo}" max="${t.today}" required></div>` : `
          <fieldset class="field"><legend>How did it go?</legend>
            <div class="choices">${opt("status", "done", "Done", status)}${opt("status", "partial", "Partly", status)}${opt("status", "missed", "Missed", status)}</div>
          </fieldset>`}
        <div id="ran">
          <div class="field" style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end">
            <div><label for="dist" style="display:block;font-weight:700;margin-bottom:8px">Distance (km)</label>
              <input class="input short" id="dist" name="distance_km" type="number" step="0.01" min="0" max="42.2" inputmode="decimal" value="${esc(prevKm)}"></div>
            <div><label for="time" style="display:block;font-weight:700;margin-bottom:8px">Time</label>
              <input class="input short" id="time" name="time" inputmode="numeric" placeholder="31:20" autocomplete="off"
                value="${log && log.minutes ? clockText(log.minutes * 60) : ""}" aria-describedby="time-hint"></div>
            <output class="pace-out" id="pace" for="dist time" aria-live="polite"></output>
          </div>
          <p class="hint" id="time-hint" style="margin-top:-16px">Minutes and seconds from your watch or phone, like 31:20. Your pace is worked out for you.</p>
          <fieldset class="field"><legend>Did you take breaks?</legend>
            <div class="choices">${breakOpts.map(([v, l]) => opt("breaks", v, esc(l), (log && log.breaks) || "")).join("")}</div>
          </fieldset>
          <fieldset class="field"><legend>How hard did it feel?</legend>
            <p class="hint">1 is very easy, 10 is as hard as you can go.</p>
            <div class="effort choices">
              ${FACES.map((f, i) => `<label class="choice"><input type="radio" name="effort" value="${i + 1}" ${log && log.effort === i + 1 ? "checked" : ""}><span><span class="face" aria-hidden="true">${f}</span>${i + 1}</span></label>`).join("")}
            </div>
            <div class="effort-scale"><span>Very easy</span><span>Hardest</span></div>
          </fieldset>
        </div>
        <fieldset class="field"><legend>Any pain?</legend>
          <div class="choices">
            ${opt("pain", "none", "None", (log && log.pain) || "none")}
            ${opt("pain", "soreness", "Normal soreness", (log && log.pain) || "none")}
            ${opt("pain", "sharp", "Sharp, or I'm limping", (log && log.pain) || "none", "pain")}
          </div>
        </fieldset>
        <div class="field" id="where" hidden>
          <label for="pw">Where does it hurt?</label>
          <input class="input" id="pw" name="pain_where" placeholder="For example: left knee">
        </div>
        ${extra ? "" : `<fieldset class="field"><legend>Last night's sleep</legend>
          <div class="choices">${opt("sleep", "good", "Good", "")}${opt("sleep", "ok", "OK", "")}${opt("sleep", "poor", "Poor", "")}</div>
        </fieldset>`}
        <div class="field"><label for="note">Note <span class="muted">(optional)</span></label>
          <input class="input" id="note" name="note" maxlength="200"></div>
        <p class="error" id="err" role="alert"></p>
        <button class="btn primary" type="submit" style="min-width:12rem">Save</button>
      </form>`;
    const form = $("#log");
    const sync = () => {
      const f = new FormData(form);
      $("#ran").hidden = f.get("status") === "missed";
      $("#where").hidden = f.get("pain") === "none" || !f.get("pain");
      const secs = parseTime(f.get("time")), dist = Number(f.get("distance_km"));
      const out = $("#pace");
      if (secs && dist > 0) {
        const p = secs / 60 / dist;
        out.innerHTML = `<strong>${clockText(p * 60)} /km</strong><span>${(60 / p).toFixed(1)} km/h</span>`;
      } else out.textContent = "";
    };
    form.addEventListener("input", sync); form.addEventListener("change", sync); sync();
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(form);
      const st = extra ? "done" : f.get("status");
      const body = { rid: S.rid, day: extra ? f.get("day") : d, status: st, pain: f.get("pain") || "none",
        pain_where: f.get("pain_where") || null, sleep: f.get("sleep") || null, note: f.get("note") || null, extra };
      if (st !== "missed") {
        const secs = parseTime(f.get("time"));
        if (f.get("time") && !secs) { $("#err").textContent = "Write the time like 31:20 (minutes:seconds)."; return; }
        body.distance_km = f.get("distance_km") ? Number(f.get("distance_km")) : null;
        body.minutes = secs ? Math.round(secs / 6) / 10 : null;
        body.effort = f.get("effort") ? Number(f.get("effort")) : null;
        body.breaks = f.get("breaks") || null;
        if (!body.minutes || !body.effort) {           // R-28
          $("#err").textContent = "Please add your time and how hard it felt (1–10).";
          return;
        }
        if (extra && !body.distance_km) { $("#err").textContent = "Add how far you ran."; return; }
      }
      const r = await api("/api/log", body);
      if (!r.ok) { $("#err").textContent = (r.errors || []).join(" "); return; }
      flash(r.message, body.pain === "sharp");
      if (window.Extras && st === "done" && body.pain !== "sharp" && !extra) window.Extras.sound(ex);
      location.hash = "#/today";
    });
  }

  function screenPain() {
    main.innerHTML = `
      <h1>Tell me about the pain</h1>
      <p class="lede">The app never suggests running through pain.</p>
      <form id="pain">
        <fieldset class="field"><legend>What kind of pain?</legend>
          <div class="choices">
            <label class="choice"><input type="radio" name="pain" value="soreness" required><span>Normal soreness</span></label>
            <label class="choice pain"><input type="radio" name="pain" value="sharp"><span>Sharp, or it changes how I move</span></label>
          </div>
        </fieldset>
        <div class="field"><label for="pw">Where?</label><input class="input" id="pw" name="pain_where" placeholder="For example: right shin"></div>
        <p class="error" id="err" role="alert"></p>
        <button class="btn danger" type="submit">Save</button>
      </form>`;
    $("#pain").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      if (!f.get("pain")) { $("#err").textContent = "Choose one."; return; }
      const r = await api("/api/pain", { rid: S.rid, pain: f.get("pain"), pain_where: f.get("pain_where") || null });
      flash(r.message, f.get("pain") === "sharp");
      location.hash = "#/today";
    });
  }

  function stateOf(d, today) {
    if (d.log) {
      if (d.log.status === "missed") return ["missed", d.log.pain === "sharp" ? "Pain" : "Missed"];
      const k = d.log.distance_km != null ? `${km(d.log.distance_km)} km` : "";
      return ["done", `${d.log.status === "partial" ? "Partly" : "Done"} ✓ ${k}`.trim()];
    }
    if (d.day === today) return ["", "Today"];
    if (d.day < today && d.type !== "rest") return ["missed", "Not logged"];
    return d.elevated && !d.ok ? ["needs-ok", "Needs your OK"] : ["", ""];
  }

  async function screenWeek(start) {
    const data = await api(`/api/plan?rid=${S.rid}`);
    const mon = start || monday(data.today);
    const days = Array.from({ length: 7 }, (_, i) => addDays(mon, i));
    const byDay = Object.fromEntries(data.days.map((d) => [d.day, d]));
    let planned = 0, done = 0;
    const rows = days.map((d) => {
      const x = byDay[d] || { day: d, type: "rest", label: "Rest", title: "Rest", km: 0 };
      if (x.km && x.type !== "walk" && x.type !== "race") planned += x.km;
      if (x.log && x.log.status !== "missed" && x.type !== "walk") done += x.log.distance_km || 0;
      done += x.extra_km || 0;
      let [cls, txt] = stateOf(x, data.today);
      if (x.extra_km) { txt = `${txt ? `${txt}, ` : ""}+${km(x.extra_km)} km extra`; cls = cls || "done"; }
      return `
        <li><a href="#/day/${d}" class="${d === data.today ? "today-row" : ""}" data-type="${esc(x.type)}">
          <span class="d">${esc(fmtDay(d, { weekday: "short" }))}<small>${esc(fmtDay(d, { day: "numeric", month: "short" }))}</small></span>
          <span class="what"><span class="swatch"></span><span>${esc(x.type === "rest" ? "Rest" : x.title)}${x.km && !String(x.title).includes(" km") ? `, ${km(x.km)} km` : ""}</span></span>
          <span class="state ${cls}">${esc(txt)}</span>
        </a></li>`;
    }).join("");
    main.innerHTML = `
      <div class="week-nav">
        <a class="btn quiet small" href="#/week/${addDays(mon, -7)}" aria-label="Previous week">‹ Earlier</a>
        <h1 style="font-size:1.5rem;text-align:center">${esc(fmtDay(mon, { day: "numeric", month: "short" }))} to ${esc(fmtDay(addDays(mon, 6), { day: "numeric", month: "short" }))}</h1>
        <a class="btn quiet small" href="#/week/${addDays(mon, 7)}" aria-label="Next week">Later ›</a>
      </div>
      <ul class="week">${rows}</ul>
      <p class="week-total"><span>Running this week</span><span>${km(done)} of ${km(planned)} km</span></p>
      <p><a class="btn quiet small" href="#/extra">Log an extra run</a></p>
      ${mon !== monday(data.today) ? `<p><a href="#/week">Back to this week</a></p>` : ""}`;
  }

  const TYPES = [["easy", "Easy"], ["long", "Long"], ["tempo", "Hard"], ["race_pace", "Race pace"],
    ["walk", "Walk"], ["strength", "Strength"]];

  async function screenCalendar() {
    const data = await api(`/api/plan?rid=${S.rid}`);
    const byDay = Object.fromEntries(data.days.map((d) => [d.day, d]));
    const months = [...new Set(data.days.map((d) => d.day.slice(0, 7)))];
    const html = months.map((m) => {
      const first = `${m}-01`;
      const lead = (ymd(first).getDay() + 6) % 7;
      const n = new Date(Number(m.slice(0, 4)), Number(m.slice(5)), 0).getDate();
      const cells = [];
      for (let i = 0; i < lead; i++) cells.push(`<span class="blank"></span>`);
      for (let i = 1; i <= n; i++) {
        const d = `${m}-${String(i).padStart(2, "0")}`;
        const x = byDay[d];
        const run = x && x.km > 0 && x.type !== "rest";
        const tinted = run || (x && x.type === "strength");
        const done = x && ((x.log && x.log.status !== "missed") || x.extra_km);
        const cls = [d === data.today ? "is-today" : "", d < data.today ? "past" : "", x && x.type === "race" ? "race" : ""].join(" ");
        cells.push(`<a href="#/day/${d}" class="${cls}" ${x ? `data-type="${esc(x.type)}"` : ""} ${tinted ? "data-run" : ""}
          aria-label="${esc(fmtDay(d, { weekday: "long", day: "numeric", month: "long" }))}: ${esc(x ? x.title : "nothing planned")}${done ? ", done" : ""}">
          <span class="n">${i}</span>${done ? `<span class="tick" aria-hidden="true">✓</span>` : ""}
          <span class="k">${x && x.type === "race" ? "Race" : run ? km(x.km) : x && x.type === "strength" ? "Gym" : ""}</span></a>`);
      }
      return `<section class="month"><h2>${esc(ymd(first).toLocaleDateString("en-GB", { month: "long", year: "numeric" }))}</h2>
        <div class="cal">${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((w) => `<span class="dow">${w}</span>`).join("")}${cells.join("")}</div></section>`;
    }).join("");
    main.innerHTML = `
      <div class="week-nav"><h1>Calendar</h1>
        <a class="btn quiet small" href="/api/export.ics?rid=${S.rid}" download>Export to calendar app</a></div>
      <div class="legend">${TYPES.map(([k, v]) => `<span data-type="${k}"><i></i>${v}</span>`).join("")}<span>✓ done</span></div>
      <div style="margin-top:24px">${html}</div>`;
    const cur = main.querySelector(".is-today");
    if (cur) cur.closest(".month").scrollIntoView({ block: "start" });
  }

  async function screenDay(d) {
    const info = await api(`/api/day?rid=${S.rid}&day=${d}`);
    const c = info.card;
    const t = S.today;
    if (info.is_today) { location.hash = "#/today"; return; }
    const big = null; // Big Day shows only on today's card
    let after = riskHtml(c);
    const runText = (l) => [
      l.distance_km != null ? `${km(l.distance_km)} km` : "",
      l.minutes ? `in ${clockText(l.minutes * 60)}` : "",
      l.minutes && l.distance_km ? `(${clockText((l.minutes * 60) / l.distance_km)} /km)` : "",
      l.effort ? `effort ${l.effort} of 10` : "",
      { none: "no breaks", planned: "breaks as planned", some: "a few extra walks", many: "many extra walks" }[l.breaks] || "",
      l.pain !== "none" ? (l.pain === "sharp" ? "sharp pain" : "soreness") : "",
    ].filter(Boolean).join(", ");
    (info.extra_runs || []).forEach((l) => { after += `<p class="note plain"><strong>Extra run:</strong> ${esc(runText(l))}.</p>`; });
    if (info.log) {
      const l = info.log;
      after += `<p class="note plain"><strong>Logged:</strong> ${l.status === "missed" ? "missed" : `${l.status === "partial" ? "partly done" : "done"}, ${esc(runText(l))}`}.</p>`;
    } else if (info.is_past && c.type !== "rest") {
      after += `<div class="btn-row"><a class="btn small" href="#/log/${d}">Log this day</a></div>`;
    }
    main.innerHTML = `
      <p><a href="#/week/${monday(d)}">‹ Week of ${esc(fmtDay(monday(d), { day: "numeric", month: "short" }))}</a></p>
      ${cardHtml(c, { big, actions: after, context: fmtDay(d, { weekday: "long", day: "numeric", month: "long" }) })}`;
    bindOk(() => screenDay(d));
    void t;
  }

  async function screenCoach(prefill) {
    const notes = await api(`/api/notes?rid=${S.rid}`);
    const ex = S.today.extras || {};
    const examples = ["Going to Japan 31 Oct–9 Nov, no running", "Felt very hard today", "I'm sick", "I'm better", "The pain is gone"];
    main.innerHTML = `
      <h1>Tell the coach</h1>
      <p class="lede">Trips, sick days, how a run felt, or a change you want. Every change still goes through the safety checks, and you review it first.</p>
      <div class="examples">${examples.map((x) => `<button type="button">${esc(x)}</button>`).join("")}</div>
      <form id="tell">
        <label for="msg" class="field" style="display:block;margin-bottom:8px;font-weight:700">Your message</label>
        <textarea class="input" id="msg" maxlength="500" required>${esc(prefill || "")}</textarea>
        <div class="btn-row"><button class="btn primary" type="submit">Send</button></div>
        <p class="working" id="busy" hidden>${esc(ex.loading || "Reading your message…")}</p>
        <p class="error" id="err" role="alert"></p>
      </form>
      ${notes.length ? `<section class="section"><h2>Earlier messages</h2><ul class="thread">
        ${notes.map((n) => `<li><p class="me" style="margin:0">${esc(n.text)}</p><p class="reply">${esc(n.reply)}</p></li>`).join("")}
      </ul></section>` : ""}`;
    main.querySelectorAll(".examples button").forEach((b) => b.addEventListener("click", () => { $("#msg").value = b.textContent; $("#msg").focus(); }));
    $("#tell").addEventListener("submit", async (e) => {
      e.preventDefault();
      const text = $("#msg").value.trim();
      if (!text) return;
      $("#busy").hidden = false; e.submitter && (e.submitter.disabled = true);
      try {
        const r = await api("/api/tell", { rid: S.rid, text });
        flash(r.reply);
        await refreshToday();
        render(screenCoach);
      } catch (err) { $("#err").textContent = err.message; $("#busy").hidden = true; }
    });
  }

  async function screenReview() {
    const data = await api(`/api/proposals?rid=${S.rid}`);
    if (!data.proposals.length) {
      main.innerHTML = `${takeFlash()}<h1>Review changes</h1><p class="lede">Nothing to review. Your plan is as it was.</p><p><a href="#/today">Back to today</a></p>`;
      return;
    }
    const desc = (x) => x.type === "rest" ? "Rest" : `${x.label}${x.km ? ` ${km(x.km)} km` : ""}${x.run_walk && x.run_walk !== "continuous" ? ` (${x.run_walk})` : ""}`;
    main.innerHTML = `
      ${takeFlash()}
      <h1>Review changes</h1>
      <p class="lede">Nothing changes until you accept. Every change has passed the safety checks.</p>
      ${data.proposals.map((p) => `
        <form class="proposal" data-pid="${p.id}">
          <h2>${p.fallback ? "A safer, simpler plan" : "Suggested changes"}</h2>
          <p>${esc(p.summary || "")}</p>
          ${p.changes.map((c) => `
            <div class="change">
              <div class="day">${esc(fmtDay(c.day, { weekday: "long", day: "numeric", month: "short" }))}</div>
              <div class="diff"><span class="old">${esc(desc(c.old))}</span><span class="arrow" aria-label="becomes">→</span><span class="new">${esc(desc(c.new))}</span>
                ${c.elevated ? `<span class="badge">Longer than usual</span>` : ""}</div>
              ${c.reason ? `<p class="why" style="margin:4px 0 0">${esc(c.reason)} <span class="rules">(${c.rule_ids.map(esc).join(", ")})</span></p>` : ""}
              ${c.elevated ? `<label class="choice" style="margin-top:12px;display:inline-block"><input type="checkbox" name="ok" value="${c.day}"><span>I'm OK with this longer run (R-04)</span></label>` : ""}
            </div>`).join("")}
          <p class="error" role="alert"></p>
          <div class="btn-row">
            <button class="btn primary" type="submit" value="accept">Accept</button>
            <button class="btn" type="submit" value="keep">Keep my plan</button>
          </div>
        </form>`).join("")}`;
    main.querySelectorAll("form.proposal").forEach((form) => form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const accept = e.submitter && e.submitter.value === "accept";
      const oks = [...form.querySelectorAll("input[name=ok]:checked")].map((i) => i.value);
      const r = await api(`/api/proposals/${form.dataset.pid}/decide`, { rid: S.rid, accept, oks });
      if (!r.ok) {
        form.querySelector(".error").textContent = r.needs_ok
          ? "Tick the box for each longer run, or choose Keep my plan."
          : r.error || "These changes no longer pass the safety checks, so they were not saved.";
        return;
      }
      flash(accept ? "Changes accepted. Your plan is updated." : "Kept your plan.");
      await refreshToday();
      render(screenReview);
    }));
  }

  async function screenRace() {
    const r = await api(`/api/race?rid=${S.rid}`);
    const toS = (t) => { const p = t.split(":").map(Number); return p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p[0] * 3600 + p[1] * 60; };
    let line = "";
    if (r.prediction && r.goal_time) {
      const lo = toS(r.prediction.low), hi = toS(r.prediction.high), g = toS(r.goal_time);
      const min = Math.min(lo, g) - 600, max = Math.max(hi, g) + 600;
      const pct = (s) => ((s - min) / (max - min)) * 100;
      const hm = (s) => `${Math.floor(s / 3600)}:${String(Math.round((s % 3600) / 60)).padStart(2, "0")}`;
      line = `
        <div class="goal-line" role="img" aria-label="Predicted ${r.prediction.low} to ${r.prediction.high}, goal ${r.goal_time}">
          <div class="track"></div>
          <div class="pred" style="left:${pct(lo)}%;width:${pct(hi) - pct(lo)}%"></div>
          <div class="goal" style="left:${pct(g)}%" data-label="Goal ${esc(r.goal_time)}"></div>
        </div>
        <div class="goal-scale"><span>${hm(min)}</span><span>${hm(max)}</span></div>`;
    }
    main.innerHTML = `
      ${takeFlash()}
      <div class="race-hero">
        <div class="big">${r.days_to_race}</div>
        <div><h1>${r.days_to_race === 1 ? "day" : "days"} to the adidas Half Marathon</h1>
          <p class="muted" style="margin:6px 0 0">${esc(fmtDay(r.race_day, { weekday: "long", day: "numeric", month: "long" }))}, 4:30 am start</p></div>
      </div>
      <section>
        <h2>Predicted finish</h2>
        ${r.prediction ? `
          <p class="range">${esc(r.prediction.low)} to ${esc(r.prediction.high)}</p>
          <p class="why">${r.prediction.kind === "riegel"
            ? `From your latest time trial (${esc(r.prediction.formula)} by formula), plus a heat allowance and water stops. Beginners often land at the slow end, so this is a range, not one number.`
            : `From your jog and walk paces at ${esc(r.prediction.ratio || "your race ratio")}, plus water stops. Measured in Singapore heat, so no heat allowance is added.`}
            <span class="rules">(${r.prediction.rules.join(", ")})</span></p>
          ${line}` : `<p class="lede">Add a time trial below to see a prediction.</p>`}
      </section>
      ${r.pace_band.length ? `
        <section class="section">
          <h2>Goal pace band</h2>
          <p class="why" style="margin-top:0">Your goal sets this band only. Training paces always come from your time trial.</p>
          <table class="band"><thead><tr><th>At</th><th>Goal time</th></tr></thead><tbody>
            ${r.pace_band.map((b) => `<tr><td>${b.km === 21.1 ? "Finish" : `${b.km} km`}</td><td>${esc(b.time)}</td></tr>`).join("")}
          </tbody></table>
        </section>` : ""}
      ${r.zones && Object.keys(r.zones).length ? `
        <section class="section">
          <h2>Your paces</h2>
          <table class="band"><thead><tr><th>Session</th><th>Pace</th><th>Treadmill at 1%</th></tr></thead><tbody>
            ${Object.entries(r.zones).map(([k, z]) => `<tr><td>${esc({ easy: "Easy", long: "Long", tempo: "Tempo", intervals: "Intervals", race: "Race pace" }[k] || k)}</td><td>${esc(z.pace)} /km</td><td>${esc(z.speed)} km/h</td></tr>`).join("")}
          </tbody></table>
        </section>` : ""}
      ${r.guide ? `
        <section class="section">
          <h2>Your run-walk guide</h2>
          <p>Go by the timer and the talk test. If your phone records the run, these are a rough guide: jog ${esc(r.guide.jog)} /km, brisk walk ${esc(r.guide.walk)} /km.</p>
        </section>` : ""}
      <section class="section">
        <h2>Time trials</h2>
        <p>A time trial is a timed run at your best even effort, usually 5 km: hard, but steady from start to finish.
        It is how the app knows your current fitness. Your training paces, treadmill speeds and race prediction all come from your latest one.</p>
        ${r.next_time_trial ? `<p>Your next one is on <a href="#/day/${r.next_time_trial}">${esc(fmtDay(r.next_time_trial, { weekday: "long", day: "numeric", month: "short" }))}</a>. When you log it, it is saved here automatically.</p>` : ""}
        ${r.time_trials.length ? `<ul class="list">${r.time_trials.map((x) => `<li>${esc(fmtDay(x.day))}: ${km(x.distance_km)} km in ${esc(x.time)} <span class="muted">(${esc({ treadmill_1pct: "treadmill, 1%", treadmill_flat: "treadmill, flat", outdoor_heat: "outdoors, warm", outdoor_cool: "outdoors, cool" }[x.setting])})</span></li>`).join("")}</ul>` : `<p class="muted">None yet.</p>`}
        <form id="trial" style="margin-top:24px">
          <h3 style="margin-bottom:16px">Add a time trial</h3>
          <div style="display:flex;gap:16px;flex-wrap:wrap">
            <div class="field"><label for="td">Date</label><input class="input short" id="td" type="date" required></div>
            <div class="field"><label for="tk">Distance (km)</label><input class="input short" id="tk" type="number" step="0.1" min="1" max="21.1" value="5" required></div>
            <div class="field"><label for="tt">Time (mm:ss)</label><input class="input short" id="tt" placeholder="32:00" pattern="\\d{1,2}(:\\d{2}){1,2}" required></div>
          </div>
          <fieldset class="field"><legend>Where</legend><div class="choices">
            <label class="choice"><input type="radio" name="set" value="treadmill_1pct" required><span>Treadmill, 1% incline</span></label>
            <label class="choice"><input type="radio" name="set" value="treadmill_flat"><span>Treadmill, flat</span></label>
            <label class="choice"><input type="radio" name="set" value="outdoor_heat"><span>Outdoors, warm</span></label>
            <label class="choice"><input type="radio" name="set" value="outdoor_cool"><span>Outdoors, cool</span></label>
          </div></fieldset>
          <p class="error" id="err" role="alert"></p>
          <button class="btn primary" type="submit">Add time trial</button>
        </form>
      </section>`;
    $("#td").value = S.today.today; $("#td").max = S.today.today;
    $("#trial").addEventListener("submit", async (e) => {
      e.preventDefault();
      const set = new FormData(e.target).get("set");
      try {
        await api("/api/time_trial", { rid: S.rid, day: $("#td").value, distance_km: Number($("#tk").value), time: $("#tt").value, setting: set });
        flash("Time trial added. Paces and prediction are updated.");
        render(screenRace);
      } catch (err) { $("#err").textContent = err.message; }
    });
  }

  async function screenProfile() {
    const p = await api(`/api/runners/${S.rid}/profile`);
    const s = p.screening;
    const yes = (v) => (v ? "Yes" : "No");
    main.innerHTML = `
      ${takeFlash()}
      <h1>${esc(p.name)}</h1>
      <p class="lede">${esc([p.age ? `${p.age}` : "", p.level === "starter" ? "first-time runner" : p.level, p.run_walk ? "run-walk" : "continuous running"].filter(Boolean).join(", "))}</p>
      <dl class="dl">
        <dt>Goal time</dt><dd>${esc(p.goal_time || "Not set")}</dd>
        <dt>Treadmill</dt><dd>${p.has_treadmill ? "Yes" : "No, every session outdoors"}</dd>
        <dt>Previous injury</dt><dd>${p.prior_injury ? "Yes" : "None"}</dd>
        <dt>Fuel list</dt><dd>${p.fuel.map(esc).join(", ")}</dd>
        ${p.body ? `<dt>Height and weight</dt><dd>${p.body.height_cm} cm, ${p.body.weight_kg} kg</dd>` : ""}
      </dl>

      <section class="section">
        <h2>Change details</h2>
        <form id="prof">
          <div class="field"><label for="diet">Diet</label>
            <select class="input" id="diet" style="max-width:24rem">${p.diet_confirmed ? "" : `<option value="" selected>Not set yet</option>`}${Object.entries(p.diets).map(([k, v]) => `<option value="${k}" ${k === p.diet && p.diet_confirmed ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
          <div class="field"><label for="goal">Goal time (h:mm)</label>
            <input class="input short" id="goal" value="${esc(p.goal_time || "")}" placeholder="2:45" pattern="\\d:\\d{2}(:\\d{2})?"></div>
          <fieldset class="field"><legend>Treadmill access</legend><div class="choices">
            <label class="choice"><input type="radio" name="tm" value="1" ${p.has_treadmill ? "checked" : ""}><span>Yes</span></label>
            <label class="choice"><input type="radio" name="tm" value="0" ${p.has_treadmill ? "" : "checked"}><span>No</span></label>
          </div></fieldset>
          <p class="error" id="err" role="alert"></p>
          <button class="btn primary" type="submit">Save details</button>
        </form>
      </section>

      <section class="section">
        <h2>Health questions</h2>
        ${s ? `<dl class="dl">
          <dt>Active 3 days a week</dt><dd>${yes(s.active_3x_week)}</dd>
          <dt>Known disease</dt><dd>${yes(s.known_disease)}</dd>
          <dt>Symptoms</dt><dd>${yes(s.symptoms)}</dd>
          <dt>Answered</dt><dd>${esc(fmtDay(s.answered_on))}</dd>
          ${s.doctor_cleared_on ? `<dt>Doctor cleared</dt><dd>${esc(fmtDay(s.doctor_cleared_on))}</dd>` : ""}
        </dl>` : `<p>Not answered yet.</p>`}
        <div class="btn-row"><button class="btn quiet small" type="button" id="reanswer">Answer again</button></div>
      </section>

      <section class="section">
        <h2>Trips and sick days</h2>
        ${p.availability.length ? `<ul class="list">${p.availability.map((a) => `<li>${esc(fmtDay(a.start_day))} to ${esc(fmtDay(a.end_day))}: ${esc(a.note || a.kind)}${a.can_run ? "" : " (no running)"}</li>`).join("")}</ul>` : `<p class="muted">None.</p>`}
        <p class="why">Add a trip or sick day in <a href="#/coach">Tell the coach</a>.</p>
      </section>

      <section class="section">
        <h2>Backup</h2>
        <p>The app makes a copy every day. You can also save one anywhere you like.</p>
        <a class="btn quiet small" href="/api/backup" download>Export backup</a>
      </section>`;
    $("#prof").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api(`/api/runners/${S.rid}/profile`, { diet: $("#diet").value || null, goal_time: $("#goal").value || null,
          has_treadmill: new FormData(e.target).get("tm") === "1" });
        flash("Details saved."); await refreshToday(); render(screenProfile);
      } catch (err) { $("#err").textContent = err.message; }
    });
    $("#reanswer").addEventListener("click", () => render(screenSetup));
  }

  // ---------- router ----------

  async function render(fn, ...args) {
    try { await fn(...args); }
    catch (err) {
      main.innerHTML = `<h1>Something went wrong</h1><p class="lede">${esc(err.message)}</p><button class="btn" type="button" id="retry">Try again</button>`;
      $("#retry").addEventListener("click", route);
    }
    main.focus({ preventScroll: true });
  }

  async function route() {
    clearTimeout(S.poll);
    const [path, query] = location.hash.replace(/^#/, "").split("?");
    const parts = path.split("/").filter(Boolean);
    const q = new URLSearchParams(query || "");
    if (!S.rid) {
      const saved = Number(store("rid"));
      S.runners = await api("/api/runners").catch(() => []);
      if (saved && S.runners.some((r) => r.id === saved)) S.rid = saved;
    }
    if (!S.rid || parts[0] === "who") return render(screenWho);
    try { await refreshToday(); } catch (e) { S.rid = null; return render(screenWho); }
    window.scrollTo(0, 0);
    if (S.today.gate === "unanswered") return render(screenSetup);
    if (S.today.gate === "locked") return render(screenLocked);
    switch (parts[0] || "today") {
      case "log": return render(screenLog, parts[1] || "today", q.get("s"));
      case "pain": return render(screenPain);
      case "extra": return render(screenLog, "today", "done", true);
      case "week": return render(screenWeek, parts[1]);
      case "calendar": return render(screenCalendar);
      case "day": return render(screenDay, parts[1]);
      case "coach": return render(screenCoach, q.get("ask"));
      case "review": return render(screenReview);
      case "race": return render(screenRace);
      case "profile": return render(screenProfile);
      default: return render(screenToday);
    }
  }

  $("#who").addEventListener("click", () => { location.hash = "#/who"; });
  window.addEventListener("hashchange", route);
  if (window.Knock) window.Knock.attach($("#countdown"), async (taps) => {
    if (!S.rid) return;
    await api("/api/knock", { rid: S.rid, taps }).catch(() => null);
    route();  // reload the data; the countdown wording is the only visible change
  });

  paintSky();
  setInterval(paintSky, 15000);
  checkOnline();
  setInterval(checkOnline, 60000);
  route();
})();
