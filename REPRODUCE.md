# Reproducing this repository

Input: Zenodo record 10809039, the CSV "Datensatz" file of the Bundesverwaltungsgericht corpus (snapshot 2024-03-13); lexifina/germany-law
`main` for the overlay (`tools/de_index.py`).

```
python tools/de_index.py <germany-law clone>
python tools/build.py bverwg <csv> <empty folder> --today 2026-11-06 --snapshot 2024-03-13 --record 10809039
```

Gives 27,200 decision commits and one documentation commit.
