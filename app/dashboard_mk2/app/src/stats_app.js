  // ================================================================== statistics model (matches, picks, bets)
  const STAT = { shots: "Strzały", sot: "Celne strzały", corners: "Rożne", fouls: "Faule", cards: "Kartki", yellow: "Żółte kartki", offsides: "Spalone" };
  const NOUN = { shots: "strzałów", sot: "celnych strzałów", corners: "rożnych", fouls: "fauli", cards: "kartek", yellow: "żółtych kartek", offsides: "spalonych" };
  const MAIN_STATS = ["shots", "sot", "corners", "fouls", "cards", "offsides"];
  const PICKABLE = new Set(["shots", "sot", "corners"]);
  const SCOPE_PL = { home: "gospodarz", away: "gość", total: "łącznie" };
  const TAX = 0.88;
  const st = (k) => S.data["st_" + k];
  const stMeta = () => st("meta") || {};
  const NF1 = nf(1);
  const d1 = (v) => (empty(v) ? "—" : NF1.format(v));
  const line = (v) => (empty(v) ? "—" : String(v).replace(".", ","));
  const HM = new Intl.DateTimeFormat("pl-PL", { timeZone: "Europe/Warsaw", hour: "2-digit", minute: "2-digit" });
  const hm = (iso) => { const d = new Date(iso); return isNaN(d) ? "—" : HM.format(d); };
  const KEYF = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Warsaw", year: "numeric", month: "2-digit", day: "2-digit" });
  const dayKey = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : KEYF.format(d); };
  function dayLabel(key) {
    const today = dayKey(new Date().toISOString());
    const tomorrow = dayKey(new Date(Date.now() + 864e5).toISOString());
    if (key === today) return "Dziś";
    if (key === tomorrow) return "Jutro";
    const [y, m, d] = key.split("-").map(Number);
    return new Intl.DateTimeFormat("pl-PL", { weekday: "short", day: "2-digit", month: "2-digit", timeZone: "UTC" }).format(new Date(Date.UTC(y, m - 1, d, 12)));
  }
  const ligaName = (id) => ((stMeta().ligi || {})[String(id)]) || ("rozgrywki " + id);
  const ligaShort = (id) => ((stMeta().skroty || {})[String(id)]) || String(id);
  function ligaRank(id) {
    const m = stMeta();
    const cups = m.puchary || [], top = m.top15 || [];
    if (cups.includes(id)) return cups.indexOf(id);
    if (top.includes(id)) return 10 + top.indexOf(id);
    return 100 + id;
  }
  function alphaOf(stat, scope) {
    const m = (stMeta().model || {})[stat];
    if (!m) return 0.05;
    return scope === "total" ? m.alpha_total : m.alpha;
  }
  const meanOf = (m, stat, scope) => {
    const v = m.m[stat];
    if (!v) return null;
    return scope === "home" ? v[0] : scope === "away" ? v[1] : v[0] + v[1];
  };
  // NB2 (Var = mu + alpha mu^2): P(X > line), P(X = line) for whole lines, P(X < line)
  function nbTail(mu, alpha, ln) {
    const a = Math.max(alpha || 0, 1e-6), r = 1 / a, p = r / (r + mu);
    let pmf = Math.exp(r * Math.log(p)), under = 0, push = 0;
    for (let k = 0; k <= Math.floor(ln); k++) {
      if (k > 0) pmf *= ((k - 1 + r) / k) * (1 - p);
      if (k < ln) under += pmf; else if (k === ln) push = pmf;
    }
    return { over: Math.max(0, 1 - under - push), push, under };
  }
  const atLeast = (mu, alpha, k) => (k <= 0 ? 1 : nbTail(mu, alpha, k - 0.5).over);
  const atMost = (mu, alpha, k) => nbTail(mu, alpha, k + 0.5).under;
  function threshold(mu, alpha, level) {
    let best = null;
    for (let k = 1; k < 80; k++) {
      const p = atLeast(mu, alpha, k);
      if (p >= level) best = { k, p }; else break;
    }
    return best;
  }
  const statOrder = (s) => { const i = MAIN_STATS.indexOf(s); return i < 0 ? 99 : i; };
  const SCOPE_ORDER = { home: 0, away: 1, total: 2 };
  const minOdds = (p) => (p > 0 ? 1 / (p * TAX) : null);
  const fairOdds = (p) => (p > 0 ? 1 / p : null);
  const matches = () => st("mecze") || [];
  const matchById = (id) => matches().find((m) => String(m.id) === String(id));
  const teamOf = (id) => (st("druzyny") || {})[String(id)];
  function pickKeys() {
    const out = new Set();
    Object.values(st("typy") || {}).forEach((d) => (d.top || []).forEach((p) => out.add(p.fixture_id + "|" + p.stat + "|" + p.scope + "|" + p.line + "|" + p.side)));
    return out;
  }
  function inLigaFilter(m, f) {
    const meta = stMeta();
    if (!f || f === "main") return (meta.puchary || []).includes(m.liga_id) || (meta.top15 || []).includes(m.liga_id);
    if (f === "all") return true;
    if (f === "cups") return (meta.puchary || []).includes(m.liga_id);
    return String(m.liga_id) === f;
  }
  function fillLigaSelect(sel, value) {
    const meta = stMeta();
    const ids = [...new Set(matches().map((m) => m.liga_id))].sort((a, b) => ligaRank(a) - ligaRank(b));
    const opts = [new Option("Top 15 lig + puchary", "main"), new Option("Puchary europejskie", "cups"), new Option("Wszystkie (także dodatkowe ligi)", "all")];
    ids.forEach((id) => opts.push(new Option(ligaName(id), String(id))));
    if (sel.options.length !== opts.length) sel.replaceChildren(...opts);
    sel.value = value || "main";
    if (!meta.ligi) sel.disabled = true; else sel.disabled = false;
  }
  function chip(label, pressed, onClick, count) {
    const b = el("button", "mk-chipbtn", label);
    b.type = "button";
    b.setAttribute("aria-pressed", pressed ? "true" : "false");
    if (count !== undefined) b.append(el("span", "n", String(count)));
    b.addEventListener("click", onClick);
    return b;
  }

  // ------------------------------------------------------------------ Mecze
  function renderMecze() {
    const box = $("#m-list");
    if (!st("mecze")) { box.replaceChildren(el("div", "mk-card", "Wczytywanie meczów…")); return; }
    fillLigaSelect($("#m-liga"), S.mz.liga);
    const all = matches().filter((m) => inLigaFilter(m, S.mz.liga));
    const keys = [...new Set(all.map((m) => dayKey(m.start)))].sort();
    if (!S.mz.day || !keys.includes(S.mz.day)) S.mz.day = keys.find((k) => k >= dayKey(new Date().toISOString())) || keys[0] || "";
    $("#m-days").replaceChildren(...keys.map((k) => chip(dayLabel(k), k === S.mz.day, () => { S.mz.day = k; render(); },
      all.filter((m) => dayKey(m.start) === k).length)));
    const q = (S.mz.q || "").trim().toLowerCase();
    const list = all.filter((m) => dayKey(m.start) === S.mz.day && (!q || (m.gosp + " " + m.gosc).toLowerCase().includes(q)))
      .sort((a, b) => ligaRank(a.liga_id) - ligaRank(b.liga_id) || a.start.localeCompare(b.start));
    const picks = pickKeys();
    const pickFix = new Set([...picks].map((k) => k.split("|")[0]));
    box.replaceChildren();
    if (!list.length) { box.append(el("div", "mk-card", q ? "Brak meczów pasujących do wyszukiwania." : "Brak meczów w tym dniu.")); return; }
    let group = null, groupBox = null;
    list.forEach((m) => {
      if (m.liga_id !== group) {
        group = m.liga_id;
        groupBox = el("div", "mk-card mk-group");
        groupBox.append(el("h3", null, ligaName(m.liga_id)));
        box.append(groupBox);
      }
      const b = el("button", "mk-match");
      b.type = "button";
      b.append(el("span", "tm", hm(m.start)));
      const mid = el("span");
      mid.append(el("span", "tt", m.gosp + " – " + m.gosc));
      mid.append(el("span", "st", " " + ["shots", "corners"].map((s) => STAT[s] + " " + d1(m.m[s][0]) + "–" + d1(m.m[s][1])).join(" · ")));
      b.append(mid);
      const bd = el("span", "bd");
      if (pickFix.has(String(m.id))) bd.append(el("span", "mk-chip c-win", "typ dnia"));
      if (m.kursy && m.kursy.length) bd.append(el("span", "mk-chip", "kursy"));
      if (m.wyr && m.wyr.length) bd.append(el("span", "mk-chip", "sygnały: " + m.wyr.length));
      if (m.status && !["NS", "TBD"].includes(m.status)) bd.append(el("span", "mk-chip c-warn", m.status === "FT" ? "koniec" : "trwa"));
      b.append(bd);
      b.addEventListener("click", () => openMatch(m.id));
      groupBox.append(b);
    });
  }
  function openMatch(id) { S.mecz = String(id); S.calc = null; show("mecz", true); }
  $("#m-liga").addEventListener("change", (e) => { S.mz.liga = e.target.value; S.mz.day = ""; render(); });
  $("#m-q").addEventListener("input", (e) => { S.mz.q = e.target.value; render(); });
  $("#mz-back").addEventListener("click", () => show("mecze", true));

  // ------------------------------------------------------------------ Mecz
  function renderMecz() {
    const m = matchById(S.mecz);
    if (!m) {
      $("#mz-title").textContent = st("mecze") ? "Nie znaleziono meczu (może już się odbył)." : "Wczytywanie…";
      ["#mz-meta", "#mz-stats", "#mz-why", "#c-out", "#mz-teams"].forEach((s) => $(s).replaceChildren());
      return;
    }
    const facts = $("#mz-meta");
    facts.replaceChildren();
    const fact = (label, value) => { const s = el("span"); s.append(label + " ", el("b", null, value)); facts.append(s); };
    fact("", ligaName(m.liga_id));
    fact("", day(m.start) + ", " + hm(m.start));
    if (m.runda) fact("Runda:", m.runda.replace("League Stage", "faza ligowa").replace("Regular Season", "kolejka"));
    if (m.sedzia) fact("Sędzia:", m.sedzia);
    $("#mz-title").textContent = m.gosp + " – " + m.gosc;
    const cards = $("#mz-stats");
    cards.replaceChildren();
    MAIN_STATS.forEach((s) => {
      const v = m.m[s];
      if (!v) return;
      const c = el("div", "mk-stat");
      c.append(el("span", "l", STAT[s]));
      const val = el("div", "v");
      val.append(el("span", null, d1(v[0])), el("span", null, d1(v[1])));
      const bar = el("div", "mk-bar");
      const h = el("span", "h"), a = el("span", "a");
      const tot = v[0] + v[1] || 1;
      h.style.width = (100 * v[0] / tot).toFixed(1) + "%";
      a.style.width = (100 * v[1] / tot).toFixed(1) + "%";
      bar.append(h, a);
      c.append(val, bar, el("span", "s", "łącznie " + d1(v[0] + v[1])));
      cards.append(c);
    });
    const why = $("#mz-why");
    why.replaceChildren(...(m.wyr && m.wyr.length ? m.wyr : ["Brak wyraźnych sygnałów: żadna drużyna nie jest skrajna w swojej lidze w tym, co tworzy i na co pozwala rywalom."]).map((t) => el("li", null, t)));
    renderCalc(m);
    renderProgi(m);
    renderKursy(m);
    renderTeams(m);
  }
  function renderCalc(m) {
    const ss = $("#c-stat"), sc = $("#c-scope"), ln = $("#c-line");
    if (!S.calc || S.calc.id !== m.id) {
      S.calc = { id: m.id, stat: "shots", scope: "home", line: null };
      ss.replaceChildren(...MAIN_STATS.map((s) => new Option(STAT[s], s)));
    }
    sc.replaceChildren(new Option(m.gosp, "home"), new Option(m.gosc, "away"), new Option("łącznie w meczu", "total"));
    ss.value = S.calc.stat;
    sc.value = S.calc.scope;
    const mu = meanOf(m, S.calc.stat, S.calc.scope);
    if (S.calc.line === null) S.calc.line = Math.floor(mu) + 0.5;
    if (document.activeElement !== ln) ln.value = S.calc.line;
    const a = alphaOf(S.calc.stat, S.calc.scope);
    const t = nbTail(mu, a, S.calc.line);
    const offer = (m.kursy || []).find((k) => k[0] === S.calc.stat && k[1] === S.calc.scope && k[2] === S.calc.line);
    const out = $("#c-out");
    out.replaceChildren();
    const box = (title, p, odd, bm) => {
      const c = el("div", "mk-stat");
      c.append(el("span", "l", title), el("span", "mk-big", pct(p, 0)));
      c.append(el("span", "s", "kurs uczciwy " + dec(fairOdds(p)) + " · min. kurs z podatkiem " + dec(minOdds(p))));
      if (odd) {
        const ev = p * odd * TAX + t.push * TAX - 1;
        const s = el("span", "s");
        s.append("najlepszy kurs " + dec(odd) + " (" + bm + "), zysk z podatkiem ", el("b", sign(ev), spct(ev)));
        c.append(s);
      }
      return c;
    };
    const who = S.calc.scope === "total" ? "W meczu" : (S.calc.scope === "home" ? m.gosp : m.gosc);
    out.append(box(who + ": " + STAT[S.calc.stat].toLowerCase() + " powyżej " + line(S.calc.line), t.over, offer && offer[3], offer && offer[4]));
    out.append(box(who + ": " + STAT[S.calc.stat].toLowerCase() + " poniżej " + line(S.calc.line), t.under, offer && offer[5], offer && offer[6]));
    const info = el("div", "mk-stat");
    info.append(el("span", "l", "Średnio wg modelu"), el("span", "mk-big", d1(mu)));
    if (t.push > 0) info.append(el("span", "s", "dokładnie " + line(S.calc.line) + ": " + pct(t.push, 0) + " (zwrot)"));
    if (offer && offer[7] !== null) info.append(el("span", "s", "rynek: powyżej " + pct(offer[7], 0)));
    const add = el("button", "mk-btn ghost", "Dodaj do zakładów");
    add.type = "button";
    add.addEventListener("click", () => draftBet({
      mecz: m.gosp + " – " + m.gosc, rozgr: ligaName(m.liga_id), data: dayKey(m.start), szansa: t.over,
      opis: who + ": " + NOUN[S.calc.stat] + " powyżej " + line(S.calc.line),
      kurs: offer ? offer[3] : null, nogi: [{ fixture_id: m.id, stat: S.calc.stat, scope: S.calc.scope, line: S.calc.line, side: "over" }],
    }));
    info.append(add);
    out.append(info);
  }
  $("#mz-all").addEventListener("change", (e) => { S.allLines = e.target.checked; render(); });
  $("#c-stat").addEventListener("change", (e) => { if (S.calc) { S.calc.stat = e.target.value; S.calc.line = null; } render(); });
  $("#c-scope").addEventListener("change", (e) => { if (S.calc) { S.calc.scope = e.target.value; S.calc.line = null; } render(); });
  $("#c-line").addEventListener("input", (e) => {
    const v = parseFloat(e.target.value);
    if (S.calc && Number.isFinite(v) && v > 0) { S.calc.line = Math.round(v * 2) / 2; render(); }
  });
  function renderProgi(m) {
    const rowsP = [];
    [["home", m.gosp], ["away", m.gosc], ["total", "łącznie"]].forEach(([scope, who]) => {
      MAIN_STATS.forEach((s) => {
        const mu = meanOf(m, s, scope);
        if (mu === null) return;
        const a = alphaOf(s, scope);
        const t70 = threshold(mu, a, 0.70), t55 = threshold(mu, a, 0.55);
        rowsP.push({ key: scope + s, kto: who, stat: STAT[s], mu,
          k70: t70 && t70.k, p70: t70 && t70.p, o70: t70 && minOdds(t70.p),
          k55: t55 && t55.k, p55: t55 && t55.p, o55: t55 && minOdds(t55.p) });
      });
    });
    table($("#mz-progi"), rowsP, [
      { f: "kto", h: "Kto", nw: true },
      { f: "stat", h: "Statystyka", nw: true },
      { f: "mu", h: "Średnio", num: true, fmt: d1 },
      { f: "k70", h: "≥ (70%)", num: true, fmt: (v, r) => (v ? "co najmniej " + v : "—") },
      { f: "p70", h: "Szansa", num: true, fmt: (v) => pct(v, 0) },
      { f: "o70", h: "Min. kurs", num: true, fmt: dec },
      { f: "k55", h: "≥ (55%)", num: true, fmt: (v) => (v ? "co najmniej " + v : "—") },
      { f: "p55", h: "Szansa", num: true, fmt: (v) => pct(v, 0) },
      { f: "o55", h: "Min. kurs", num: true, fmt: dec },
    ], "key", { nosortAll: true });
  }
  function renderKursy(m) {
    const picks = pickKeys();
    const rowsK = (m.kursy || []).map((k) => {
      const [stat, scope, ln, oOver, bOver, oUnder, bUnder, qOver, nBm] = k;
      const mu = meanOf(m, stat, scope);
      const t = nbTail(mu, alphaOf(stat, scope), ln);
      const evO = oOver ? t.over * oOver * TAX + t.push * TAX - 1 : null;
      const evU = oUnder ? t.under * oUnder * TAX + t.push * TAX - 1 : null;
      return { key: stat + scope + ln, stat, scope, ln, statL: STAT[stat] || stat, kto: scope === "home" ? m.gosp : scope === "away" ? m.gosc : "łącznie",
        pO: t.over, q: qOver, oOver, bOver, oUnder, bUnder, evO, evU, nBm,
        typ: picks.has(m.id + "|" + stat + "|" + scope + "|" + ln + "|over") ? "powyżej" : picks.has(m.id + "|" + stat + "|" + scope + "|" + ln + "|under") ? "poniżej" : "" };
    }).sort((a, b) => statOrder(a.stat) - statOrder(b.stat) || SCOPE_ORDER[a.scope] - SCOPE_ORDER[b.scope] || a.ln - b.ln);
    // by default only the lines near the middle of the market (where its probability is 20-80%)
    const shown = S.allLines ? rowsK : rowsK.filter((r) => r.q === null || (r.q >= 0.2 && r.q <= 0.8));
    $("#mz-all").checked = !!S.allLines;
    $("#mz-lines").textContent = "Pokazano " + shown.length + " z " + rowsK.length + " linii.";
    table($("#mz-kursy"), shown, [
      { f: "statL", h: "Statystyka", nw: true },
      { f: "kto", h: "Kto", nw: true },
      { f: "ln", h: "Linia", num: true, fmt: line },
      { f: "pO", h: "Model: powyżej", num: true, fmt: (v) => pct(v, 0) },
      { f: "q", h: "Rynek: powyżej", num: true, fmt: (v) => pct(v, 0) },
      { f: "oOver", h: "Powyżej: kurs", num: true, render: (td, r) => { td.textContent = r.oOver ? dec(r.oOver) + " " : "—"; if (r.bOver) td.append(el("span", "c-muted", r.bOver)); } },
      { f: "evO", h: "Zysk (z podatkiem)", num: true, fmt: (v) => spct(v, 0), cls: (v, r) => (v > 0 && PICKABLE.has(r.stat) ? "c-win" : v > 0 ? "c-warn" : "c-muted") },
      { f: "oUnder", h: "Poniżej: kurs", num: true, render: (td, r) => { td.textContent = r.oUnder ? dec(r.oUnder) + " " : "—"; if (r.bUnder) td.append(el("span", "c-muted", r.bUnder)); } },
      { f: "evU", h: "Zysk (z podatkiem)", num: true, fmt: (v) => spct(v, 0), cls: (v, r) => (v > 0 && PICKABLE.has(r.stat) ? "c-win" : v > 0 ? "c-warn" : "c-muted") },
      { f: "typ", h: "Typ dnia", render: (td, r) => { if (r.typ) td.append(el("span", "mk-chip c-win", r.typ)); } },
      { f: "_add", h: "", nosort: true, nw: true, render: (td, r) => {
        [["over", "powyżej", r.pO, r.oOver], ["under", "poniżej", 1 - r.pO, r.oUnder]].forEach(([side, word, p, odd]) => {
          const b = el("button", "mk-btn ghost sm", "+ " + (side === "over" ? "pow." : "pon."));
          b.title = "Dodaj do zakładów: " + word + " " + line(r.ln);
          b.type = "button";
          b.addEventListener("click", (ev) => { ev.stopPropagation(); draftBet({
            mecz: m.gosp + " – " + m.gosc, rozgr: ligaName(m.liga_id), data: dayKey(m.start), szansa: p, kurs: odd,
            opis: r.kto + ": " + NOUN[r.stat] + " " + word + " " + line(r.ln),
            nogi: [{ fixture_id: m.id, stat: r.stat, scope: r.scope, line: r.ln, side }] }); });
          td.append(b);
        });
      } },
    ], "key");
    if (!rowsK.length) $("#mz-kursy").replaceChildren(el("caption", "c-muted", "Brak kursów na statystyki dla tego meczu (pobieram je na 3 dni przed meczem)."));
  }
  function renderTeams(m) {
    const box = $("#mz-teams");
    box.replaceChildren();
    [[m.gosp_id, m.gosp], [m.gosc_id, m.gosc]].forEach(([id, name]) => {
      const t = teamOf(id);
      const c = el("div", "mk-card");
      c.append(el("h2", null, name));
      if (!t) { c.append(el("p", "mk-note", "Brak danych o drużynie.")); box.append(c); return; }
      c.append(el("p", "mk-note", ligaName(t.liga_id) + (t.mecze_w_sezonie ? " · mecze ligowe w tym sezonie: " + t.mecze_w_sezonie : "")));
      const seasonRows = MAIN_STATS.filter((s) => t.sezon[s]).map((s) => ({ key: s, stat: STAT[s], za: t.sezon[s][0], przeciw: t.sezon[s][1],
        mz: t.miejsce[s] ? t.miejsce[s][0] : null, mp: t.miejsce[s] ? t.miejsce[s][1] : null, n: t.miejsce[s] ? t.miejsce[s][2] : null }));
      const w1 = el("div", "mk-wrap short");
      const t1 = el("table", "mk-tbl");
      w1.append(t1);
      c.append(el("h3", null, "Sezon w lidze: średnio na mecz"), w1);
      table(t1, seasonRows, [
        { f: "stat", h: "Statystyka", nw: true },
        { f: "za", h: "Sami", num: true, fmt: d1 },
        { f: "mz", h: "Miejsce", num: true, fmt: (v, r) => (v ? v + "/" + r.n : "—") },
        { f: "przeciw", h: "Rywale", num: true, fmt: d1 },
        { f: "mp", h: "Miejsce", num: true, fmt: (v, r) => (v ? v + "/" + r.n : "—") },
      ], "key", { nosortAll: true });
      c.append(el("p", "mk-note", "Miejsce 1 = najwięcej w lidze (dla „Rywale”: rywale tej drużyny mają tego najwięcej)."));
      const w2 = el("div", "mk-wrap short");
      const t2 = el("table", "mk-tbl");
      w2.append(t2);
      c.append(el("h3", null, "Ostatnie 5 meczów (sami – rywale)"), w2);
      table(t2, (t.ost5 || []).map((g, i) => Object.assign({ key: i }, g)), [
        { f: "data", h: "Data", nw: true, fmt: (v) => (v ? v.split("-").reverse().slice(0, 2).join(".") : "—") },
        { f: "rywal", h: "Rywal", render: (td, r) => { td.textContent = r.rywal + " "; td.append(el("span", "c-muted", (r.dom ? "(d) " : "(w) ") + r.rozgr)); } },
        ...["shots", "sot", "corners", "fouls", "cards"].map((s) => ({ f: s, h: STAT[s], num: true, nosort: true,
          fmt: (v, r) => (r.s && r.s[s] ? int(r.s[s][0]) + "–" + int(r.s[s][1]) : "—") })),
      ], "key", { nosortAll: true });
      box.append(c);
    });
  }

  // ------------------------------------------------------------------ Typy dnia
  function renderTypyDnia() {
    const all = st("typy");
    if (!all) { $("#td-top").replaceChildren(el("div", "mk-card", "Wczytywanie…")); return; }
    const keys = Object.keys(all).sort();
    const today = dayKey(new Date().toISOString());
    if (!S.td || !keys.includes(S.td)) S.td = keys.find((k) => k >= today) || keys[keys.length - 1];
    $("#td-days").replaceChildren(...keys.map((k) => chip(dayLabel(k), k === S.td, () => { S.td = k; render(); }, (all[k].top || []).length)));
    const d = all[S.td] || { top: [], kupony2: [], kupony3: [], kandydaci: [] };
    const box = $("#td-top");
    box.replaceChildren();
    if (!d.top.length) box.append(el("div", "mk-card", "Na ten dzień model nie znalazł linii wyraźnie lepszych od rynku."));
    d.top.forEach((p, i) => {
      const m = matchById(p.fixture_id);
      const c = el("div", "mk-pick");
      const hd = el("div", "hd");
      const ti = el("span", "ti", (i + 1) + ". " + p.opis);
      hd.append(ti);
      if (p.wynik) hd.append(el("span", "mk-chip " + (p.wynik === "wygrana" ? "c-win" : p.wynik === "przegrana" ? "c-loss" : "c-muted"), p.wynik + (p.wartosc !== null && p.wartosc !== undefined ? " (" + int(p.wartosc) + ")" : "")));
      c.append(hd);
      const sub = el("div", "mk-facts");
      const fact = (label, value, cls) => { const s = el("span"); s.append(label + " ", el("b", cls || null, value)); sub.append(s); };
      fact("", p.mecz + (m ? ", " + day(m.start) + " " + hm(m.start) : ""));
      fact("Model", pct(p.p_model, 0));
      fact("Rynek", pct(p.p_rynek, 0));
      fact("Kurs", dec(p.kurs) + " (" + p.bukmacher + ")");
      fact("Min. kurs z podatkiem", dec(p.kurs_min_z_podatkiem), "c-win");
      fact("Zysk z podatkiem przy tym kursie", spct(p.ev_podatek, 0), sign(p.ev_podatek));
      c.append(sub);
      const row = el("div", "mk-row");
      const add = el("button", "mk-btn ghost", "Dodaj do zakładów");
      add.type = "button";
      add.addEventListener("click", () => draftBet({ mecz: p.mecz, rozgr: m ? ligaName(m.liga_id) : "", data: m ? dayKey(m.start) : S.td,
        opis: p.opis, kurs: p.kurs, szansa: p.p_model, nogi: [{ fixture_id: p.fixture_id, stat: p.stat, scope: p.scope, line: p.line, side: p.side }] }));
      row.append(add);
      if (m) {
        const open = el("button", "mk-btn ghost", "Analiza meczu");
        open.type = "button";
        open.addEventListener("click", () => openMatch(m.id));
        row.append(open);
      }
      c.append(row);
      box.append(c);
    });
    const kb = $("#td-kupony");
    kb.replaceChildren();
    const coupons = [...(d.kupony2 || []), ...(d.kupony3 || [])];
    if (!coupons.length) kb.append(el("p", "mk-note", "Brak kombinacji o łącznym kursie 1,8–2,5 z kandydatów tego dnia."));
    coupons.slice(0, 6).forEach((k) => {
      const c = el("div", "mk-pick");
      const hd = el("div", "hd");
      hd.append(el("span", "ti", "Kupon " + k.nogi.length + " nogi · kurs " + dec(k.kurs)), el("span", "mk-chip", "szansa " + pct(k.p_model, 0)));
      c.append(hd);
      const ul = el("ul", "mk-why");
      k.nogi.forEach((n) => ul.append(el("li", null, n)));
      c.append(ul);
      const f = el("div", "mk-facts");
      const s1 = el("span"); s1.append("Zysk z podatkiem ", el("b", sign(k.ev_podatek), spct(k.ev_podatek, 0)));
      const s2 = el("span"); s2.append("Min. łączny kurs ", el("b", "c-win", dec(minOdds(k.p_model))));
      f.append(s1, s2);
      c.append(f);
      const add = el("button", "mk-btn ghost", "Dodaj do zakładów");
      add.type = "button";
      add.addEventListener("click", () => draftBet({ rodzaj: "Kupon " + k.nogi.length, opis: k.nogi.join("\n"), kurs: k.kurs, szansa: k.p_model, data: S.td }));
      c.append(add);
      kb.append(c);
    });
    table($("#td-kand"), (d.kandydaci || []).map((p, i) => Object.assign({ key: i }, p)), [
      { f: "mecz", h: "Mecz", wide: true },
      { f: "opis", h: "Zdarzenie", wide: true },
      { f: "p_model", h: "Model", num: true, fmt: (v) => pct(v, 0) },
      { f: "p_rynek", h: "Rynek", num: true, fmt: (v) => pct(v, 0) },
      { f: "kurs", h: "Kurs", num: true, fmt: dec },
      { f: "bukmacher", h: "U kogo", nw: true },
      { f: "kurs_min_z_podatkiem", h: "Min. kurs", num: true, fmt: dec },
      { f: "ev_podatek", h: "Zysk z podatkiem", num: true, fmt: (v) => spct(v, 0), cls: sign },
    ], "key", { sort: { f: "ev_podatek", dir: "desc" } });
  }

  // ------------------------------------------------------------------ Szukaj
  function renderSzukaj() {
    if (!st("mecze")) return;
    fillLigaSelect($("#sz-liga"), S.sz.liga);
    const ss = $("#sz-stat");
    if (!ss.options.length) ss.replaceChildren(...MAIN_STATS.map((s) => new Option(STAT[s], s)));
    ss.value = S.sz.stat;
    $("#sz-kto").value = S.sz.kto;
    $("#sz-typ").value = S.sz.typ;
    $("#sz-dni").value = String(S.sz.dni);
    const now = Date.now();
    const lastDay = dayKey(new Date(now + (S.sz.dni - 1) * 864e5).toISOString());
    const today = dayKey(new Date(now).toISOString());
    const pmin = Math.min(0.99, Math.max(0.01, (parseFloat($("#sz-p").value) || 70) / 100));
    const kFixed = parseInt($("#sz-k").value, 10);
    const res = [];
    matches().forEach((m) => {
      const dk = dayKey(m.start);
      if (dk < today || dk > lastDay || !inLigaFilter(m, S.sz.liga)) return;
      if (S.sz.kursy && !(m.kursy && m.kursy.length)) return;
      if (m.status && !["NS", "TBD"].includes(m.status)) return;
      const scopes = S.sz.kto === "any" ? ["home", "away"] : [S.sz.kto];
      scopes.forEach((scope) => {
        const mu = meanOf(m, S.sz.stat, scope);
        if (mu === null) return;
        const a = alphaOf(S.sz.stat, scope);
        let k = null, p = null;
        if (Number.isFinite(kFixed)) {
          k = kFixed;
          p = S.sz.typ === "min" ? atLeast(mu, a, k) : atMost(mu, a, k);
        } else if (S.sz.typ === "min") {
          const t = threshold(mu, a, pmin);
          if (t) { k = t.k; p = t.p; }
        } else {
          for (let x = 0; x < 80; x++) { const q = atMost(mu, a, x); if (q >= pmin) { k = x; p = q; break; } }
        }
        if (k === null || p < pmin) return;
        const who = scope === "home" ? m.gosp : scope === "away" ? m.gosc : "łącznie";
        const ln = S.sz.typ === "min" ? k - 0.5 : k + 0.5;
        const offer = (m.kursy || []).find((x) => x[0] === S.sz.stat && x[1] === scope && x[2] === ln);
        const odd = offer ? (S.sz.typ === "min" ? offer[3] : offer[5]) : null;
        res.push({ key: m.id + scope, m, start: m.start, mecz: m.gosp + " – " + m.gosc, liga: ligaShort(m.liga_id),
          opis: who + ": " + (S.sz.typ === "min" ? "co najmniej " : "co najwyżej ") + k + " " + NOUN[S.sz.stat],
          mu, p, fair: fairOdds(p), min: minOdds(p), odd, bm: offer ? (S.sz.typ === "min" ? offer[4] : offer[6]) : null,
          ev: odd ? p * odd * TAX - 1 : null, scope, ln, k });
      });
    });
    $("#sz-count").textContent = "Wyniki: " + int(res.length);
    table($("#sz-tbl"), res, [
      { f: "start", h: "Kiedy", nw: true, fmt: when },
      { f: "liga", h: "Rozgr.", nw: true },
      { f: "mecz", h: "Mecz", wide: true },
      { f: "opis", h: "Zdarzenie", wide: true },
      { f: "mu", h: "Średnio", num: true, fmt: d1 },
      { f: "p", h: "Szansa", num: true, fmt: (v) => pct(v, 0) },
      { f: "fair", h: "Kurs uczciwy", num: true, fmt: dec },
      { f: "min", h: "Min. kurs z podatkiem", num: true, fmt: dec, cls: () => "c-win" },
      { f: "odd", h: "Kurs w serwisie", num: true, render: (td, r) => { td.textContent = r.odd ? dec(r.odd) + " " : "—"; if (r.bm) td.append(el("span", "c-muted", r.bm)); } },
      { f: "ev", h: "Zysk z podatkiem", num: true, fmt: (v) => spct(v, 0), cls: sign },
      { f: "_a", h: "", nosort: true, render: (td, r) => {
        const b = el("button", "mk-btn ghost sm", "Analiza");
        b.type = "button";
        b.addEventListener("click", (ev) => { ev.stopPropagation(); openMatch(r.m.id); });
        const add = el("button", "mk-btn ghost sm", "+ zakład");
        add.type = "button";
        add.addEventListener("click", (ev) => { ev.stopPropagation(); draftBet({ mecz: r.mecz, rozgr: ligaName(r.m.liga_id), data: dayKey(r.start),
          opis: r.opis, kurs: r.odd, szansa: r.p, nogi: [{ fixture_id: r.m.id, stat: S.sz.stat, scope: r.scope, line: r.ln, side: S.sz.typ === "min" ? "over" : "under" }] }); });
        td.append(b, add);
      } },
    ], "key", { sort: { f: "p", dir: "desc" } });
  }
  [["#sz-liga", "liga"], ["#sz-stat", "stat"], ["#sz-kto", "kto"], ["#sz-typ", "typ"]].forEach(([sel, k]) =>
    $(sel).addEventListener("change", (e) => { S.sz[k] = e.target.value; render(); }));
  $("#sz-dni").addEventListener("change", (e) => { S.sz.dni = parseInt(e.target.value, 10) || 3; render(); });
  ["#sz-k", "#sz-p"].forEach((s) => $(s).addEventListener("input", () => render()));
  $("#sz-kursy").addEventListener("change", (e) => { S.sz.kursy = e.target.checked; render(); });

  // ------------------------------------------------------------------ Moje zakłady
  const ZK_FIELDS = ["data", "mecz", "rozgr", "rodzaj", "opis", "buk", "kurs", "stawka", "tryb", "szansa", "wynik", "notatki"];
  function draftBet(d) {
    S.zkEdit = null;
    S.zkDraft = Object.assign({ rodzaj: "Singiel", tryb: "Realny", stawka: 10, data: dayKey(new Date().toISOString()) }, d);
    fillBetForm(S.zkDraft);
    show("zaklady", true);
    setTimeout(() => { const f = $("#zk-kurs"); if (f) f.focus(); }, 50);
  }
  function fillBetForm(d) {
    $("#zk-form-title").textContent = S.zkEdit ? "Edytuj zakład" : "Dodaj zakład";
    $("#zk-data").value = d.data || "";
    $("#zk-mecz").value = d.mecz || "";
    $("#zk-rozgr").value = d.rozgr || "";
    $("#zk-rodzaj").value = d.rodzaj || "Singiel";
    $("#zk-opis").value = d.opis || "";
    $("#zk-buk").value = d.buk || "";
    $("#zk-kurs").value = d.kurs ? Number(d.kurs).toFixed(2) : "";
    $("#zk-stawka").value = d.stawka || 10;
    $("#zk-tryb").value = d.tryb || "Realny";
    $("#zk-szansa").value = d.szansa ? Math.round(d.szansa * 100) : "";
    $("#zk-wynik").value = d.wynik || "";
    $("#zk-notatki").value = d.notatki || "";
    const hint = $("#zk-hint");
    hint.textContent = d.szansa ? "Według modelu szansa " + pct(d.szansa, 0) + ": zakład opłaca się po podatku od kursu " + dec(minOdds(d.szansa)) + ". Wpisz kurs ze swojego bukmachera." : "";
  }
  function betPayout(b) {
    const s = Number(b.stawka) || 0, k = Number(b.kurs) || 0;
    if (b.wynik === "W") { let pay = s * TAX * k; if (pay > 2280) pay *= 0.9; return pay; }
    if (b.wynik === "Z") return s;
    if (b.wynik === "P") return 0;
    return null;
  }
  function suggestResult(b) {
    if (b.wynik || !b.nogi || !b.nogi.length) return null;
    const res = st("rozliczenia") || {};
    const out = b.nogi.map((n) => {
      const r = res[String(n.fixture_id)];
      if (!r || !r.s[n.stat] || r.s[n.stat][0] === null) return null;
      const v = n.scope === "home" ? r.s[n.stat][0] : n.scope === "away" ? r.s[n.stat][1] : r.s[n.stat][0] + r.s[n.stat][1];
      if (v === n.line) return { r: "Z", v };
      return { r: (v > n.line) === (n.side === "over") ? "W" : "P", v };
    });
    if (out.some((x) => x && x.r === "P")) return { r: "P", v: out.map((x) => (x ? x.v : "?")) };
    if (out.some((x) => x === null)) return null;
    return { r: out.every((x) => x.r === "Z") ? "Z" : "W", v: out.map((x) => x.v) };
  }
  function renderZaklady() {
    const all = S.ov.zaklady || [];
    const f = $("#zk-filtr").value;
    const list = all.filter((b) => !f || (f === "open" ? !b.wynik : b.tryb === f));
    const sum = $("#zk-sum");
    sum.replaceChildren();
    ["Realny", "Papier"].forEach((tryb) => {
      const bs = all.filter((b) => b.tryb === tryb);
      if (!bs.length) return;
      const done = bs.filter((b) => b.wynik);
      const staked = done.reduce((a, b) => a + (Number(b.stawka) || 0), 0);
      const paid = done.reduce((a, b) => a + (betPayout(b) || 0), 0);
      const prof = paid - staked;
      const s = el("span");
      s.append((tryb === "Realny" ? "Realne" : "Na papierze") + ": " + bs.length + " zakł., rozliczone " + done.length + ", zysk ",
        el("b", sign(prof), zl(prof)), done.length ? " (ROI " + spct(staked ? prof / staked : 0) + ")" : "");
      sum.append(s);
    });
    if (!all.length) sum.append(el("span", "c-muted", "Brak zakładów. Dodaj pierwszy z formularza powyżej albo przyciskiem „Dodaj do zakładów” przy typie lub meczu."));
    resolveNames(all.map((b) => b.by));
    table($("#zk-tbl"), list.map((b) => Object.assign({ key: b.id, wyplata: betPayout(b), zysk: betPayout(b) === null ? null : betPayout(b) - (Number(b.stawka) || 0), prop: suggestResult(b) }, b)), [
      { f: "data", h: "Data", nw: true, fmt: (v) => (v ? v.split("-").reverse().join(".") : "—") },
      { f: "mecz", h: "Mecz", wide: true, render: (td, r) => { td.textContent = r.mecz || "—"; if (r.rozgr) td.append(el("div", "c-muted", r.rozgr)); } },
      { f: "opis", h: "Zakład", wide: true, render: (td, r) => { td.textContent = r.opis || "—"; td.style.whiteSpace = "pre-line"; if (r.rodzaj && r.rodzaj !== "Singiel") td.prepend(el("span", "mk-chip", r.rodzaj), " "); } },
      { f: "kurs", h: "Kurs", num: true, fmt: dec },
      { f: "stawka", h: "Stawka", num: true, fmt: (v) => (empty(v) ? "—" : NF2.format(v) + " zł") },
      { f: "tryb", h: "Tryb", fmt: (v) => (v === "Papier" ? "papier" : "realny") },
      { f: "wynik", h: "Wynik", render: (td, r) => {
        if (r.wynik) td.append(el("span", "mk-chip " + ({ W: "c-win", P: "c-loss", Z: "c-muted" }[r.wynik]), { W: "wygrana", P: "przegrana", Z: "zwrot" }[r.wynik]));
        else if (r.prop) {
          const b = el("button", "mk-btn ghost sm", "Wg danych: " + { W: "wygrana", P: "przegrana", Z: "zwrot" }[r.prop.r] + " (" + r.prop.v.join(", ") + ") – zatwierdź");
          b.type = "button";
          b.disabled = !S.canWrite;
          const msg = el("span");
          b.addEventListener("click", (ev) => { ev.stopPropagation(); save((db) => db.collection("zaklady").doc(r.id).update({ wynik: r.prop.r, at: new Date().toISOString() }), msg); });
          td.append(b, msg);
        } else td.append(el("span", "c-muted", "czeka"));
      } },
      { f: "zysk", h: "Zysk", num: true, fmt: zl, cls: sign },
      { f: "buk", h: "Bukmacher", nw: true },
      { f: "_x", h: "", nosort: true, render: (td, r) => {
        const e = el("button", "mk-btn ghost sm", "Edytuj");
        e.type = "button";
        e.disabled = !S.canWrite;
        e.addEventListener("click", (ev) => { ev.stopPropagation(); S.zkEdit = r.id; fillBetForm(r); window.scrollTo({ top: 0, behavior: "smooth" }); });
        const d = el("button", "mk-btn danger sm", "Usuń");
        d.type = "button";
        d.disabled = !S.canWrite;
        const m = el("span");
        d.addEventListener("click", (ev) => { ev.stopPropagation(); if (window.confirm("Usunąć ten zakład?")) save((db) => db.collection("zaklady").doc(r.id).delete(), m); });
        td.append(e, d, m);
      } },
    ], "key", { sort: { f: "data", dir: "desc" } });
    ["#zk-save", "#zk-clear"].forEach((s) => { $(s).disabled = !S.canWrite; });
  }
  $("#zk-filtr").addEventListener("change", () => render());
  $("#zk-clear").addEventListener("click", () => { S.zkEdit = null; S.zkDraft = null; fillBetForm({ data: dayKey(new Date().toISOString()), stawka: 10 }); $("#zk-msg").textContent = ""; });
  $("#zk-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const msg = $("#zk-msg");
    const num = (s) => { const v = parseFloat($(s).value); return Number.isFinite(v) ? v : null; };
    const d = {
      data: $("#zk-data").value, mecz: $("#zk-mecz").value.trim(), rozgr: $("#zk-rozgr").value.trim(), rodzaj: $("#zk-rodzaj").value,
      opis: $("#zk-opis").value.trim(), buk: $("#zk-buk").value.trim(), kurs: num("#zk-kurs"), stawka: num("#zk-stawka"),
      tryb: $("#zk-tryb").value, szansa: num("#zk-szansa") !== null ? num("#zk-szansa") / 100 : null, wynik: $("#zk-wynik").value,
      notatki: $("#zk-notatki").value.trim(),
    };
    if (!d.opis) { msg.className = "mk-err"; msg.textContent = "Opisz zakład."; return; }
    if (!(d.kurs > 1)) { msg.className = "mk-err"; msg.textContent = "Podaj kurs (większy niż 1)."; return; }
    if (!(d.stawka > 0)) { msg.className = "mk-err"; msg.textContent = "Podaj stawkę."; return; }
    const prev = S.zkEdit ? (S.ov.zaklady || []).find((b) => b.id === S.zkEdit) : null;
    const nogi = prev ? prev.nogi || [] : (S.zkDraft && S.zkDraft.nogi) || [];
    const body = Object.assign(d, { nogi }, stamp(), prev ? { by: prev.by, at: prev.at, zmieniono: new Date().toISOString() } : {});
    const ref = (db) => (S.zkEdit ? db.collection("zaklady").doc(S.zkEdit).set(body) : db.collection("zaklady").add(body));
    save(ref, msg).then((ok) => { if (ok) { S.zkEdit = null; S.zkDraft = null; fillBetForm({ data: dayKey(new Date().toISOString()), stawka: 10 }); } });
  });
  function csvCell(v) {
    if (v === null || v === undefined) return "";
    const s = typeof v === "number" ? String(v).replace(".", ",") : String(v);
    return /[;"\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }
  $("#zk-csv").addEventListener("click", async () => {
    const msg = $("#zk-csv-msg");
    const list = [...(S.ov.zaklady || [])].sort((a, b) => (a.data || "").localeCompare(b.data || ""));
    if (!list.length) { msg.className = "mk-err"; msg.textContent = "Brak zakładów do pobrania."; return; }
    const head = ["Lp", "Data", "Godzina", "Rozgrywki", "Mecz", "Rodzaj", "Zakład", "Bukmacher", "Kurs", "Stawka (zł)", "Podatek 12% (zł)",
      "Stawka po podatku (zł)", "Wynik", "Wypłata (zł)", "Podatek od wygranej (zł)", "Zysk (zł)", "Saldo (zł)", "Szansa wg modelu (%)",
      "Min. kurs wg modelu", "Źródło", "Tryb", "Notatki"];
    let saldo = 0;
    const lines = [head.join(";")];
    list.forEach((b, i) => {
      const s = Number(b.stawka) || 0, k = Number(b.kurs) || 0;
      const gross = b.wynik === "W" ? s * TAX * k : null;
      const wTax = gross !== null && gross > 2280 ? gross * 0.1 : (b.wynik === "W" ? 0 : null);
      const pay = betPayout(b);
      const prof = pay === null ? null : pay - s;
      if (prof !== null) saldo += prof;
      lines.push([i + 1, b.data || "", "", b.rozgr || "", b.mecz || "", b.rodzaj || "", (b.opis || "").replace(/\n/g, " + "), b.buk || "",
        k || "", s || "", +(s * 0.12).toFixed(2), +(s * TAX).toFixed(2), b.wynik || "", pay === null ? "" : +pay.toFixed(2),
        wTax === null ? "" : +wTax.toFixed(2), prof === null ? "" : +prof.toFixed(2), prof === null ? "" : +saldo.toFixed(2),
        b.szansa ? Math.round(b.szansa * 100) : "", b.szansa ? +minOdds(b.szansa).toFixed(2) : "", b.nogi && b.nogi.length ? "Model" : "Własny",
        b.tryb || "", b.notatki || ""].map(csvCell).join(";"));
    });
    const data = "﻿" + lines.join("\r\n");
    const dl = S.downloads;
    if (!dl) { msg.className = "mk-err"; msg.textContent = "Pobieranie nie jest dostępne w tym widoku."; return; }
    try {
      await dl.save({ filename: "zaklady-mk2-" + dayKey(new Date().toISOString()) + ".csv", data });
      msg.className = "mk-saved"; msg.textContent = "Gotowe";
    } catch (e) {
      msg.className = e && e.code === "declined" ? "c-muted" : "mk-err";
      msg.textContent = e && e.code === "declined" ? "Anulowano." : "Nie udało się pobrać (" + ((e && e.code) || "błąd") + ").";
    }
  });

  // ------------------------------------------------------------------ Skuteczność
  function renderSkutecznosc() {
    const w = st("wyniki");
    if (!w) return;
    const s = w.podsumowanie || {};
    const sum = $("#sk-sum");
    sum.replaceChildren();
    const add = (label, value, cls) => { const x = el("span"); x.append(label + " ", el("b", cls || null, value)); sum.append(x); };
    add("Typów:", int(s.typy));
    add("Rozliczone:", int(s.rozliczone));
    add("Trafione:", int(s.wygrane) + " z " + int(s.rozliczone));
    add("Zysk na stawkę 1:", money(s.zysk), sign(s.zysk));
    add("Z podatkiem 12%:", money(s.zysk_podatek), sign(s.zysk_podatek));
    table($("#sk-tbl"), (w.typy || []).map((p, i) => Object.assign({ key: i }, p)), [
      { f: "day", h: "Dzień", nw: true, fmt: (v) => (v ? v.split("-").reverse().join(".") : "—") },
      { f: "fixture_id", h: "Mecz", wide: true, fmt: (v) => { const m = matchById(v) || (st("rozliczenia") || {})[String(v)]; return m ? m.gosp + " – " + m.gosc : String(v); } },
      { f: "stat", h: "Zdarzenie", wide: true, fmt: (v, r) => SCOPE_PL[r.scope] + ": " + NOUN[r.stat] + " " + (r.side === "over" ? "powyżej " : "poniżej ") + line(r.line) },
      { f: "p_model", h: "Model", num: true, fmt: (v) => pct(v, 0) },
      { f: "p_market", h: "Rynek", num: true, fmt: (v) => pct(v, 0) },
      { f: "odd", h: "Kurs", num: true, fmt: dec },
      { f: "wartosc", h: "Padło", num: true, fmt: int },
      { f: "wynik", h: "Wynik", chip: true, fmt: (v) => v || "czeka", cls: (v) => (v === "wygrana" ? "c-win" : v === "przegrana" ? "c-loss" : "c-muted") },
      { f: "zysk_podatek", h: "Zysk z podatkiem", num: true, fmt: money, cls: sign },
    ], "key", { sort: { f: "day", dir: "desc" } });
    const pr = w.prognozy || {};
    table($("#sk-prog"), Object.keys(pr).filter((s) => STAT[s]).map((s) => Object.assign({ key: s, statL: STAT[s] }, pr[s])), [
      { f: "statL", h: "Statystyka", nw: true },
      { f: "n", h: "Drużyny × mecze", num: true, fmt: int },
      { f: "srednia_prognoza", h: "Prognoza średnio", num: true, fmt: dec },
      { f: "srednia_fakt", h: "Padło średnio", num: true, fmt: dec },
      { f: "blad_sredni", h: "Średni błąd", num: true, fmt: dec },
      { f: "progi_70_proc", h: "Progi 70%: trafione", num: true, fmt: (v, r) => (empty(v) ? "—" : pct(v, 0) + " (" + r.progi_70_trafione + "/" + r.progi_70 + ")"),
        cls: (v) => (empty(v) ? "" : v >= 0.62 ? "c-win" : "c-loss") },
    ], "key", { nosortAll: true });
    if (!Object.keys(pr).length) $("#sk-prog").replaceChildren(el("caption", "c-muted", "Pierwsze porównania pojawią się po rozegraniu meczów, dla których model zapisał prognozy."));
  }

  // ------------------------------------------------------------------ Prośby
  function renderProsby() {
    resolveNames(S.ov.prosby.map((x) => x.by));
    ["#pr-tekst", "#pr-add"].forEach((s) => { $(s).disabled = !S.canWrite; });
    table($("#tb-prosby"), S.ov.prosby, [
      { f: "at", h: "Kiedy", fmt: when, nw: true },
      { f: "by", h: "Kto", fmt: (v) => nameOf(v) },
      { f: "tekst", h: "Prośba", wide: true },
      { f: "status", h: "Stan", chip: true, cls: (v) => ({ nowa: "c-warn", "w toku": "c-warn", zrobione: "c-win", odrzucona: "c-loss" }[v] || "") },
      { f: "odpowiedz", h: "Odpowiedź Claude", wide: true },
    ], "id", { sort: { f: "at", dir: "desc" } });
  }

