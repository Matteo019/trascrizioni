# Modifiche dal PC di casa

## Soglia di silenzio del microfono: 120 → 30

In `src/trascrittore.py`, `SILENCE_RMS["microfono"]` passa da 120 a 30.

**Motivo:** con il microfono, quando chi parla è lontano (per esempio il docente in aula),
il volume dei blocchi resta spesso sotto 120. Quei blocchi venivano trattati come silenzio
e quindi non trascritti. Con 30 passano a Whisper, che li amplifica fino a `GUADAGNO_MAX`.
La soglia per l'audio del PC resta 300, perché il segnale digitale è molto più forte.

## Nota per il PC di lavoro

Questo commit è stato pushato su GitHub direttamente da questo clone, senza passare dallo
script `strumenti/pubblica_github.sh`. Sul PC di lavoro bisogna quindi riportare la modifica
in `main` (GitLab) e riallineare il ramo `pubblico` a `github/main` prima di pubblicare di nuovo.
