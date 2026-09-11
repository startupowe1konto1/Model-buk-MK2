"""Wspolny zapis raportu CLI: markdown + JSON obok siebie, albo wydruk na stdout.

`baseline.main()` i `run_backtest.main()` mialy identyczny blok kodu
odpowiedzialny wylacznie za to, gdzie wyladuje wynik (plik vs stdout) - roznil
je tylko obiekt zrzucany do JSON-a. To ten blok, wyjety raz.
"""
import json
import os
from typing import Optional


def write_or_print(text: str, data: object, out_prefix: Optional[str]) -> None:
    if not out_prefix:
        print(text)
        return
    out_dir = os.path.dirname(out_prefix)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_prefix + ".md", "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(out_prefix + ".json", "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("zapisano {0}.md i {0}.json".format(out_prefix))
