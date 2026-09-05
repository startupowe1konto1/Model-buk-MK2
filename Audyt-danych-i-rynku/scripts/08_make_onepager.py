#!/usr/bin/env python3
"""Generuje 2-stronicowy one-pager PDF (A4) dla partnerow.
Wykresy sa renderowane do statycznego SVG w Pythonie, zeby druk nie zalezal od JS."""
import html

DEC = {1880:224,1890:3203,1900:6067,1910:8304,1920:19223,1930:24001,1940:24577,
       1950:49085,1960:60905,1970:72547,1980:80155,1990:119797,2000:267042,
       2010:366185,2020:208186}

LAYERS = [
 ("Wyniki i kursy 1X2","Club Football · 230 557 meczów","var(--a1)",
  {2000:2794,2001:6329,2002:6173,2003:3967,2004:5534,2005:6340,2006:6959,2007:6585,
   2008:7148,2009:7054,2010:6925,2011:7410,2012:10597,2013:11729,2014:11993,2015:12352,
   2016:12100,2017:12364,2018:12042,2019:12018,2020:9562,2021:13127,2022:12168,
   2023:12501,2024:11148,2025:3638}),
 ("Statystyki meczowe","rożne, strzały, SOT, kartki · 114 363","var(--a2)",
  {2000:946,2001:2864,2002:2810,2003:1803,2004:2076,2005:2502,2006:3536,2007:3548,
   2008:3726,2009:3647,2010:3627,2011:4123,2012:4316,2013:4193,2014:4280,2015:4296,
   2016:3938,2017:5612,2018:7148,2019:7105,2020:5838,2021:7812,2022:6958,2023:7193,
   2024:6828,2025:3638}),
 ("Sędzia, stadion, formacje","Transfermarkt · 88 958 meczów","var(--a2)",
  {2006:64,2008:31,2010:64,2012:3514,2013:5679,2014:5719,2015:5858,2016:5741,2017:5849,
   2018:5686,2019:5718,2020:4761,2021:6446,2022:5563,2023:6239,2024:7127,2025:10402,
   2026:4497}),
 ("Składy XI i ławka","3 179 016 wpisów","var(--a3)",
  {2013:125121,2014:205844,2015:212481,2016:207836,2017:216507,2018:203808,2019:208903,
   2020:185537,2021:252896,2022:217957,2023:252778,2024:289375,2025:410241,2026:189732}),
 ("xG","Understat · 21 690 meczów, top 5 lig","var(--a3)",
  {2014:851,2015:1846,2016:1812,2017:1878,2018:1808,2019:1823,2020:2404,2021:2186,
   2022:759,2023:1919,2024:1718,2025:1736,2026:950}),
 ("Kursy zamknięcia: rożne i kartki","Footiqo · tylko Premier League 2025/26","var(--gap)",
  {2025:380}),
 ("Kursy na rożne drużynowe","snapshotter · zaczyna się teraz","var(--live)",
  {2026:8}),
]


def ctx_svg():
    W, X0, X1, CY, CH = 940, 44, 906, 78, 52
    keys = sorted(DEC); mx = max(DEC.values()); step = (X1 - X0) / len(keys)
    p = [f'<svg viewBox="0 0 {W} 132" class="sv">']
    p.append(f'<text x="{X0}" y="18" class="t-lab">Wyniki meczów</text>')
    p.append(f'<text x="{X0}" y="33" class="t-sub">1 309 501 meczów · 224 rozgrywki '
             f'· szczyt 366 185 w dekadzie 2010</text>')
    for i, d in enumerate(keys):
        h = max(2, DEC[d] / mx * CH)
        p.append(f'<rect x="{X0+i*step+1.5:.1f}" y="{CY-h:.1f}" width="{step-3:.1f}" '
                 f'height="{h:.1f}" rx="1" style="fill:var(--a3)"/>')
        if d % 40 == 0 or d == 2020:
            p.append(f'<text x="{X0+i*step+step/2:.1f}" y="{CY+15}" text-anchor="middle" '
                     f'class="t-ax">{d}s</text>')
    bx0 = X0 + keys.index(2000) * step
    p.append(f'<path d="M{bx0:.1f} {CY+24} v6 H{X1} v-6" '
             f'style="fill:none;stroke:var(--rule2);stroke-width:1"/>')
    p.append(f'<text x="{(bx0+X1)/2:.1f}" y="{CY+42}" text-anchor="middle" class="t-sub">'
             f'wycinek na wykresie poniżej</text>')
    return "\n".join(p) + "</svg>"


