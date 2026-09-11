# Handoff — stan projektu na 11.09.2026

> Dokument startowy dla nowej sesji. Poprzednie leżą w `docs/handoff-archiwum/`.
> Ten zastępuje handoff z 09.09 (po backteście). Tamten opis danych, podatku
> i Betclica **obowiązuje nadal** — tutaj jest to, co doszło i co się zmieniło.

---

## A. Jedno zdanie

Model nie ma przewagi nad rynkiem i wiemy już **dlaczego** — nie dlatego, że
przesadza z pewnością siebie, tylko dlatego, że po prostu za mało wie; a raporty
w repozytorium mówią to teraz same z siebie, bez potrzeby tłumaczenia ich komukolwiek.

---

## B. Co wiemy po pomiarach

### Model kontra losowe obstawianie

Sprawdzian: udajemy zakład po kursie z piątku i patrzymy, w którą stronę kurs
pójdzie do meczu. Jeśli model coś wie, rynek zwykle „przychodzi po nas".

| kto | zakładów | średni CLV | mediana CLV | **rynek szedł w naszą stronę** |
|---|---:|---:|---:|---:|
| model, wariant polski | 32 152 | +0,3288% | −0,4841% | **47,72%** |
| model, Betclic bez podatku | 66 774 | +0,0895% | −0,3443% | **48,12%** |
| **losowe obstawianie** | 213 792 nóg | +0,1059% | +0,0084% | **50,08%** |

Model jest **gorszy od losowania** na obu miarach, które cokolwiek znaczą.
Przy tej liczbie zakładów to nie jest szum.

### Dlaczego — i to obala wcześniejszą diagnozę

| | model | rynek |
|---|---:|---:|
| jak bardzo myli się co do własnej pewności (ECE) | 0,00667 | 0,00387 |
| ile naprawdę wie (log-loss, mniej = lepiej) | 1,02482 | 0,99727 |

Liczone na 71 264 meczach sezonów testowych, na wszystkich, nie tylko na tych,
na które model postawił.

Pierwszy wiersz to uczciwość. Model jest gorszy, ale **nieznacznie** — spodziewano
się różnicy dziesięciokrotnej, wyszła niecałe dwukrotna. Drugi wiersz to wiedza
i tam różnica jest, w proporcji, kilkanaście razy większa.

**Model nie kłamie na temat swojej pewności. On mało wie.**

> **Konsekwencja praktyczna:** przekalibrowanie modelu (izotoniczne, Platta)
> **nie pomoże** — nie ma czego prostować. Trzeba mu dać więcej danych.
> To wykreśla z listy tani krok, który wyglądał obiecująco.

---

## C. Trzy przekonania, które pomiar obalił

Ta sekcja jest najcenniejsza w całym dokumencie. Każde z tych trzech brzmiało
sensownie i każde było fałszywe. Nie odkrywaj ich drugi raz.

### 1. „Dodatni średni CLV to dobry znak" — nie

CLV to iloraz cen: ma dolną granicę na −100%, ale nie ma górnej. Garść dużych
korzystnych ruchów wystarczy, żeby podciągnąć średnią, choć typowy zakład traci.
Dowód: **samo losowe obstawianie** daje średnią +0,1059% przy medianie +0,0084%.
Prawoskośność jest własnością miary, nie przewagą.

**Patrz na medianę i na odsetek pobitych zamknięć.**

### 2. „Wyższy próg daje lepszy CLV, więc bramka dobrze porządkuje zakłady" — nie

Średni CLV i odsetek pobitych zamknięć idą między wariantami podatkowymi
w **przeciwnych** kierunkach. Pozorna poprawa to ten sam artefakt co wyżej:
ostrzejszy próg przechyla wybór w stronę długich kursów i podbija średnią.

### 3. „CLV spada z sezonu na sezon, więc rynek się wyostrzył" — nie

