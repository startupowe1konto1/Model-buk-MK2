"""Build index.html of the MK2 app: the scanner app plus the statistics sections (one-off restructuring).

Reads src/base_scanner.html (the scanner app), applies exact replacements (each must match once) and inserts
src/stats_sections.html and src/stats_app.js.  Run from the app folder.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
page = (HERE / "base_scanner.html").read_text(encoding="utf-8")  # the scanner app before the statistics sections
sections = (HERE / "stats_sections.html").read_text(encoding="utf-8")
js = (HERE / "stats_app.js").read_text(encoding="utf-8")


def rep(old: str, new: str) -> None:
    global page
    n = page.count(old)
    assert n == 1, (n, old[:120])
    page = page.replace(old, new)


# ---------------------------------------------------------------- CSS
rep("""@media (prefers-reduced-motion: no-preference) { .mk-page { animation: mkin 180ms ease-out; } @keyframes mkin { from { opacity: 0.6; } to { opacity: 1; } } }
</style>""", """@media (prefers-reduced-motion: no-preference) { .mk-page { animation: mkin 180ms ease-out; } @keyframes mkin { from { opacity: 0.6; } to { opacity: 1; } } }
/* statistics sections */
.mk-subnav { display: grid; gap: 6px; padding-top: 2px; border-top: 1px solid var(--line); }
.mk-tab.sm { font-size: 0.88rem; padding: 6px 9px 5px; }
.mk-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.mk-chipbtn { font: 600 0.85rem var(--body); border: 1px solid var(--line); background: var(--panel); color: var(--ink); border-radius: 999px; padding: 4px 11px; cursor: pointer; }
.mk-chipbtn[aria-pressed="true"] { background: var(--pitch); border-color: var(--pitch); color: var(--panel); }
.mk-chipbtn .n { opacity: 0.75; font-weight: 400; margin-left: 5px; }
.mk-chipbtn:focus-visible, .mk-match:focus-visible { outline: 2px solid var(--pitch); outline-offset: 2px; }
.mk-group { gap: 6px; }
.mk-group h3 { margin: 0 0 2px; font: 600 1rem var(--display); color: var(--muted); letter-spacing: 0.02em; }
.mk-match { display: grid; grid-template-columns: 3.2rem minmax(0, 1fr) auto; gap: 4px 12px; align-items: center; padding: 9px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--paper); cursor: pointer; text-align: left; font: inherit; color: inherit; width: 100%; }
.mk-match:hover { border-color: var(--pitch); }
.mk-match .tm { font-variant-numeric: tabular-nums; color: var(--muted); font-size: 0.88rem; }
.mk-match .tt { font-weight: 600; }
.mk-match .st { color: var(--muted); font-size: 0.82rem; font-variant-numeric: tabular-nums; }
.mk-match .bd { display: flex; gap: 4px; flex-wrap: wrap; justify-content: flex-end; }
@media (max-width: 560px) { .mk-match { grid-template-columns: 2.8rem minmax(0, 1fr); } .mk-match .bd { grid-column: 2; justify-content: flex-start; } .mk-match .st { display: block; } }
.mk-match-title { font-size: 1.5rem !important; }
.mk-vs { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 10.5rem), 1fr)); gap: 10px; }
.mk-stat { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; display: grid; gap: 6px; background: var(--panel); align-content: start; }
.mk-stat .l { color: var(--muted); font-size: 0.8rem; }
.mk-stat .v { display: flex; justify-content: space-between; font: 700 1.45rem/1 var(--display); font-variant-numeric: tabular-nums; }
.mk-stat .s { color: var(--muted); font-size: 0.8rem; font-variant-numeric: tabular-nums; }
.mk-stat .s b { color: var(--ink); }
.mk-bar { display: flex; height: 6px; border-radius: 3px; overflow: hidden; background: var(--chip); }
.mk-bar .h { background: var(--pitch); }
.mk-bar .a { background: var(--muted); opacity: 0.55; }
.mk-calc { display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: center; }
.mk-calc-out { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 15rem), 1fr)); gap: 10px; }
.mk-big { font: 700 1.7rem/1.1 var(--display); font-variant-numeric: tabular-nums; }
.mk-why { margin: 0; padding-left: 1.1rem; display: grid; gap: 4px; }
.mk-pick { border: 1px solid var(--line); border-left: 4px solid var(--pitch); border-radius: 8px; padding: 10px 12px; display: grid; gap: 8px; background: var(--panel); }
.mk-pick .hd { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; align-items: baseline; }
.mk-pick .ti { font-weight: 600; }
.mk-back { justify-self: start; }
.mk-btn.sm { padding: 2px 8px; font-size: 0.8rem; margin: 1px 4px 1px 0; }
.mk-check { display: flex !important; grid-auto-flow: column; gap: 6px; align-items: center; }
.mk-tbl caption { caption-side: bottom; text-align: left; padding: 10px; }
</style>""")

# ---------------------------------------------------------------- header and navigation
start = page.index('<header class="mk-top">')
end = page.index("</header>") + len("</header>")
page = page[:start] + """<header class="mk-top">
  <div class="mk-top-in">
    <div class="mk-brand">
      <h1>Model Buk MK2</h1>
      <span class="mk-fresh" id="fresh">Wczytywanie danych…</span>
    </div>
    <nav class="mk-tabs" role="tablist" aria-label="Sekcje">
      <button class="mk-tab" role="tab" id="t-mecze" data-p="mecze" aria-selected="true">Mecze</button>
      <button class="mk-tab" role="tab" id="t-typydnia" data-p="typydnia">Typy dnia</button>
      <button class="mk-tab" role="tab" id="t-szukaj" data-p="szukaj">Szukaj</button>
      <button class="mk-tab" role="tab" id="t-zaklady" data-p="zaklady">Moje zakłady<span class="n" id="n-zak">0</span></button>
      <button class="mk-tab" role="tab" id="t-skutecznosc" data-p="skutecznosc">Skuteczność</button>
      <button class="mk-tab" role="tab" id="t-plan" data-p="plan">Plan</button>
      <button class="mk-tab" role="tab" id="t-prosby" data-p="prosby">Prośby<span class="n" id="n-prosby">0</span></button>
      <button class="mk-tab" role="tab" id="t-skaner" data-p="skaner">Skaner Oskara</button>
    </nav>
    <div class="mk-subnav" id="subnav" hidden>
      <nav class="mk-tabs" role="tablist" aria-label="Skaner Oskara">
        <button class="mk-tab sm" role="tab" id="t-start" data-p="start">Podsumowanie</button>
        <button class="mk-tab sm" role="tab" id="t-kupony" data-p="kupony">Kupony</button>
        <button class="mk-tab sm" role="tab" id="t-typy" data-p="typy">Typy z kuponów<span class="n" id="n-typy">0</span></button>
        <button class="mk-tab sm" role="tab" id="t-single" data-p="single">Single</button>
        <button class="mk-tab sm" role="tab" id="t-przebiegi" data-p="przebiegi">Przebiegi</button>
        <button class="mk-tab sm" role="tab" id="t-eksperymenty" data-p="eksperymenty">Eksperymenty</button>
        <button class="mk-tab sm" role="tab" id="t-dane" data-p="dane">Dane</button>
        <button class="mk-tab sm" role="tab" id="t-ustawienia" data-p="ustawienia">Strategia skanera</button>
      </nav>
      <div class="mk-row">
        <label class="mk-label" for="f-src">Źródło
          <select class="mk-ctrl" id="f-src"><option value="">Wszystkie źródła</option></select>
        </label>
        <span class="mk-count" id="who"></span>
      </div>
    </div>
  </div>