def strata_svg():
    Y0, Y1 = 2000, 2026
    N = Y1 - Y0 + 1
    W, PX0, PX1, ROW, TOP = 940, 196, 868, 46, 30
    col = (PX1 - PX0) / N
    H = TOP + len(LAYERS) * ROW + 6
    p = [f'<svg viewBox="0 0 {W} {H}" class="sv">']
    for y in range(Y0, Y1 + 1):
        if y % 5 == 0 or y == Y1:
            x = PX0 + (y - Y0) * col + col / 2
            p.append(f'<text x="{x:.1f}" y="19" text-anchor="middle" class="t-ax">{y}</text>')
            p.append(f'<line x1="{x:.1f}" y1="24" x2="{x:.1f}" y2="{TOP+len(LAYERS)*ROW-6}" '
                     f'style="stroke:var(--rule);stroke-width:1"/>')
    for i, (title, sub, colr, data) in enumerate(LAYERS):
        yt = TOP + i * ROW
        base = yt + ROW - 13
        maxh = ROW - 20
        mx = max(data.values())
        p.append(f'<line x1="{PX0}" y1="{base+.5}" x2="{PX1}" y2="{base+.5}" '
                 f'style="stroke:var(--rule);stroke-width:1"/>')
        p.append(f'<text x="0" y="{yt+15}" class="t-lab">{html.escape(title)}</text>')
        p.append(f'<text x="0" y="{yt+27}" class="t-sub">{html.escape(sub)}</text>')
        for y in range(Y0, Y1 + 1):
            v = data.get(y)
            if not v:
                continue
            h = max(2.5, v / mx * maxh)
            p.append(f'<rect x="{PX0+(y-Y0)*col+2:.1f}" y="{base-h:.1f}" '
                     f'width="{max(3,col-4):.1f}" height="{h:.1f}" rx="1" '
                     f'style="fill:{colr}"/>')
        lbl = f"{round(mx/1000)}k" if mx >= 1000 else str(mx)
        p.append(f'<text x="{PX1+7}" y="{base-1}" class="t-pk">{lbl}</text>')
    return "\n".join(p) + "</svg>"


