---
tags: [todo, weather, entity-resolution, backlog]
status: backlog
date: 2026-09-05
priority: sredni — dopiero gdy model bazowy dziala
---

# 31 — TODO: pogoda i dopasowanie stadionów

> Odłożone świadomie. Zapisane, żeby nie uciekło.

## Problem

Open-Meteo daje darmowe archiwum pogody **po współrzędnych geograficznych**. My mamy nazwy stadionów jako tekst. Brakuje mostu między jednym a drugim.

Zmierzone 05.09.2026:

```text
unikalnych nazw stadionow w transfermarkt : 2 968
stadionow pobranych z OSM (poj. > 8000)   :   242
dopasowanie DOKLADNE nazw                 :   111  (3,7%)
unikalnych par (stadion, data) do pokrycia: 88 553
stadionow w OSM w Europie (bez filtra)    : 11 147
```

Czyli 96% nazw nie pasuje wprost. „Olimpico di Roma" vs „Stadio Olimpico", „Giuseppe Meazza" vs „San Siro" itd.

## Dwie drogi

### Droga A — dopasowywać stadiony (2 968 encji)

Pobrać pełne 11 147 stadionów z OSM, potem fuzzy matching nazw + weryfikacja ręczna najczęstszych.
Nakład: ~2 dni. Dokładność: wysoka, ale długi ogon śmieci.

### Droga B — dopasowywać KLUBY (~430 encji) ← rekomendowana

Zamiast stadionów dopasować kluby z `data/raw/transfermarkt/clubs.csv` i przypisać im stadion domowy ze współrzędnymi. Klub zmienia stadion raz na dekadę, więc błąd jest znikomy.

Nakład: **~pół dnia**. Siedmiokrotnie mniej encji, znacznie czystsze nazwy (nazwy klubów są bardziej ustandaryzowane niż stadionów).

Wyjątki do obsłużenia osobno: mecze na neutralnym terenie (`neutral_venue` w Footiqo, `stadium` odbiegający od domowego w transfermarkcie), finały, mecze przeniesione.

## Kroki

- [ ] pobrać pełny zestaw stadionów z OSM Overpass (11 147, bez filtra pojemności)
- [ ] zbudować tabelę `club_id -> stadium_name -> (lat, lon, elevation)`
- [ ] fuzzy match nazw klubów + ręczna weryfikacja top 200 wg liczby meczów
- [ ] flaga `venue_confidence` dla każdego dopasowania
- [ ] wykryć mecze na neutralnym terenie i wyłączyć je z automatycznego przypisania
- [ ] odpytać Open-Meteo Archive dla unikalnych par (współrzędne, data)
- [ ] zapisać jako `data/raw/weather/` z `provider_timestamp` i `fetched_at`

## Uwaga metodologiczna — to nie jest kosmetyka

Nota [[11 - Czas predykcji i leakage]] wymienia „rzeczywista pogoda zamiast archiwalnej prognozy" jako typowe źródło leakage. Open-Meteo Archive zwraca **pogodę zrealizowaną**, nie prognozę znaną przed meczem.

Do backtestu historycznego to akceptowalne, ale **w produkcji trzeba używać endpointu prognoz i zapisywać je z `known_at`**, bo model przewidujący T−24h nie mógł wiedzieć, ile faktycznie spadło deszczu.

## Priorytet

Nota [[04 - Rejestr cech]] mówi wprost: pogoda ma **niski priorytet bazowy**, liczą się głównie `EnvironmentalShock` (ekstrema, wysokość, duża zmiana klimatu) i interakcje typu `WindShock × Crossing`.

Czyli: to jest praca do zrobienia dopiero wtedy, gdy model bazowy działa i szukamy przyrostowej wartości. Nie teraz.
