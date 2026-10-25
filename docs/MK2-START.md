# MK2 — uruchomienie kolektora API-Football (krok 0–1)

Wszystko wykonujesz na swoim komputerze w PowerShellu. Kolektor działa na
czystym Pythonie 3.10+ (bez instalowania pakietów). Dane lądują poza
repozytorium: `Documents\Model-Buk-MK2-Data`.

## 1. Klucz API (raz)

```powershell
setx API_FOOTBALL_KEY "TWÓJ_KLUCZ"
```

Zamknij okno PowerShell i otwórz nowe (zmienna jest widoczna dopiero w nowym
oknie). Klucza nie wpisujemy do żadnego pliku w repo.

## 2. Kod

```powershell
cd $HOME\Documents\Model-Buk-Merged-work
git fetch origin
git checkout mk/praca
```

## 3. Test połączenia

```powershell
py -3 app\scripts\mk2_odds_collector.py status
```

Powinno wypisać plan (Pro), datę końca i zużycie dzienne. Jeśli `py` nie
działa, użyj `python` zamiast `py -3`.

## 4. Pierwszy przebieg ręcznie

```powershell
py -3 app\scripts\mk2_odds_collector.py all
```

Na końcu wypisze jedną linię JSON: liczba zapytań, ile zostało na dziś, ile
meczów i nowych kursów zapisano. Typowo 30–50 zapytań.

## 5. Harmonogram (co godzinę + zamknięcia co 15 min)

```powershell
powershell -ExecutionPolicy Bypass -File app\scripts\mk2_register_tasks.ps1
```

Zadania pojawią się w Harmonogramie zadań w folderze `\ModelBukMK2\`.
Usunięcie: ten sam skrypt z `-Remove`.

Uwaga: zadania działają tylko, gdy komputer jest włączony i nie śpi. Przy
uśpieniu kursy z tego czasu przepadają (API nie trzyma historii kursów).

## Co zbiera i ile to kosztuje

| Zadanie | Co | Zapytania |
|---|---|---|
| `all` co godzinę | kalendarz wczoraj..+3 dni (5) + kursy 15 lig, strony po 10 meczów (ok. 25–35) + sezony raz na dobę (15) | ok. 35/h, ok. 850/dobę |
| `closing` co 15 min | kursy meczów startujących w ciągu 2 h, max 40 | 0 w tygodniu rano, do ok. 600/dobę w weekend |

Razem zwykle 1 000–1 500 zapytań dziennie z limitu 7 500. Każdy przebieg
zatrzymuje się, gdy zostanie 1 500 (`--reserve`). Jeśli z tego samego klucza
korzysta też kolektor Oskara, łącznie wychodzi ok. 3 000–3 500, nadal w limicie.

Ligi (zmiana: `--leagues 39,140,...`): Premier League 39, Championship 40,
LaLiga 140, Bundesliga 78, Serie A 135, Ligue 1 61, Primeira Liga 94,
Eredivisie 88, Belgia 144, Turcja 203, Szkocja 179, Ekstraklasa 106,
Liga Mistrzów 2, Liga Europy 3, Liga Konferencji 848.

## Gdzie są dane

| Plik | Zawartość |
|---|---|
| `collector.sqlite` → `quotes` | każdy kurs: mecz, bukmacher, zakład, wybór, kurs, czas aktualizacji dostawcy, kiedy pierwszy raz widziany |
| `collector.sqlite` → `odds_seen` | każde pobranie tablicy kursów meczu (świeżość, kurs zamknięcia) |
| `collector.sqlite` → `fixtures` | kalendarz, statusy, wyniki i wynik do przerwy |
| `collector.sqlite` → `runs` | przebiegi: zapytania, zostało, błędy |
| `raw\...\*.json.gz` | surowe odpowiedzi (do odtworzenia wszystkiego później) |
| `logs\collector.jsonl` | jedna linia na przebieg |

Sprawdzenie, czy zbiera:

```powershell
Get-Content $HOME\Documents\Model-Buk-MK2-Data\logs\collector.jsonl -Tail 3
```