</header>""" + page[end:]

# ---------------------------------------------------------------- sections
rep("""  <div class="mk-banner err" id="loaderr" hidden></div>
""", """  <div class="mk-banner err" id="loaderr" hidden></div>
""" + sections)
rep("""  <section class="mk-page" id="pg-start" role="tabpanel" aria-labelledby="t-start">""",
    """  <section class="mk-page" id="pg-start" role="tabpanel" aria-labelledby="t-start" hidden>""")
have_need = """    <div class="mk-cols">
      <div class="mk-card">
        <h2>Co już mamy</h2>
        <ul class="mk-list" id="have"></ul>
      </div>
      <div class="mk-card">
        <h2>Czego potrzebujemy</h2>
        <p class="mk-note">Najpierw to, co blokuje dalsze prace. Kliknij, żeby zmienić stan albo dopisać notatkę.</p>
        <ul class="mk-list" id="need"></ul>
      </div>
    </div>
"""
rep(have_need, "")
rep("""  <section class="mk-page" id="pg-plan" role="tabpanel" aria-labelledby="t-plan" hidden>
""", """  <section class="mk-page" id="pg-plan" role="tabpanel" aria-labelledby="t-plan" hidden>
""" + have_need)
rep("""    <div class="mk-card">
      <h2>Prośby do Claude</h2>
      <p class="mk-note">Napisz, co ma się zmienić albo co sprawdzić: nowa funkcja, test, poprawka. Claude odpowie tutaj i zmieni stan prośby.</p>
      <form class="mk-form" id="pr-form">
        <label for="pr-tekst" style="grid-column: 1 / -1">Prośba<textarea class="mk-ta" id="pr-tekst" placeholder="Np. dodaj filtr po lidze w kuponach"></textarea></label>
        <div class="mk-row"><button class="mk-btn" id="pr-add" type="submit">Wyślij prośbę</button><span id="pr-msg"></span></div>
      </form>
      <div class="mk-wrap short"><table class="mk-tbl" id="tb-prosby"></table></div>
    </div>
  </section>
