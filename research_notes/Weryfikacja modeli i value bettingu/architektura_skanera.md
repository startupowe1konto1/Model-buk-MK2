# Architektura produkcyjnego skanera value bet (Model Buk) — źródła, kadencja, selekcja, walidacja, luki (stan na 2026-09-28)

Zakres: skaner top-20 lig europejskich, kilka najpewniejszych zakładów z realnym zyskiem u polskich bukmacherów z licencją (podatek 12%, mnożnik wypłaty 0,88). Notatki łączą odczyt kodu repozytorium (tylko odczyt) z dokumentacją dostawców, regulaminami i literaturą. API-Football NIE było wywoływane; żadna strona bukmachera ani porównywarki nie była scrapowana.

Kontekst projektu, który warunkuje wszystkie wnioski:
- Żaden własny model nie pokonał rynku closing (0 z 22 lig); cała zmierzona dyspersja między bukmacherami jest warta ~8 pp yieldu, a polski podatek kosztuje ~11,5 pp — [Audyt-danych-i-rynku/32 - M0 i granica podatkowa.md](file:///C:/Users/oskar/Documents/Model-Buk-Merged/Audyt-danych-i-rynku/32%20-%20M0%20i%20granica%20podatkowa.md) (linie 13-17, 135-156).
- Model był gorszy od losowego obstawiania w CLV: odsetek zakładów, przy których rynek „przyszedł" do nas, 47,72% (wariant polski, 32 152 zakłady) vs 50,08% dla losowych nóg — [HANDOFF.md](file:///C:/Users/oskar/Documents/Model-Buk-Merged/HANDOFF.md) (linie 24-31).

---

## 1. Źródła bieżących kursów (w tym ostra referencja i polscy bukmacherzy), ich pokrycie i koszt; regulaminy PL bukmacherów i ryzyko prawne

### Takeaway
Żaden pojedynczy legalnie dostępny feed nie daje jednocześnie ostrej referencji (Pinnacle/Betfair) i polskich bukmacherów: realna konfiguracja to **referencja ostra z The Odds API (region `eu`, Pinnacle) lub API-Football (Pinnacle w odpowiedzi `/odds?fixture`)** + **polskie kursy z odds-api.io (STS PL, Superbet, Betclic PL, eFortuna PL, LVbet PL, Betfan PL)**, łącznie rzędu ~125-190 USD/mies. Pinnacle i Betfair wolno używać wyłącznie jako *danych referencyjnych* — gra u nich przez rezydenta PL jest przestępstwem skarbowym, a polscy bukmacherzy w regulaminach zastrzegają odmowę/limit zakładu bez podania przyczyny.

### Cited Findings

**API-Football (obecny dostawca, Pro do 2026-10-28)**
- `/odds` zwraca kursy przedmeczowe dostępne zwykle 1-14 dni przed meczem, aktualizowane co 3 godziny; przechowywane jest tylko ostatnie 7 dni („you must capture it as it comes in") — [API-Football, beginner's guide](https://www.api-football.com/news/post/how-to-get-started-with-api-football-the-complete-beginners-guide) (treść z wyniku wyszukiwarki; bezpośredni fetch strony i `documentation-v3` zwrócił HTTP 403).
- Sonda projektu: lista 33 bukmacherów API-Football zawiera Pinnacle, Betfair, Bet365 i Superbet (id 34); brak STS, Fortuny, Betclic, LVBET, Forbet, Etoto, Totalbet — [Audyt-danych-i-rynku/28 - API-Football wyniki sondy.md](file:///C:/Users/oskar/Documents/Model-Buk-Merged/Audyt-danych-i-rynku/28%20-%20API-Football%20wyniki%20sondy.md) (linie 30-41).
- Jeden request `/odds?fixture=1557395` (Newcastle-Bournemouth, PL) zwrócił 6 bukmacherów: 1xBet, Bet365, BetVictor, Marathonbet, Pinnacle, William Hill; Pinnacle tylko 19 rynków (vs 105 Bet365) — tamże (linie 62-68).
- Superbet (id 34) zwrócił zero kursów w testach live 2026-09-28 — informacja z briefu zlecającego (nie weryfikowana ponownie, API nie było wywoływane).
- Lokalny cache `/odds?date=2026-08-29` ma `results: 0` — [app/runtime/api_catalog/spot_check_raw/odds__date-2026-08-29.json](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/runtime/api_catalog/spot_check_raw/odds__date-2026-08-29.json); spójne z 7-dniową retencją.
- Cennik (źródło wtórne, niezweryfikowane na api-football.com): Pro 19 USD/mies. = 7 500 req/dzień; Ultra 29 USD = 75 000/dzień; Mega 39 USD = 150 000/dzień; brak opłat za nadwyżkę — podsumowanie wyszukiwarki na bazie [DEV Community survey](https://dev.to/leomarsh886/a-survey-of-public-apis-for-building-a-football-live-score-site-in-2026-part-3-football-data-api-16hi) i [TheStatsAPI vs API-Football](https://www.thestatsapi.com/blog/thestatsapi-vs-api-football).

**The Odds API**
- Plany: Starter 0 USD/500 kredytów; 20K 30 USD; 100K 59 USD; 5M 119 USD; 15M 249 USD miesięcznie — [the-odds-api.com](https://the-odds-api.com/).
- Koszt `GET /v4/sports/{sport}/odds` = liczba rynków × liczba regionów (jedno wywołanie zwraca wszystkie mecze ligi); `/events/{id}/odds` = unikalne rynki × regiony; historyczne = 10 × rynki × regiony; snapshoty historyczne co 5 min od września 2022 (wcześniej co 10 min), dane od 6 czerwca 2020; pole `last_update` na poziomie rynku; regiony `us, us2, uk, au, eu` — [The Odds API v4 docs](https://the-odds-api.com/liveapi/guides/v4/).
- Region `eu` zawiera Pinnacle (klucz `pinnacle`) z adnotacją „Odds are from public website which may incur a delay", a także Betfair Exchange, 1xBet, Betsson, Marathon Bet; region `uk` zawiera `betfair_ex_uk`; brak STS, Fortuny, Superbet, LV BET i innych polskich — [The Odds API, bookmaker list](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html).
- Klucze lig: m.in. `soccer_epl`, `soccer_efl_champ`, `soccer_spain_la_liga`, `soccer_spain_segunda_division`, `soccer_germany_bundesliga`, `soccer_germany_bundesliga2`, `soccer_italy_serie_a`, `soccer_italy_serie_b`, `soccer_france_ligue_one`, `soccer_france_ligue_two`, `soccer_netherlands_eredivisie`, `soccer_portugal_primeira_liga`, `soccer_belgium_first_div`, `soccer_turkey_super_league`, `soccer_spl`, `soccer_poland_ekstraklasa`, `soccer_austria_bundesliga`, `soccer_switzerland_superleague`, `soccer_denmark_superliga`, `soccer_greece_super_league`, `soccer_norway_eliteserien`, `soccer_sweden_allsvenskan` — [The Odds API, sports list](https://the-odds-api.com/sports-odds-data/sports-apis.html).
- Sprzeczność: blog konkurenta twierdzi, że The Odds API nie ma Pinnacle („❌ No") — [OddsPapi pricing comparison](https://oddspapi.io/blog/odds-api-pricing-2026-comparison/); przeczy temu pierwotna strona The Odds API (wyżej). Źródło pierwotne ma pierwszeństwo, ale adnotacja o opóźnieniu jest istotna.

**odds-api.io (jedyny znaleziony feed z polskimi bukmacherami)**
- Plany: Solo £49/mies. (2 bukmacherów), Starter £99 (5), Growth £179 (10), Pro £229 (15); wszystkie płatne 5 000 req/h; darmowy: 2 bukmacherów „rekreacyjnych", 100 req/h; „365+ Bookmakers", pre-match i live, „with historical odds" — [odds-api.io](https://odds-api.io/).
- Katalog bukmacherów zawiera: „STS PL", „Superbet", „Betclic PL", „LVbet PL", „Betfan PL", „eFortuna PL", ComeOn, Betcris, Betfair Exchange; **Pinnacle nie występuje** w katalogu — [odds-api.io/sportsbooks](https://odds-api.io/sportsbooks); strona Pinnacle w serwisie nie zawiera Pinnacle — [odds-api.io/sportsbooks/pinnacle](https://odds-api.io/sportsbooks/pinnacle).
- Deklaracje dla Superbet: „sub-second latency", „Latency < 150ms", „Odds update within seconds of line movements"; strona nie rozróżnia wariantów krajowych Superbet (PL/RO) — [odds-api.io/sportsbooks/superbet](https://odds-api.io/sportsbooks/superbet).
- odds-api.io pisze, że „Pinnacle is restricting API access for odds data providers" i promuje Betfair Exchange jako zamiennik ostrej referencji — [odds-api.io blog](https://odds-api.io/blog/pinnacle-api-shutdown-alternatives).

**Pinnacle, Betfair, inni**
- „Access to Pinnacle API suite has been closed for the general public since July 23rd, 2025"; dostęp indywidualny dla „select high value bettors & commercial partnerships", wsparcie dla „academics and pregame handicapping projects"; wniosek na api@pinnacle.com — [pinnacleapi-documentation README](https://github.com/pinnacleapi/pinnacleapi-documentation/blob/master/README.md).
- Betfair nie przyjmuje klientów z Polski (źródła wtórne, jedno źródło podaje odmiennie) — [CheekyPunter](https://www.cheekypunter.com/faq/betfair-country-restrictions/); [Caanberry](https://caanberry.com/betfair-legal-countries/).
- Sportmonks: Starter €29 (5 lig), Growth €99 (30), Pro €249 (120); dodatek Odds & Predictions €24/mies. („50+ bookmakers", „150+ markets"); bez nazw bukmacherów na stronie — [Sportmonks plans](https://www.sportmonks.com/football-api/plans-pricing/). Premium Odds Feed €129 (Lite, 10+ bukm.) / €199 (Pro, 120+), aktualizacja ~co minutę, historia do 7 dni po meczu — źródło wtórne [OddsPapi blog](https://oddspapi.io/blog/odds-api-pricing-2026-comparison/) (via wynik wyszukiwarki).
- OddsJam: ceny API niepubliczne (rozmowa sprzedażowa); konsumenckie 99-499 USD/mies.; „over 100 sportsbooks" — źródło wtórne [OddsPapi: OddsJam alternative](https://oddspapi.io/blog/oddsjam-api-alternative/), [SportsAPI.com](https://sportsapi.com/api-directory/oddsjam/).
- OddsPapi deklaruje darmowe 250 req/mies., 350+ bukmacherów, Pinnacle i Betfair Exchange oraz historię w cenie — deklaracja sprzedawcy, [OddsPapi pricing comparison](https://oddspapi.io/blog/odds-api-pricing-2026-comparison/); SportsGameOdds 99-499 USD/mies., 80+ bukmacherów z Pinnacle — tamże.

**Prawo i regulaminy (PL)**
- Stawka podatku od gier dla zakładów wzajemnych (poza wyścigami zwierząt): 12% — [art. 74 ustawy o grach hazardowych, LEX](https://sip.lex.pl/akty-prawne/dzu-dziennik-ustaw/gry-hazardowe-17581037/art-74).
- Udział w zagranicznej grze hazardowej na terytorium RP: grzywna do 120 stawek dziennych (art. 107 § 2 KKS) — [LEX, art. 107 KKS](https://sip.lex.pl/akty-prawne/dzu-dziennik-ustaw/kodeks-karny-skarbowy-16852901/art-107); MF: kara pieniężna równa „100% of winnings obtained, not reduced by wagered amounts" oraz grzywna do 120 stawek dziennych; operatorzy telekomunikacyjni blokują domeny z Rejestru w 48 h — [gov.pl, Rejestr domen](https://www.gov.pl/web/finanse/rejestr-domen-sluzacych-do-oferowania-gier-hazardowych-niezgodnie-z-ustawa).
- STS, Regulamin Internetowych Zakładów Bukmacherskich (zatwierdzony decyzją DRG2.6834.6.2025 z 3 marca 2025): w tekście nie znaleziono klauzuli o botach/automatycznym pobieraniu danych (przeszukanie pełnego tekstu PDF pod kątem „automaty", „oprogramow", „robot", „skrypt"); pkt 3.14 — STS może odmówić przyjęcia zakładu „bez podawania przyczyny" i zamknąć konto; pkt 3.15 — może odmówić zakładów „na wysoką stawkę, zakładów zawierających powtarzający się schemat"; pkt 5.1/przykład — wygrana = stawka × „0,88 – przykładowy współczynnik, wartość współczynnika jest ustalana przez spółkę przed przyjęciem zakładu" × kurs; pkt 5.2 — maks. wygrana 500 000 zł na zakład — [STS regulamin PDF](https://static-content.sts.pl/assets/regulations/regulamin-zakladow-online-pl.pdf).
- Superbet, Regulamin Zakładów Online (10 lutego 2026, decyzja DRG2.6841.1.2026): pkt 3.4 — odmowa przyjęcia zakładu „od dowolnego Klienta, w dowolnym czasie, na dowolne Zdarzenie bez podania przyczyny"; pkt 5.F.5 — prawo „odmówić zawarcia zakładu lub ograniczyć wysokość stawki bez podania przyczyny"; nie znaleziono klauzul o oprogramowaniu automatycznym ani o wykorzystaniu danych serwisu — [Superbet regulamin](https://support.superbet.pl/hc/pl-pl/articles/360010496619-Regulamin-Zak%C5%82ad%C3%B3w-Online).
- Betclic „gra bez podatku": po przekroczeniu 1 000 zł stawek co najmniej 50% wartości stawek musi pochodzić z kwalifikujących się AKO (min. 2 zdarzenia, każde ≥1,20), liczone w kroczącym okresie 12 mies. lub 5 000 ostatnich zakładów; poniżej 50% „kupony SOLO i niekwalifikujące się AKO rozliczane są ze współczynnikiem 0.94" — [meczyki.pl, 26.08.2026](https://www.meczyki.pl/bukmacherzy/betclic-gra-bez-podatku/487) (portal afiliacyjny; wymaga weryfikacji w regulaminie promocji Betclic). Projekt oznaczył ten reżim jako „jedyna dźwignia ruszająca 11,5 punktu naraz… nadal nie został zaudytowany" — [Audyt 32](file:///C:/Users/oskar/Documents/Model-Buk-Merged/Audyt-danych-i-rynku/32%20-%20M0%20i%20granica%20podatkowa.md) (linie 142-145).

**Ryzyka operacyjne**
- Kaunitz i in.: kilka miesięcy po rozpoczęciu gry za pieniądze bukmacherzy „started to severely limit our accounts"; limity stawek, „manual inspection", odmowy — [Kaunitz, Zhong, Kreiner 2017, arXiv:1710.02824](https://arxiv.org/abs/1710.02824v2).
- Wcześniejsza notatka projektu wskazywała, że STS/Fortuna/Betclic „wymagają własnego scrapera" — [Audyt 28](file:///C:/Users/oskar/Documents/Model-Buk-Merged/Audyt-danych-i-rynku/28%20-%20API-Football%20wyniki%20sondy.md) (linia 120); brief wyklucza tę ścieżkę.

### Inferences
- **Rekomendowany stos danych (koszt miesięczny):**
  1. *Referencja ostra*: The Odds API 100K (59 USD) — Pinnacle z regionu `eu` + Betfair Exchange (`eu`/`uk`); rezerwowo Pinnacle z API-Football (Pro ~19 USD, jeśli przedłużony po 2026-10-28). Dwa niezależne źródła Pinnacle pozwalają wykrywać opóźnione/nieaktualne notowania („public website… delay").
  2. *Wykonanie (PL)*: odds-api.io Solo £49 (np. STS PL + Betclic PL) lub Starter £99 (STS PL, Superbet, Betclic PL, eFortuna PL + 1). To jedyny znaleziony legalny kanał dla STS/Fortuny/Betclic bez własnego scrapowania.
  3. *Łącznie*: ~19 + 59 USD + £49-99 ≈ 140-210 USD/mies. (kurs GBP→USD przybliżony). Sportmonks/OddsJam/SportsGameOdds nie wnoszą polskich bukmacherów w udokumentowany sposób, więc nie są potrzebne.
- API-Football w obecnym kształcie nie wystarczy jako źródło wykonania: jedyny polski bukmacher (Superbet) zwraca zero kursów, a aktualizacja co 3 h jest za rzadka na „lagging soft book".
- Pinnacle/Betfair mogą być wyłącznie źródłem prawdopodobieństwa referencyjnego; system nigdy nie może rekomendować wykonania u nich (art. 107 § 2 KKS, kara 100% wygranej).
- Odczyt kursów polskich bukmacherów przez licencjonowanego agregatora przenosi ryzyko pozyskania danych na dostawcę, ale nie usuwa ryzyka konta: regulaminy STS i Superbet pozwalają odmawiać i limitować bez podania przyczyny, a Kaunitz pokazał, że zwycięskie konta są limitowane w ciągu miesięcy. Skaner powinien zakładać krótką żywotność kont i rejestrować limity.
- Reżim podatkowy per bukmacher jest największą dźwignią: próg opłacalności ceny nad kursem fair to +13,64% przy 0,88, ale tylko +6,38% przy 0,94 (Betclic SOLO bez spełnienia warunku AKO, jeśli potwierdzone) — obliczenia własne (1/mnożnik − 1).

### Gaps
- Nie udało się pobrać pierwotnej dokumentacji API-Football (`documentation-v3` → 403): brak potwierdzenia rozmiaru strony w `/odds?date=`/`?league=` (paginacja), limitu req/min i listy bukmacherów per liga. Częstotliwość „co 3 h" pochodzi z opisu na api-football.com widocznego w wyszukiwarce.
- Cennik API-Football pochodzi ze źródeł wtórnych.
- Czy historia The Odds API jest w planie darmowym — strona cennika zdawała się tak sugerować, dokumentacja nie; należy sprawdzić przed zakupem.
- odds-api.io: nie ustalono, skąd pochodzą kursy STS/Betclic (umowa vs scraping), jaki jest koszt requestu per event/liga, jaka głębokość historii i czy „Superbet" to wariant PL. Nie sprawdzono regulaminu odds-api.io (prawo do użytku, redystrybucja).
- Nie przeczytano regulaminów Fortuny, Betclic, LV BET, Betfan ani regulaminów *serwisu* (w odróżnieniu od regulaminu zakładów) STS/Superbet — tam mogą być klauzule o automatycznym dostępie. Nie badano prawa sui generis do baz danych.
- Oficjalna lista podmiotów z zezwoleniem MF nie została pobrana (linki: [podatki.gov.pl](https://www.podatki.gov.pl/pozostale/gry-hazardowe/zaklady-wzajemne-i-gry-hazardowe-przez-internet/)).
- Nie zbadano PIT od wygranych powyżej progu kwotowego.
- Obserwacja: WebFetch do pinnacle.com zwracał ECONNREFUSED w dniu badania (przyczyna niezweryfikowana; możliwe blokowanie domeny), więc artykuły edukacyjne Pinnacle są cytowane tylko wtórnie.

---

## 2. Timing: jak kursy poruszają się przed meczem, kiedy miękcy bukmacherzy spóźniają się za ostrymi, jaka kadencja i jaki koszt requestów dla 20 lig

### Takeaway
Informacja (składy, kontuzje) spływa głównie w ostatnich ~75-90 min, ostre linie mają najwyższe limity i wiarygodność blisko startu, a jedyne rzetelne dane o przewadze miękkich kursów pokazują, że okazje istnieją 1-5 h przed meczem i są krótkotrwałe (komercyjne narzędzia skanują co ~60 s). Dla ludzkiego wykonawcy sensowna jest **kadencja warstwowa: codzienny przegląd + skan co 1 h (kursy PL + referencja) + gęsty skan co 10-15 min w oknie T-120…T-5 min**, co mieści się w The Odds API 100K i limitach odds-api.io; API-Football ma sens najwyżej co 3 h.

### Cited Findings
- Premier League: składy i kadry muszą być zgłoszone „at least 75 minutes before" meczu; od sezonu 2024-25 kluby mogą je publikować 75 min przed startem (wcześniej embargo do 60 min); to zgodne z praktyką UEFA — [Premier League news](https://www.premierleague.com/en/news/4081650); [GiveMeSport](https://www.givemesport.com/why-premier-league-team-lineups-are-announced-75-minutes-before-kick-off/).
- Linie otwarcia są stawiane z niskimi limitami; limity Pinnacle rosną w miarę zbliżania się meczu wraz z płynnością — podsumowanie artykułów Pinnacle z wyszukiwarki ([Pinnacle: higher limits](https://www.pinnacle.com/betting-resources/en/educational/why-pinnacle-offers-higher-betting-limits-than-other-sportsbooks); [Pinnacle: market movement](https://www.pinnacle.com/betting-resources/en/betting-strategy/market-movement-in-betting/4732xnzxpqpzrff5)); bezpośredni odczyt się nie powiódł (ECONNREFUSED), więc to źródło wtórne.
- Kaunitz i in.: reguła `max(odds) > 1/(p_cons − 0.05)` na kursach closing dała 3,5% zwrotu w 56 435 zakładach (479 440 meczów, 2005-2015); w symulacji na kursach minutowych, stawiając 1-5 h przed meczem, 9,9% zwrotu w 6 994 zakładach przy średnim kursie 2,32; paper trading 5,5% (407 zakładów), gra realna 8,5% (265 zakładów, 5 miesięcy), razem 6,2% w 672 zakładach; bez operatora 24/7 przegapili ok. 10× więcej okazji; opóźnienia w aktualizacji kursów u bukmacherów wymusiły test z losowym odrzuceniem 30% meczów — [arXiv:1710.02824](https://arxiv.org/pdf/1710.02824) (tekst PDF).
- RebelBetting deklaruje analizę „over one million odds every 60 seconds" z ostrych i miękkich bukmacherów — [RebelBetting](https://www.rebelbetting.com/valuebetting) (via podsumowanie wyszukiwarki).
- Buchdahl: 756 z 952 zakładów miało skrócenie ceny o 3,94% u konkurencyjnych bukmacherów — [Pinnacle Odds Dropper, Buchdahl o CLV](https://www.pinnacleoddsdropper.com/blog/closing-line-value--clv-demystified-by-expert-joseph-buchdahl).
- Kanał danych: API-Football aktualizuje `/odds` co 3 h ([api-football.com guide](https://www.api-football.com/news/post/how-to-get-started-with-api-football-the-complete-beginners-guide)); The Odds API zapisuje historię co 5 min, a Pinnacle pochodzi z publicznej strony „which may incur a delay" ([v4 docs](https://the-odds-api.com/liveapi/guides/v4/), [bookmakers](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html)); odds-api.io deklaruje <150 ms i aktualizacje „within seconds" ([Superbet page](https://odds-api.io/sportsbooks/superbet)).
- Obecny klient API-Football: `/fixtures?date=` 1 request na dzień dla wszystkich lig (TTL 180 s) z lokalnym filtrem; per mecz `/odds` (TTL 30 min), `/injuries` (TTL 60 min), `/fixtures/lineups` (TTL 15 min); tryb `deep` dokłada ostatnie mecze i statystyki — [app/src/model_buk/live_provider.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/live_provider.py) (linie 111-124, 172-203, 350-395). Konfiguracja: `api_day_offsets=[0,1]`, `max_fixtures=10`, `include_recent_observations=false` — [app/config/paper_scan.toml](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/config/paper_scan.toml).
- Projekt zaleca uruchamianie `scripts/run_paper_scan.ps1` w Harmonogramie zadań „co kilka godzin" — [docs/CURRENT-DATA-PIPELINE.md](file:///C:/Users/oskar/Documents/Model-Buk-Merged/docs/CURRENT-DATA-PIPELINE.md) (linie 221-226).

### Inferences
- **Kadencja (rekomendacja):**
  - 08:00 lokalnie — przegląd dnia: terminarz 0-72 h, watchlista, brak rekomendacji wykonawczych (kursy poranne rzadko są „ostre", a limity Pinnacle niskie).
  - Co 60 min — skan właściwy dla meczów T-24 h…T-2 h: polskie kursy (odds-api.io) + referencja (The Odds API). Wysyłać tylko okazje z referencją i kursem PL młodszymi niż ~10 min.
  - Co 10-15 min w oknie T-120…T-5 min, tylko ligi z meczami w oknie — tu przypada publikacja składów (T-75) i najwyższa wiarygodność referencji; to okno daje też naturalny „closing snapshot" do CLV.
  - Skan raz dziennie jest niewystarczający: okazje „lagging soft book" żyją minuty (komercyjne narzędzia skanują co ~60 s), a ludzki wykonawca potrzebuje 5-10 min na postawienie.
- **Koszt requestów na skan (estymacja własna; założenie: N = 60 meczów w oknie 48 h w tygodniu, 150 w weekend; 20 lig):**
  - API-Football, obecny adapter (kontekst bez `deep`): 2 × `/fixtures?date` + 3 × N = 182-452 req/skan. Co 1 h → 4,4-10,8 tys./dzień (weekend przekracza 7 500). Co 3 h → 1,5-3,6 tys./dzień. Tylko `/odds` (bez injuries/lineups poza oknem T-90): 62-152 req/skan → co 1 h 1,5-3,6 tys./dzień. Ponieważ `/odds` odświeża się co 3 h, częstsze pytanie o te same kursy jest marnowaniem quoty.
  - The Odds API: 20 lig × 2 rynki (h2h, totals) × 1 region (`eu`) = 40 kredytów/skan, niezależnie od liczby meczów; z `uk` (Betfair UK) 80. Co 1 h: 28,8-57,6 tys./mies.; gęste okno T-120 (8 skanów × jedna liga × 2-4 kredyty na slot startowy, ~100-150 slotów/mies.) dokłada rząd kilku tysięcy → mieści się w planie 100K (59 USD).
  - odds-api.io: 5 000 req/h wystarcza nawet przy odpytywaniu per mecz (150 meczów co 15 min = 600 req/h).
  - Jednorazowy backfill historyczny (tylko referencja; polskich kursów historycznie nie ma legalnie): 10 × 2 rynki × 1 region = 20 kredytów za snapshot ligi; przy ~150 slotach startowych/ligę/sezon i 2 snapshotach (T-60, T-5) ≈ 6 000 kredytów/liga-sezon → 20 lig × 3 sezony ≈ 360 tys. → jeden miesiąc planu 5M (119 USD).
- 9,9% (1-5 h przed) vs 3,5% (closing) u Kaunitza sugeruje, że przewaga miękkich kursów względem konsensusu maleje ku zamknięciu; ale to kursy bez polskiego podatku i bez polskich bukmacherów.

### Gaps
- Brak twardych, pierwotnych danych o *typowym opóźnieniu* polskich bukmacherów (STS/Fortuna/Betclic/Superbet) względem Pinnacle — trzeba to zmierzyć z własnego archiwum snapshotów.
- Nie znaleziono pierwotnego badania akademickiego o przebiegu ruchu kursów piłkarskich w czasie (opening → składy → close); wyniki wyszukiwania dawały głównie blogi niskiej jakości, pominięte.
- Liczby meczów w oknie (N) i slotów startowych to założenia; należy je policzyć z terminarza 20 lig.
- Nie ustalono, czy `/v4/sports/{sport}/events` (lista meczów) w The Odds API zużywa kredyty.

---

## 3. Logika selekcji: EV po podatku i haircucie, dolna granica prawdopodobieństwa, limit kursu, Kelly, korelacja, ranking top-N

### Takeaway
Obecna bramka (haircut 5 pp absolutnie, podatek 0,88, EV ≥ 2%, kurs ≤ 2,0) jest **matematycznie prawie niewykonalna**: wymaga kursu 24-29% powyżej fair, a przy p ≤ 0,62 jest niemożliwa w ogóle — co potwierdza wynik projektu „0 rekordów z dodatnim konserwatywnym EV". Skaner powinien liczyć p z odmarżowanej referencji ostrej (Pinnacle/Betfair, jak Kaunitz i narzędzia komercyjne), z dolną granicą kalibrowaną empirycznie (ruch do zamknięcia + rozrzut metod de-vig), stosować mnożnik podatku per bukmacher, 1 zakład na mecz, ranking po dolnej granicy EV i ułamkowy Kelly ≤ ¼ z twardym limitem.

### Cited Findings
- Istniejący selektor: EV konserwatywne = `p_lower × tax_multiplier × odds − 1 − execution_cost`; wybór najświeższego kwotowania per bukmacher, limit kursu przed wyborem najlepszej ceny, najlepsza cena per wynik, odrzucenie przeterminowanych kwotowań, opcjonalna bramka „market edge", ranking malejąco po EV konserwatywnym (tie-break: p_lower), limity `max_per_fixture=2` i `max_per_correlation_group=1` w obrębie meczu, bez dopełniania listy („never pad") — [app/src/model_buk/opportunity_selector.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/opportunity_selector.py) (linie 69-75, 106-260).
- Predyktor: `probability_lower_bound = max(0, p − probability_margin)` z `probability_margin=0,05`; docstring: margines „is not a calibrated confidence interval"; `market_probability` to odmarżowane prawdopodobieństwo *tego samego bukmachera* (bez łączenia cen między bukmacherami); `tax_multiplier` globalny z polityki — [app/src/model_buk/snapshot_predictor.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/snapshot_predictor.py) (linie 38-47, 126-155, 245-275).
- Wynik projektu: z 503 882 rekordów okazji 86 299 miało kurs ≤ 2,00 i „Żaden rekord nie miał dodatniego konserwatywnego EV po haircutcie 5%, podatku 12% i kosztach wykonania" — [docs/MODEL-BUK-1-STAGE-RESULTS.md](file:///C:/Users/oskar/Documents/Model-Buk-Merged/docs/MODEL-BUK-1-STAGE-RESULTS.md) (linie 243-248).
- Architektura docelowa: `EV_net = p × odds × tax_multiplier − 1`; decyzja po kalibracji i z dolną granicą niepewności edge; kursy nie mogą być cechami modelu PURE — [docs/TARGET-BACKEND-ARCHITECTURE.md](file:///C:/Users/oskar/Documents/Model-Buk-Merged/docs/TARGET-BACKEND-ARCHITECTURE.md) (linie 48-57).
- Kaunitz: `p_real ≈ p_cons − α`, gdzie `p_cons` = odwrotność średniego kursu z rynku; estymowane α = 0,034 (home), 0,057 (draw), 0,037 (away); wybrano α = 0,05 (α = 0,06 równie zyskowne, ale o połowę mniej zakładów) — [arXiv:1710.02824](https://arxiv.org/pdf/1710.02824).
- RebelBetting: filtr min/max kursu działa na kursie *ostrym* („true odds"), nie na kursie miękkiego bukmachera — [RebelBetting community](https://community.rebelbetting.com/t/more-high-value-bets-changes-to-odds-filter/13908) (via wynik wyszukiwarki).
- Metody de-vig: równa marża, odds ratio, logarytmiczna, Shin — [Buchdahl via Pinnacle Odds Dropper](https://www.pinnacleoddsdropper.com/blog/closing-line-value--clv-demystified-by-expert-joseph-buchdahl); w kodzie dostępne `proportional`, `power`, `shin` — [snapshot_predictor.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/snapshot_predictor.py) (linie 67-68).
- Pułapka metryk: wyższy próg EV przechyla wybór ku długim kursom i sztucznie podbija średni CLV; należy patrzeć na medianę i odsetek pobitych zamknięć — [HANDOFF.md](file:///C:/Users/oskar/Documents/Model-Buk-Merged/HANDOFF.md) (linie 60-73).

### Inferences
- **Niewykonalność obecnej bramki (obliczenia własne):** minimalny kurs = 1,02 / (0,88 × (p − 0,05)). Dla p = 0,50/0,55/0,60 wymagany kurs 2,58/2,32/2,11 > limit 2,0 (niemożliwe); dla p = 0,65/0,70/0,80 wymagany kurs 1,93/1,78/1,55, czyli +25,6%/+24,8%/+23,6% ponad fair. Przy dyspersji rynku ~8 pp to praktycznie zawsze pusto.
- **Proponowana specyfikacja kandydata (mapowalna na `OpportunityCandidate`):**
  1. `p_ref` = odmarżowane Pinnacle (lub mediana Pinnacle/Betfair-mid) w chwili skanu; `probability` = `p_ref`; model PURE tylko jako diagnostyka/weto (projekt nie ma przewagi nad rynkiem).
  2. `p_lower = p_ref − k·σ(t, liga, rynek) − ½·rozrzut(de-vig)`, gdzie σ to empiryczne odchylenie `p_ref(skan) − p_ref(close)` z archiwum snapshotów wg czasu do startu; start konserwatywny: 1-2 pp zamiast 5 pp.
  3. `tax_multiplier` per bukmacher/oferta (0,88 domyślnie; 0,94/1,0 tylko po weryfikacji regulaminu Betclic i śledzeniu udziału AKO).
  4. Bramki: EV_lower ≥ 2-3%; kurs ≤ 2,0-2,5 liczony na kursie *referencyjnym* (jak RebelBetting); wiek kwotowania PL i referencji ≤ 10 min; referencja z co najmniej 2 źródeł lub z Pinnacle blisko startu; wykluczenie meczów z ruchem referencji > X pp w ostatnich 15 min (niestabilna informacja).
  5. Dla orientacji: przy `p_lower = p_ref − 0,01` wymagany kurs PL to +17,6…18,3% ponad fair przy 0,88 i +10,1…10,7% przy 0,94 (EV ≥ 2%) — nadal rzadkie, więc oczekiwany wynik to 0-3 typy dziennie, często 0.
- **Korelacja:** `max_per_fixture=1` (dla „kilku najpewniejszych" dwa typy z jednego meczu to jedno ryzyko), dodatkowo limit na ligę/slot startowy (np. 2) i łączną ekspozycję na slot, bo wyniki w jednej kolejce są skorelowane przez wspólne błędy referencji.
- **Ranking:** po `EV_lower` (jest w kodzie), tie-break po `p_lower` i krótszym czasie do startu (wyższa wiarygodność referencji). Alternatywa „risk-adjusted": `EV_lower / (tax×odds×√(p(1−p)))`; przy limicie kursu ≤ 2,0 różnica rankingów jest niewielka.
- **Stawkowanie (ułamkowy Kelly, obliczenia własne):** `f* = (b·p − q)/b`, `b = tax × kurs − 1`, liczony na `p_lower`. Przykłady: 0,88 × 2,0 przy p = 0,60 → EV +5,6%, pełny Kelly 7,37%, ¼ Kelly 1,84%; 0,88 × 1,9 przy p = 0,62 → EV +3,7%, pełny 5,45%, ¼ 1,36%; 0,94 × 2,0 przy p = 0,55 → EV +3,4%, pełny 3,86%, ¼ 0,97%. Rekomendacja: ¼ Kelly na `p_lower`, limit 1-2% bankrolla na zakład i ~5% na slot startowy; w fazie paper stawka płaska 1 j. (prostsza statystyka).

### Gaps
- Brak empirycznego σ (jak bardzo odmarżowane Pinnacle w T-x różni się od zamknięcia) dla 20 lig — wymaga backfillu z The Odds API lub archiwum prospektywnego.
- Nie ustalono, jak dokładnie komercyjne narzędzia (RebelBetting, OddsJam, Trademate) sortują listę (strona filtrów RebelBetting zwróciła błąd); potwierdzono tylko filtr po kursie ostrym i skan co ~60 s.
- Brak źródła dla dopuszczalnego udziału Kelly przy estymowanych p; progi 1-2% są heurystyką.

---

## 4. Walidacja: protokół paper/shadow, CLV względem zamknięcia Pinnacle, liczba zakładów do potwierdzenia przewagi, reguły stopu

### Takeaway
Zysk jako kryterium jest bezużyteczny w rozsądnym czasie: przy kursie ~2,0, podatku 0,88 i yieldzie 2-3% trzeba **~5 200-11 700 zakładów** (moc 80%, α = 5% jednostronnie). CLV ma ~10× mniejsze odchylenie, więc **~40-160 zakładów** wystarcza do wykrycia średniego CLV 2-3% (a ~400-620 przy 1%). Protokół: 6-8 tygodni shadow z pełnym logowaniem kandydatów i zamknięć Pinnacle, bramka na **podatkowo skorygowanym CLV** (`0,88 × kurs_PL / kurs_fair_close − 1`), mediana i odsetek pobitych zamknięć, sekwencyjne reguły stopu; dopiero potem mały pilot pieniężny.

### Cited Findings
- Buchdahl: typowe odchylenie standardowe zysku przy kursie ~evens ≈ 1,00, a dla CLV ≈ 0,1; przy stałym biciu zamknięcia o 5% istotność „perhaps as few as just 50" zakładów (65 w jego badaniu), podczas gdy na wynikach „several thousand bets"; CLV liczony jako kurs zagrany / odmarżowany kurs zamknięcia Pinnacle — [Pinnacle Odds Dropper, Buchdahl](https://www.pinnacleoddsdropper.com/blog/closing-line-value--clv-demystified-by-expert-joseph-buchdahl).
- Zastrzeżenie Buchdahla: „odds originators" mogą nie mieć CLV, dopóki Pinnacle nie zareaguje — tamże.
- Średni CLV jest prawoskośny: samo losowe obstawianie dało średnią +0,1059% przy medianie +0,0084%; „Patrz na medianę i na odsetek pobitych zamknięć" — [HANDOFF.md](file:///C:/Users/oskar/Documents/Model-Buk-Merged/HANDOFF.md) (linie 60-67).
- Kolumny `PS*` z football-data.co.uk zmieniły charakter (marża 2,46% w 2015/16 → 5,08% w 2024/25), więc porównania CLV między sezonami nie są bezpieczne — [HANDOFF.md](file:///C:/Users/oskar/Documents/Model-Buk-Merged/HANDOFF.md) (linie 75-84); pliki football-data nie mają timestampów kwotowań i nie dowodzą wykonalnego ROI — [docs/CURRENT-DATA-PIPELINE.md](file:///C:/Users/oskar/Documents/Model-Buk-Merged/docs/CURRENT-DATA-PIPELINE.md) (linie 235-242).
- Repozytorium ma bootstrap klastrowy (`bootstrap_cluster_mean_interval`), ocenę zwrotów po podatku (`evaluate_betting_returns`) i bramkę promocji (`decide_promotion`) — [app/src/model_buk/evaluation/gates.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/evaluation/gates.py) (linie 308, 393-451, 667); `closing_odds` w replayu jest „diagnostic metadata only" — [evaluation/historical_replay.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/evaluation/historical_replay.py) (linie 41-76).
- Kaunitz porównywał wynik z bootstrapem „random bet strategy" o tej samej liczbie zakładów i kursach z tego samego okresu — [arXiv:1710.02824](https://arxiv.org/pdf/1710.02824).

### Inferences
- **Liczba zakładów — wynik finansowy (obliczenia własne; `sd = tax·kurs·√(p(1−p))`, `p = (1+yield)/(tax·kurs)`, n = ((1,645+0,842)·sd/yield)², moc 80%, α = 5% jednostronnie):**

  | mnożnik | kurs | yield 1% | 2% | 3% | 5% |
  |---|---|---:|---:|---:|---:|
  | 0,88 | 1,70 | 30 351 | 7 505 | 3 298 | 1 158 |
  | 0,88 | 1,90 | 41 342 | 10 280 | 4 543 | 1 615 |
  | 0,88 | 2,00 | 46 838 | 11 668 | 5 166 | 1 844 |
  | 0,94 | 2,00 | 54 332 | 13 560 | 6 015 | 2 155 |

  Przy 1-3 typach dziennie 5 000 zakładów to 5-14 lat — wynik finansowy może służyć tylko jako kontrola spójności.
- **Liczba zakładów — CLV (sd na zakład 0,05-0,10):** średni CLV 1% → 155-618 zakładów; 2% → 39-155; 3% → 17-69; 5% → 6-25 (moc 80%). Realistycznie przyjąć sd ≈ 0,08-0,10 dla kursów ≤ 2,0 do czasu pomiaru.
- **Definicje do logowania:** `CLV_raw = kurs_PL / kurs_fair_close_Pinnacle − 1` (czy trafiamy przed rynkiem) oraz `CLV_net = tax × kurs_PL / kurs_fair_close − 1` (czy to w ogóle opłacalne; dodatni dopiero, gdy kurs PL ≥ 13,6% ponad fair close przy 0,88). Zamknięcie = ostatnie kwotowanie Pinnacle przed startem z gęstego okna T-15…T-0.
- **Protokół:**
  1. *Shadow 0* (od razu): archiwizacja wszystkich kandydatów (też odrzuconych, z powodem), kursów PL, referencji i zamknięć; bez rekomendacji. Cel: σ referencji, opóźnienia PL vs Pinnacle, częstość okazji.
  2. *Paper* (6-8 tygodni lub ≥ 150 wybranych zakładów): rekomendacje z zamrożoną konfiguracją (hash w manifeście — już jest), stawka płaska 1 j.; ręczna kontrola wykonalności na próbie (czy kurs był jeszcze dostępny po X min).
  3. *Pilot pieniężny*: tylko jeśli dolna granica 95% CI mediany/średniej `CLV_net` > 0 i odsetek pobitych zamknięć > 50% na ≥ 150 zakładach; mikro-stawki, ¼ Kelly z limitem.
- **Reguły stopu (propozycja):** (a) po 100 zakładach paper: mediana `CLV_raw` ≤ 0 lub odsetek pobitych zamknięć ≤ 50% → stop i przebudowa; (b) po 250: górna granica 95% CI `CLV_net` < 0 → stop (reżim podatkowy nie do pokonania); (c) sekwencyjny test (SPRT) na `CLV_net`, żeby wielokrotne podglądanie nie inflowało błędu I rodzaju; (d) w pilocie: pierwszy limit stawki/odmowa u bukmachera → oznaczyć konto, liczyć „utracony wolumen"; spadek bankrolla > 20 j. → pauza i przegląd; (e) każda zmiana progu = nowy `config_id`, statystyki od zera.
- Bootstrap klastrowy po dniu/slocie startowym (już w `gates.py`) należy stosować także do CLV, bo zakłady z jednej kolejki są skorelowane.

### Gaps
- Brak pomiaru rzeczywistego sd CLV dla polskich bukmacherów przy kursach ≤ 2,0; wartość 0,1 pochodzi z Buchdahla dla ogólnego przypadku.
- Nie ustalono, czy Pinnacle „z publicznej strony" (The Odds API) ma wystarczającą rozdzielczość w ostatnich minutach, by pełnić rolę closing line; wymaga porównania z API-Football Pinnacle.
- Progi stopu są propozycją projektową, nie wynikiem literatury.

---

## 5. Analiza luk: co istniejący pipeline już implementuje, a czego brakuje do produktu

### Takeaway
Szkielet produkcyjny jest dojrzały (point-in-time, idempotencja, archiwum append-only, selektor z dolną granicą EV i bez dopełniania, bramki bootstrap), ale **brakuje wszystkiego, co czyni z niego skaner value**: adapterów polskich kursów i referencji ostrej, mapowania meczów między dostawcami, predyktora rynkowego (Pinnacle de-vig) z kalibrowaną dolną granicą, podatku per bukmacher, harmonogramu warstwowego z budżetem quoty, zapisu zamknięć i CLV, ledgera paper/settlementu oraz stawkowania. Obecna konfiguracja dodatkowo obcina skan do 10 meczów i 16 lig.

### Cited Findings
- `CurrentDataPipeline`: adaptery wstrzykiwane po nazwie (wiele dostawców możliwe), odrzucenie rekordów `available_at > cutoff`, bramki kompletności i świeżości per grupa cech, atomowy zapis `result.json`/`manifest.json`, blokada run_id, hashe wejścia/wyniku — [app/src/model_buk/current_pipeline.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/current_pipeline.py) (linie 242-556).
- `ApiFootballSnapshotAdapter`: terminarz + kursy + kontuzje + składy + (opcjonalnie) forma; pomija kursy z `update` po cutoffie; `context_availability`; limit `max_fixtures` (domyślnie 20, w konfiguracji 10) — [app/src/model_buk/ingestion/api_football.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/ingestion/api_football.py) (linie 49-184); [app/config/paper_scan.toml](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/config/paper_scan.toml).
- Archiwum prospektywne JSONL per run, append-only, z manifestem — [docs/CURRENT-DATA-PIPELINE.md](file:///C:/Users/oskar/Documents/Model-Buk-Merged/docs/CURRENT-DATA-PIPELINE.md) (linie 21-35); [app/src/model_buk/snapshot_archive.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/snapshot_archive.py).
- Kompozycja paper scan (live/offline), silnik PURE `MultiMarketEngine`, archiwizacja live — [app/src/model_buk/paper_scan.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/paper_scan.py) (linie 103-183); harmonogram przez `scripts/run_paper_scan.ps1` — [app/scripts/run_paper_scan.ps1](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/scripts/run_paper_scan.ps1).
- Katalog lig skanera: 16 lig (EPL, Championship, LaLiga, LaLiga2, Bundesliga, 2. Bundesliga, Serie A, Serie B, Ligue 1, Ligue 2, Primeira, Eredivisie, Belgia, Süper Lig, Szkocja, Ekstraklasa) — [app/src/model_buk/catalog.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/catalog.py) (linie 16-35).
- Klient odczytuje nagłówki `x-ratelimit-requests-remaining`/`-limit` — [live_provider.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/live_provider.py) (linie 79-86).
- W kodzie `app/src/model_buk` brak jakiejkolwiek implementacji Kelly/bankrolla (przeszukanie „kelly", „bankroll"); `closing_odds` występuje tylko w replayu historycznym i importerach — przeszukanie repozytorium w tej sesji.
- Mapowanie Pinnacle `PS`/`P` istnieje tylko dla archiwów football-data — [app/src/model_buk/ingestion/football_data_uk.py](file:///C:/Users/oskar/Documents/Model-Buk-Merged/app/src/model_buk/ingestion/football_data_uk.py) (linie 81-82, 235).

### Inferences
**Mapa: istnieje → brakuje**

| Obszar | Istnieje | Brakuje / do zmiany |
|---|---|---|
| Orkiestracja PIT | `current_pipeline.py` (cutoff, świeżość, idempotencja, manifest) | nic krytycznego; dodać `max_age` dla grupy `reference_odds` |
| Źródła | `ApiFootballSnapshotAdapter` (Pinnacle/Bet365 per mecz, Superbet puste) | adapter **odds-api.io** (STS PL, Betclic PL, eFortuna PL, Superbet) i adapter **The Odds API** (Pinnacle `eu`, Betfair); wspólny format `SnapshotRecord` z `provider` |
| Encje | `team_names.py`, `ingestion/canonical.py` (nie czytane szczegółowo) | mapowanie meczów i selekcji między 3 dostawcami (fixture_id API-Football ↔ event_id The Odds API ↔ odds-api.io), normalizacja rynków (1X2, O/U 2.5, AH) |
| Prawdopodobieństwo | PURE `MultiMarketSnapshotPredictor`, `market_probability` = de-vig tego samego bukmachera | nowy `SharpReferencePredictor`: de-vig Pinnacle/Betfair (osobny od PURE, zgodnie z zasadą „kursy nie są cechami PURE"), `p_lower` z empirycznego σ zamiast stałych 0,05 |
| Podatek | `tax_multiplier` per kandydat w dataclass, ale globalny w predyktorze | profil per bukmacher/oferta (0,88; Betclic 0,94/1,0 warunkowo) + licznik udziału AKO, jeśli używany reżim Betclic |
| Selekcja | `opportunity_selector.py` (EV_lower, cap kursu, best price, stale, korelacja, top-N, no padding) | `max_per_fixture=1`, limit per liga/slot, cap kursu liczony na kursie referencyjnym, bramka „ruch referencji", świeżość referencji |
| Zakres | 16 lig, `day_offsets [0,1]`, `max_fixtures=10` | 20 lig (np. + Austria, Szwajcaria, Dania, Grecja — klucze są w The Odds API), zniesienie limitu 10 meczów |
| Kadencja | Harmonogram „co kilka godzin" | scheduler warstwowy (dzienny / 60 min / 10-15 min w T-120) + budżet quoty per dostawca na bazie nagłówków limitu |
| Zamknięcie i CLV | `closing_odds` tylko w replayu historycznym | job „closing capture" (ostatnia referencja przed startem) + raport `CLV_raw`, `CLV_net`, mediana, % pobitych, bootstrap klastrowy |
| Ledger | `outcome_join` dla danych kontekstowych, settlement w replayu offline | ledger paper/live: rekomendacja, kurs wzięty, czas, stawka, wynik, limit/odmowa u bukmachera |
| Stawki | brak | ¼ Kelly na `p_lower`, limity per zakład/slot |
| Wyjście | CLI `scan-current`, JSON | krótki digest „0-3 typy" z czasem ważności kursu; ewentualne powiadomienia wymagają zgody użytkownika na wysyłkę |
| Walidacja | `gates.py`, `replay_runner`, nested walk-forward | reguły stopu/SPRT na CLV, rejestr konfiguracji paper (`config_id` już jest) |

- Kolejność budowy (najmniejsze ryzyko → wartość): (1) adapter The Odds API + closing capture + archiwum (od razu zbiera σ i zamknięcia, tani); (2) adapter odds-api.io dla 2 bukmacherów PL (Solo £49) i pomiar opóźnień PL vs Pinnacle; (3) `SharpReferencePredictor` + profil podatkowy + zmiany selektora; (4) scheduler warstwowy; (5) ledger/CLV raport i reguły stopu; (6) dopiero po pozytywnym paper — Kelly i pilot.
- Realistyczne oczekiwanie: przy podatku 0,88 i zmierzonej dyspersji ~8 pp skaner w większości skanów zwróci 0 typów; sensowność produktu zależy od (a) rzadkich dużych opóźnień PL po wiadomościach (składy T-75) i (b) reżimu z mnożnikiem 0,94/1,0. Oba trzeba zmierzyć w shadow, zanim powstanie reszta.

### Gaps
- Nie czytano szczegółowo `team_names.py`, `ingestion/canonical.py`, `evaluation/outcome_join.py`, `odds_parser.py` — ocena ich przydatności do mapowania wielu dostawców jest wstępna.
- Brak danych, ile meczów/rynków 20 lig realnie pokrywa odds-api.io dla STS/Betclic (np. 2. ligi, AH) — do sprawdzenia na darmowym planie.