Ten sam spadek wychodzi identycznie z **całkowicie wyłączonym modelem i bramką**
i przeżywa wszystkie trzy metody zdejmowania marży. Przyczyna jest w danych:
średnia marża wliczona w kolumny `PSH/PSD/PSA` rośnie z **2,46% (2015/16) do
5,08% (2024/25)**, podczas gdy na zamknięciu stoi około 3,9%. Zmieniło się to,
**czym jest piątkowe zdjęcie** u football-data.co.uk.

> **Reguła:** porównania CLV między sezonami na kolumnach `PS*` nie są bezpieczne.
> Tabela sezonowa w raportach nie jest trendem efektywności rynku. Raporty
> ostrzegają o tym same, ale warto wiedzieć, skąd to się bierze.

---

## D. Co zbudowano

### Gałąź `baseline-kalibracja`, 10 commitów przed `main`, **niescalona**

Testy: **144 zielone** (nasze) plus **7** w silniku rożnych.

Doszło w tym przyroście:

- `backtest/baseline.py` — punkt odniesienia bez modelu, dla wszystkich trzech
  metod zdejmowania marży, po sezonach i ligach
- kalibracja modelu obok rynkowej, liczona na wszystkich meczach testowych
- raporty czytelne bez tłumacza: punkt odniesienia w tej samej tabeli, liczba
  odrębnych meczów obok liczby zakładów, wyjaśnienie mylącej średniej,
  ostrzeżenie przy tabeli sezonowej i **zdanie wprost mówiące, na co patrzeć**
- `app/` — silnik do rzutów rożnych, dociągnięty z równoległego repozytorium
  (szczegóły w `app/POCHODZENIE.md`), z naprawioną bramką podatkową

### Silnik rożnych z drugiego repozytorium

Skopiowany 11.09 z `github.com/mkonieczny1212/Model-buk` @ `7dc4bab`. To ten sam
projekt prowadzony przez drugą osobę: tam rożne, tutaj 1X2.

**Ich własny wniosek pokrywa się z naszym** — rynek zamknięcia był lepiej
skalibrowany niż ich model na każdej z czterech linii. Dwie niezależne próby,
dwa różne rynki, dwie różne metody, ten sam wynik. To mocniejszy dowód niż
każdy z osobna.

Poprawiono w nim bramkę: decydowała na opłacalności **przed** podatkiem. Mnożnik
podatkowy jest teraz obowiązkowy, a jego brak wywala konfigurację z wyjaśnieniem.

> Ich raportowane ROI (**+20,7%** dla bramki v0.1) jest **przed podatkiem**.
> Po 12% od stawki realny zwrot jest o kilkanaście punktów niższy, a ich własny
> przedział ufności i tak obejmował zero.

---

## E. Co robić dalej, w tej kolejności

1. **Scalić `baseline-kalibracja` do `main` i wypchnąć.** Nie zrobione — decyzja
   należy do właściciela.
2. **Sprawdzić kalibrację modelu w ogonie rozkładu.** Uczciwość mierzyliśmy jako
   średnią po wszystkich meczach. Model może być w porządku przy typowych
   spotkaniach, a mylić się właśnie przy mało prawdopodobnych wynikach — czyli
   tam, gdzie stawia. Średnia by to zamaskowała. **Jeśli tak, to tani, punktowy
   problem; jeśli nie, potwierdza się „model za mało wie" i trzeba dokładać cechy.**
   To jedno rozstrzygnięcie decyduje, czy następny krok to godziny czy dni.
3. **Wybór metody zdejmowania marży jest robiony na całym zbiorze**, w tym na
   sezonach testowych. To ten sam grzech, który cały pipeline ma wykluczać, tylko
   na poziomie ustawienia. I nie jest obojętny: punkt odniesienia dla długich
   kursów wynosi +0,056% dla `proportional`, +0,442% dla `shin` i +0,599% dla
   `power` — a pipeline wybiera `power`, najkorzystniejszą z trzech. Poprawnie:
   wybierać metodę wewnątrz pętli, z samych sezonów treningowych.
4. **Dokładanie cech do modelu** — forma, kontuzje, terminarz, pogoda. Dopiero
   po punkcie 2, bo to on mówi, czy to jest właściwa droga.