HTML = """<!doctype html><html lang="pl"><head><meta charset="utf-8">
<title>Model Buk — stan projektu</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,300;0,500;1,6..72,300&family=Source+Sans+3:wght@400;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{
  --ground:#FFFFFF; --paper:#F8F9F7;
  --ink:#17201C; --ink2:#5A6A62; --ink3:#8C9A92;
  --rule:#DDE4DF; --rule2:#C3CEC7;
  --a1:#2F6B52; --a2:#4A8B6B; --a3:#7FB199; --gap:#B5502F; --live:#B8862A;
  --disp:"Newsreader",Georgia,serif;
  --body:"Source Sans 3","Helvetica Neue",Arial,sans-serif;
  --mono:"IBM Plex Mono",Menlo,monospace;
}
@page{size:A4; margin:14mm 13mm}
*{box-sizing:border-box}
body{margin:0;font-family:var(--body);color:var(--ink);background:#fff;
     font-size:9.6pt;line-height:1.45;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.page{page-break-after:always;position:relative;height:269mm;display:flex;flex-direction:column}
.page:last-child{page-break-after:auto}

.mast{display:flex;justify-content:space-between;align-items:baseline;
      border-bottom:1.5pt solid var(--ink);padding-bottom:6pt}
.mast .brand{font-family:var(--disp);font-size:21pt;font-weight:500;letter-spacing:-.01em}
.mast .meta{font-family:var(--mono);font-size:7.2pt;color:var(--ink3);letter-spacing:.08em;
            text-transform:uppercase}

h1{font-family:var(--disp);font-weight:300;font-size:22pt;line-height:1.1;
   letter-spacing:-.015em;margin:12pt 0 6pt}
h1 em{font-style:italic;color:var(--a1)}
.lede{font-size:10.5pt;color:var(--ink2);margin:0 0 12pt;max-width:150mm;line-height:1.5}

h2{font-family:var(--disp);font-weight:500;font-size:12.5pt;margin:0 0 6pt;
   padding-bottom:3pt;border-bottom:.75pt solid var(--rule2)}

.cols{display:grid;grid-template-columns:1fr 1fr;gap:11pt 16pt}
.block{margin-bottom:11pt}
ul{margin:0;padding-left:12pt}
li{margin-bottom:4pt}
li b{font-weight:600}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:500}

.findings{counter-reset:f}
.findings li{list-style:none;position:relative;padding-left:17pt;margin-bottom:5.5pt}
.findings li::before{counter-increment:f;content:counter(f);position:absolute;left:0;top:.5pt;
  font-family:var(--mono);font-size:7.5pt;color:#fff;background:var(--a1);
  width:12pt;height:12pt;border-radius:50%;display:flex;align-items:center;
  justify-content:center;font-weight:500}
.findings{padding-left:0}

table{width:100%;border-collapse:collapse;font-size:8.6pt}
th{font-family:var(--mono);font-size:7pt;letter-spacing:.06em;text-transform:uppercase;
   color:var(--ink3);font-weight:400;text-align:right;padding:0 0 4pt;border-bottom:.75pt solid var(--rule)}
th:first-child{text-align:left}
td{padding:4.5pt 0;border-bottom:.5pt solid var(--rule);text-align:right;
   font-family:var(--mono);font-variant-numeric:tabular-nums}
td:first-child{text-align:left;font-family:var(--body)}
tr:last-child td{border-bottom:none}

.figs{display:grid;grid-template-columns:repeat(5,1fr);border-top:1.2pt solid var(--ink);
      border-bottom:1.2pt solid var(--ink);margin:0 0 16pt}
.fig{padding:9pt 8pt;border-right:.5pt solid var(--rule)}
.fig:last-child{border-right:none}
.fig .n{font-family:var(--mono);font-size:16pt;font-weight:500;letter-spacing:-.03em;
        display:block;line-height:1.05}
.fig .k{font-size:8pt;color:var(--ink2);display:block;margin-top:3pt}
.fig .s{font-family:var(--mono);font-size:6.6pt;color:var(--ink3);display:block}

.chartbox{border:.75pt solid var(--rule);padding:9pt 10pt 6pt;margin-bottom:13pt;
          background:var(--paper);border-radius:2pt}
.chartcap{font-family:var(--mono);font-size:7pt;letter-spacing:.08em;text-transform:uppercase;
          color:var(--ink3);margin:0 0 7pt}
.sv{display:block;width:100%;height:auto}
.t-lab{font-family:var(--body);font-size:8pt;fill:var(--ink)}
.t-sub{font-family:var(--mono);font-size:6.4pt;fill:var(--ink3)}
.t-ax{font-family:var(--mono);font-size:6.4pt;fill:var(--ink3)}
.t-pk{font-family:var(--mono);font-size:6.2pt;fill:var(--ink3)}

.legend{display:flex;gap:14pt;font-family:var(--mono);font-size:7pt;color:var(--ink2);
        margin-top:6pt}
.legend span{display:flex;align-items:center;gap:4pt}
.sw{width:7pt;height:7pt;border-radius:1pt;display:inline-block}

.callout{background:var(--paper);border-left:2.5pt solid var(--a1);padding:8pt 11pt;
         margin:0;font-size:9.4pt;line-height:1.5}
.callout b{color:var(--a1)}

footer{margin-top:auto;border-top:.5pt solid var(--rule);
       padding-top:5pt;font-family:var(--mono);font-size:6.8pt;color:var(--ink3);
       display:flex;justify-content:space-between}
</style></head><body>

<div class="page">
  <div class="mast">
    <div class="brand">Model Buk</div>
    <div class="meta">Stan projektu · 5 września 2026</div>
  </div>

  <h1>Zbudowaliśmy fundament danych<br>i zmierzyliśmy, <em>czy to się spina</em>.</h1>
  <p class="lede">Jeden dzień pracy: audyt wykonalności, zebranie dziesięciu zestawów danych,
  pomiar realnego rynku bukmacherskiego i uruchomienie automatycznego zbieracza kursów.
  Poniżej co z tego wynika.</p>

  <div class="cols">
    <div>
      <div class="block">
        <h2>Co zostało zrobione</h2>
        <ul>
          <li><b>Audyt wykonalności</b> — przeliczona ekonomika projektu z uwzględnieniem
          polskiego podatku 12% od obrotu, którego wcześniej nie było w modelu.</li>
          <li><b>Pomiar rynku</b> — ręczny audyt oferty STS oraz sonda API-Football:
          33 bukmacherów, 338 typów zakładów, zmierzone marże.</li>
          <li><b>Zebranie danych</b> — 10 zestawów, <span class="num">18 GB</span>,
          <span class="num">605</span> zmiennych, <span class="num">9,2 mln</span> rekordów.</li>
          <li><b>Zbieracz kursów</b> — działa automatycznie co godzinę, archiwizuje ceny
          przed każdym meczem Ekstraklasy i Premier League.</li>
          <li><b>Architektura i dokumentacja</b> — projekt silnika, katalog danych na
          poziomie kolumn, dwa repozytoria.</li>
        </ul>
      </div>

      <div class="block">
        <h2>Co dalej</h2>
        <ul>
          <li><b>Backtest na <span class="num">86 025</span> meczach</b>, dla których mamy
          kurs otwarcia i zamknięcia. To jedyne miejsce, gdzie można dziś sprawdzić,
          czy metoda w ogóle bije rynek.</li>
          <li><b>Pierwszy model rożnych</b> na <span class="num">380</span> meczach
          Premier League z historycznymi kursami zamknięcia.</li>
          <li><b>Zbieranie cen</b> — po sezonie powstanie zbiór ~<span class="num">10,5 mln</span>
          kursów, którego nie da się kupić wstecz.</li>
        </ul>
      </div>
    </div>

    <div>
      <div class="block">
        <h2>Pięć ustaleń, które zmieniają plan</h2>
        <ul class="findings">
          <li><b>Podatek 12% wywraca ekonomikę.</b> Minimalny sensowny zysk oczekiwany to
          <span class="num">+13,6%</span>, nie <span class="num">+5%</span>. Wszystkie cztery
          dotychczasowe testowe zakłady są po podatku <b>ujemne</b>.</li>
          <li><b>Częste zdarzenia to zła ścieżka.</b> Najlepsza drużyna w bazie trafia
          4+ rożne w <span class="num">89%</span> meczów — ale kurs jest wtedy tak krótki,
          że podatek zjada całą przewagę.</li>
          <li><b>Rynek rożnych nie jest naiwny.</b> Pinnacle wycenia go przy marży
          <span class="num">6,1%</span>, STS przy <span class="num">13,7%</span>.
          Przewaga leży w rozjeździe między nimi, nie w błędzie rynku.</li>
          <li><b>Kursy, których szukaliśmy, są dostępne</b> — rożne drużynowe, strzały celne
          i kartki od sześciu bukmacherów. Brakuje wyłącznie ich historii.</li>
          <li><b>Bardzo dużo da się sprawdzić już teraz</b> —
          <span class="num">227 525</span> meczów z kursami 1X2,
          <span class="num">86 025</span> z policzalną miarą jakości typu.</li>
        </ul>
      </div>

      <p class="callout">Mamy dużo danych <b>o tym, co się w meczach działo</b>, i prawie
      żadnych <b>o tym, ile rynek za to żądał</b>. Dlatego zbieracz kursów działa od wczoraj —
      historii cen nie da się odzyskać.</p>
    </div>
  </div>

  <footer><span>Model Buk · dokument wewnętrzny</span><span>1 / 2</span></footer>
</div>

<div class="page">
  <div class="mast">
    <div class="brand">Co mamy</div>
    <div class="meta">Inwentarz danych · 5 września 2026</div>
  </div>

  <div class="figs" style="margin-top:12pt">
    <div class="fig"><span class="n">605</span><span class="k">zmiennych</span><span class="s">w 10 zestawach</span></div>
    <div class="fig"><span class="n">9,2 mln</span><span class="k">rekordów</span><span class="s">łącznie</span></div>
    <div class="fig"><span class="n">1888</span><span class="k">najstarszy mecz</span><span class="s">Bolton – Derby</span></div>
    <div class="fig"><span class="n">224</span><span class="k">rozgrywki</span><span class="s">31 krajów</span></div>
    <div class="fig"><span class="n">18 GB</span><span class="k">na dysku</span><span class="s">dane surowe</span></div>
  </div>

  <div class="chartbox">
    <p class="chartcap">Pełna historia · liczba meczów w dekadach</p>
    __CTX__
  </div>

  <div class="chartbox">
    <p class="chartcap">Przekrój warstw danych · 2000–2026 · każdy pas ma własną skalę,
    wartość szczytowa podpisana z prawej</p>
    __STRATA__
    <div class="legend">
      <span><i class="sw" style="background:var(--a1)"></i>dane obecne</span>
      <span><i class="sw" style="background:var(--live)"></i>zaczyna się teraz</span>
      <span><i class="sw" style="background:var(--gap)"></i>jeden sezon, jedna liga</span>
    </div>
  </div>

  <div class="cols">
    <div>
      <h2>Pokrycie ligowe</h2>
      <table>
        <thead><tr><th>Segment</th><th>Statystyki</th><th>xG</th><th>Kursy rożne</th></tr></thead>
        <tbody>
          <tr><td>Top 5 Europy</td><td>pełne</td><td>2014→</td><td>1 sezon</td></tr>
          <tr><td>Reszta Europy (17 lig)</td><td>pełne</td><td>brak</td><td>brak</td></tr>
          <tr><td>Ekstraklasa</td><td>brak</td><td>brak</td><td>brak</td></tr>
          <tr><td>Reszta świata</td><td>brak</td><td>brak</td><td>brak</td></tr>
        </tbody>
      </table>
    </div>
    <div>
      <h2>Największe braki</h2>
      <table>
        <thead><tr><th>Czego brakuje</th><th>Skala</th></tr></thead>
        <tbody>
          <tr><td>Historia cen rożnych drużynowych</td><td>0 meczów</td></tr>
          <tr><td>Ekstraklasa: rożne i strzały</td><td>0 źródeł</td></tr>
          <tr><td>Kontuzje z datą publikacji</td><td>brak</td></tr>
          <tr><td>Ruch linii między otwarciem a zamknięciem</td><td>2 punkty</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <footer><span>Źródła: Football-Data.co.uk · Transfermarkt · Understat · Footiqo · StatsBomb · ClubElo · API-Football</span><span>2 / 2</span></footer>
</div>
</body></html>"""

out = HTML.replace("__CTX__", ctx_svg()).replace("__STRATA__", strata_svg())
open("onepager.html", "w", encoding="utf-8").write(out)
print(f"onepager.html zapisany ({len(out)/1024:.0f} KB)")
