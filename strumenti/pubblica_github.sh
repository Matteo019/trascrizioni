#!/usr/bin/env bash
# Pubblica su GitHub (repo pubblico) lo stato attuale di main, senza la sua storia.
#
# GitLab (privato) tiene la storia completa di main; GitHub riceve solo il ramo «pubblico»,
# che a ogni pubblicazione prende un commit con gli stessi file di main.
# Prima del push controlla che nei file e nel messaggio non ci siano parole private
# (nomi di clienti...), elencate una per riga in .git/parole-private.txt: il file sta
# dentro .git, quindi non finisce mai in nessun repo.
#
# Uso (da Git Bash, nella cartella del progetto):
#   strumenti/pubblica_github.sh "Messaggio del commit pubblico"
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
PAROLE=".git/parole-private.txt"
MESSAGGIO="${1:-}"

[ -n "$MESSAGGIO" ] || { echo "Scrivi il messaggio: strumenti/pubblica_github.sh \"Cosa cambia\""; exit 1; }
[ -s "$PAROLE" ] || { echo "Manca $PAROLE (una parola privata per riga): senza controllo non pubblico."; exit 1; }
git rev-parse --verify -q pubblico >/dev/null || { echo "Manca il ramo «pubblico»."; exit 1; }

git fetch -q --no-write-fetch-head github main
if [ "$(git rev-parse github/main)" != "$(git rev-parse pubblico)" ]; then
    echo "GitHub non è dove l'ha lasciato «pubblico» (qualcuno ci ha scritto?): controlla prima di pubblicare."
    exit 1
fi
if [ "$(git rev-parse 'main^{tree}')" = "$(git rev-parse 'pubblico^{tree}')" ]; then
    echo "Niente da pubblicare: GitHub ha già gli stessi file di main."
    exit 0
fi

# parole private nei file di main o nel messaggio: ci si ferma prima di creare qualsiasi commit
trovati=$(git grep -n -i -F -f "$PAROLE" main -- . || true)
if [ -n "$trovati" ] || printf '%s' "$MESSAGGIO" | grep -q -i -F -f "$PAROLE"; then
    echo "Parole private trovate, non pubblico:"
    printf '%s\n' "$trovati" | head -20
    exit 1
fi

commit=$(git commit-tree 'main^{tree}' -p pubblico -m "$MESSAGGIO")
git branch -f pubblico "$commit"
git push github pubblico:main
echo "Pubblicato su GitHub: $(git log -1 --format='%h %s' pubblico)"