</main>""", """  </section>
</main>""")
rep("""<h2>Ustawienia strategii do następnego testu</h2>""", """<h2>Ustawienia skanera Oskara do następnego testu</h2>""")

# ---------------------------------------------------------------- script
rep("""    data: {}, meta: null, page: "start", src: "",""", """    data: {}, meta: null, page: "mecze", src: "",
    mz: { day: "", liga: "main", q: "" }, mecz: null, calc: null, td: null, skaner: "start",
    sz: { liga: "main", stat: "shots", kto: "home", typ: "min", dni: 3, kursy: false },
    zkEdit: null, zkDraft: null, downloads: null,""")
rep("""    ov: { plan: {}, typy: {}, notatki: {}, prosby: [], profil: null },""",
    """    ov: { plan: {}, typy: {}, notatki: {}, prosby: [], profil: null, zaklady: [] },""")
rep("""  const PAGES = ["start", "kupony", "typy", "single", "przebiegi", "eksperymenty", "dane", "plan", "ustawienia"];""",
    """  const PAGES = ["mecze", "mecz", "typydnia", "szukaj", "zaklady", "skutecznosc", "plan", "prosby",
    "start", "kupony", "typy", "single", "przebiegi", "eksperymenty", "dane", "ustawienia"];
  const SKANER = ["start", "kupony", "typy", "single", "przebiegi", "eksperymenty", "dane", "ustawienia"];""")
rep("""  const FILES = ["meta", "podsumowanie", "eksperymenty", "przebiegi", "kupony", "kupony_nogi", "single", "wyniki", "dane", "plan"];""",
    """  const FILES = ["st_meta", "st_mecze", "st_druzyny", "st_typy", "st_wyniki", "st_rozliczenia",
    "meta", "podsumowanie", "eksperymenty", "przebiegi", "kupony", "kupony_nogi", "single", "wyniki", "dane", "plan"];""")
rep("""      if (f === "meta" || f === "podsumowanie") fillSources();""",
    """      if (f === "meta" || f === "podsumowanie" || f === "st_meta") fillSources();""")
rep("""    const m = S.meta;
    const f = $("#fresh");
    f.replaceChildren();
    if (m) {""", """    const m = S.meta;
    const f = $("#fresh");
    f.replaceChildren();
    const sm2 = S.data.st_meta;
    if (sm2) {
      f.append("Prognozy z ", el("b", null, when(sm2.utworzono)), " · ostatnie wyniki z ", el("b", null, day(sm2.dane_do)));
    } else if (m) {""")
rep("""    if (!cl || typeof cl.use !== "function") { readOnly("Ta kopia strony nie ma połączenia z bazą aplikacji: możesz przeglądać, ale zmiany nie zostaną zapisane."); return; }""",
    """    if (!cl || typeof cl.use !== "function") { readOnly("Ta kopia strony nie ma połączenia z bazą aplikacji: możesz przeglądać, ale zmiany nie zostaną zapisane."); return; }
    cl.use("downloads").then((d) => { S.downloads = d; }, () => { S.downloads = null; });""")
rep("""    db.doc("ustawienia/profil").onSnapshot""", """    db.collection("zaklady").orderBy("at", "desc").limit(1000).onSnapshot((s) => { S.ov.zaklady = s.docs.map((d) => Object.assign({ id: d.id }, d.data())); render(); }, err);
    db.doc("ustawienia/profil").onSnapshot""")
rep("""  function show(p, push) {
    if (!PAGES.includes(p)) p = "start";
    if (p !== S.page) window.scrollTo(0, 0);
    S.page = p;
    PAGES.forEach((id) => { $("#pg-" + id).hidden = id !== p; $("#t-" + id).setAttribute("aria-selected", id === p ? "true" : "false"); });
    if (push) { try { history.replaceState(null, "", "#" + p); } catch (e) { /* ignore */ } }
    render();
  }""", """  function show(p, push) {
    if (p === "skaner") p = S.skaner || "start";
    if (!PAGES.includes(p)) p = "mecze";
    if (p !== S.page) window.scrollTo(0, 0);
    S.page = p;
    if (SKANER.includes(p)) S.skaner = p;
    PAGES.forEach((id) => { $("#pg-" + id).hidden = id !== p; });
    const primary = SKANER.includes(p) ? "skaner" : p === "mecz" ? "mecze" : p;
    document.querySelectorAll(".mk-tab").forEach((b) => b.setAttribute("aria-selected", b.dataset.p === primary || b.dataset.p === p ? "true" : "false"));
    $("#subnav").hidden = !SKANER.includes(p);
    if (push) { try { history.replaceState(null, "", "#" + (p === "mecz" && S.mecz ? "mecz/" + S.mecz : p)); } catch (e) { /* ignore */ } }
    render();
  }""")
rep("""      if (c.nosort) th.textContent = c.h;""", """      if (c.nosort || opts.nosortAll) th.textContent = c.h;""")

old_have = """    const items = planItems();
    const have = $("#have"), need = $("#need");"""
i0 = page.index(old_have)
i1 = page.index("""    profitChart($("#ch-zysk"), bySrc(rows("eksperymenty")));""")
block = page[i0:i1]
page = page[:i0] + page[i1:]
rep("""  function renderKupony() {""", """  function renderHaveNeed() {
""" + block.rstrip() + """
  }

  function renderKupony() {""")
rep("""  function renderPlan() {""", """  function renderPlan() {
    renderHaveNeed();""")
rep("""    ["#st-kmin", "#st-kmax", "#st-nogi", "#st-noga", "#st-pod", "#st-ligi", "#st-uwagi", "#st-save", "#pr-tekst", "#pr-add"].forEach((s) => { $(s).disabled = !S.canWrite; });
    const pending = S.ov.prosby.filter((x) => x.status === "nowa" || x.status === "w toku").length;
    $("#n-prosby").textContent = String(pending);
    table($("#tb-prosby"), S.ov.prosby, [
      { f: "at", h: "Kiedy", fmt: when, nw: true },
      { f: "by", h: "Kto", fmt: (v) => nameOf(v) },
      { f: "tekst", h: "Prośba", wide: true },
      { f: "status", h: "Stan", chip: true, cls: (v) => ({ nowa: "c-warn", "w toku": "c-warn", zrobione: "c-win", odrzucona: "c-loss" }[v] || "") },
      { f: "odpowiedz", h: "Odpowiedź Claude", wide: true },
    ], "id", { sort: { f: "at", dir: "desc" } });
  }""", """    ["#st-kmin", "#st-kmax", "#st-nogi", "#st-noga", "#st-pod", "#st-ligi", "#st-uwagi", "#st-save"].forEach((s) => { $(s).disabled = !S.canWrite; });
  }""")
rep("""  // ------------------------------------------------------------------ render
  let pending = false;""", js + """  // ------------------------------------------------------------------ render
  let pending = false;""")
rep("""      $("#n-typy").textContent = String(Object.keys(S.ov.typy).length);""",
    """      $("#n-typy").textContent = String(Object.keys(S.ov.typy).length);
      $("#n-zak").textContent = String((S.ov.zaklady || []).filter((b) => !b.wynik).length);""")
rep("""      const fn = { start: renderStart,""", """      const fn = { mecze: renderMecze, mecz: renderMecz, typydnia: renderTypyDnia, szukaj: renderSzukaj, zaklady: renderZaklady,
        skutecznosc: renderSkutecznosc, prosby: renderProsby, start: renderStart,""")
rep("""  const start = location.hash.slice(1);
  if (PAGES.includes(start)) show(start, false);
  window.addEventListener("hashchange", () => { const h = location.hash.slice(1); if (PAGES.includes(h) && h !== S.page) show(h, false); });""",
    """  function route(h) {
    const [p, arg] = (h || "").split("/");
    if (p === "mecz" && arg) { S.mecz = arg; S.calc = null; show("mecz", false); return; }
    show(PAGES.includes(p) || p === "skaner" ? p : "mecze", false);
  }
  route(location.hash.slice(1));
  window.addEventListener("hashchange", () => route(location.hash.slice(1)));""")

(APP / "index.html").write_text(page, encoding="utf-8")
print("index.html written:", len(page), "chars")