5. Otwarte z poprzedniego handoffu: pogoda (`weather/` puste), osłona monitoringu
   snapshottera, dług w rejestrze MB-0002/3/4, audyt Betclica.

---

## F. Pułapki, w które już wdepnięto

- **Nie ufaj liście procesów, ufaj plikowi.** Sprawdzałem, czy przeliczanie się
  skończyło, patrząc na `ps`; pokazało „gotowe", gdy jeszcze liczyło, i przez to
  zacommitowano raporty o rundę starsze. Czas zapisu pliku nie kłamie.
- **Przerwany agent może zostawić celowo zepsuty kod.** Agenci dowodzą, że ich
  testy działają, psując kod i sprawdzając, czy test to wyłapie. Przerwanie
  między popsuciem a przywróceniem zostawia usterkę w drzewie. Zdarzyło się:
  w bramce silnika rożnych został `# MUTATION` gatujący na wartości przed
  podatkiem. **Czytaj diff przed commitem, zawsze.**
- **Plan z gotowym kodem produkuje puste testy.** W pierwszym przyroście plan
  zawierał kompletny kod każdego zadania; implementerzy przepisywali zamiast
  wyprowadzać i **pięć pustych testów** przeszło nietkniętych (m.in. test mający
  pilnować, żeby model nie podglądał wyniku meczu, który przewiduje — porównywał
  liczbę zapamiętaną przed zmianą, więc nie mógł paść). Drugi przyrost pisano
  bez kodu w planie, z obowiązkiem dowiedzenia mutacją, że każdy test gryzie —
  i to zadziałało.
- **Nota 22 §5 przeczy handoffowi** w definicji progu: nota wymaga ostrej
  nierówności (`EV_net <= EV_min → NO BET`), handoff dopuszcza równość
  (`EV_net >= 0.05`). Kod trzyma się handoffu i ma to udokumentowane
  w `backtest/gate.py`. **Notę 22 trzeba uspójnić.**
- **`app/` i `backtest/` to dwa różne światy.** `backtest/` jest świadomie na
  czystej bibliotece standardowej i działa bez instalowania czegokolwiek.
  `app/` wymaga pandas, catboost, FastAPI. Nie mieszaj.

---

## G. Gdzie co jest

```text
Model-buk-2.0/
├── HANDOFF.md                     ten plik
├── docs/handoff-archiwum/         poprzednie handoffy
├── docs/superpowers/plans/        plany obu przyrostow
├── backtest/                      pipeline 1X2, czysty stdlib, 144 testy
│   └── baseline.py                punkt odniesienia bez modelu
├── app/                           silnik roznych z drugiego repo
│   └── POCHODZENIE.md             skad, czym sie rozni, co poprawiono
├── reports/                       kalibracja rynku, 3 warianty CLV, baseline
├── Football-Prediction-Brain/     25 not metodologicznych (zrodlo prawdy)
├── Audyt-danych-i-rynku/          notatki i skrypty 01-09
└── experiments/test-bets.csv      4 bety, dlug wg poprzedniego handoffu

hazard_saas/data/raw/              dane surowe, 18 GB, POZA repo
automated_bets_downloader/         snapshotter, okna capture poprawione 11.09
```

### Jak odpalić

```bash
cd /Users/wojciechrudnicki/Code_projects/Model-buk-2.0
python3 -m unittest discover -s tests                     # 144 testy, ~2 min
python3 -m backtest.baseline --out reports/baseline-null  # punkt odniesienia, ~1 min
python3 -m backtest.run_backtest --tau pl_standard --out reports/backtest-clv-pl_standard   # ~9 min
```

Silnik rożnych wymaga osobnego środowiska (`app/requirements.txt`) i Pythona
co najmniej 3.11. Jego test podatkowy chodzi bez tych zależności:

```bash
cd app && PYTHONPATH=src ~/.pyenv/versions/3.12.8/bin/python3.12 -m unittest tests.test_tax_gate
```
