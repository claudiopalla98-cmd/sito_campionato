# Sito del campionato – Comunello Volley Rosà

Come funziona:

```
Sito FIPAV Vicenza -> scraper.py -> dati.json -> index.html (GitHub Pages) -> telefono
```

- `index.html`: il sito. Ogni 5 minuti rilegge `dati.json` da solo.
- `scraper.py`: legge pagina "Risultati e classifiche" (1 richiesta per giro) e, solo per le partite appena giocate, la pagina di dettaglio (parziali dei set). Se qualcosa non torna **non** tocca `dati.json`.
- `.github/workflows/aggiorna.yml`: lancia lo scraper ogni 5 minuti nei giorni di gara (ven-dom, pomeriggio/sera) e ogni ora negli altri momenti, e salva `dati.json` se è cambiato.
- `dati.json`: fotografia attuale (calendario completo, nessun risultato ancora).

## Messa online (GitHub Pages)

1. Crea una repo nuova e carica tutti i file di questa cartella. La cartella `.github` è nascosta sul Mac (`Cmd+Maiusc+.` per vederla): in alternativa su GitHub usa **Add file → Create new file**, scrivi come nome `.github/workflows/aggiorna.yml` e incolla il contenuto.
2. **Settings → Pages → Source: Deploy from a branch → `main` / root**. Il sito sarà su `https://TUO-UTENTE.github.io/NOME-REPO/`.
3. **Actions → Aggiorna dati campionato → Run workflow** per il primo giro di prova.

## Da sapere

- Prima di partire lo scraper legge il `robots.txt` del sito e si ferma se non consente l'accesso automatico (`RISPETTA_ROBOTS` in cima a `scraper.py`). Se succede, serve l'ok del Comitato.
- Per provarlo sul computer: `pip install -r requirements.txt` e `python scraper.py`.
- Nuova squadra o cambio impianto: si modifica la lista `SQUADRE` in `scraper.py`.
- Progetto personale, non ufficiale FIPAV.
