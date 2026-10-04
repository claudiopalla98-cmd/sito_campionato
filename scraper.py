"""
Scraper FIPAV Vicenza -> dati.json per il sito del campionato.

Ogni giro fa UNA richiesta alla pagina "Risultati e classifiche" (tutte le partite + classifica)
e una richiesta in più solo per ogni partita appena giocata (per leggere i parziali dei set).
Se qualcosa va storto il file dati.json NON viene toccato: online resta l'ultima versione buona.
"""
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------- CONFIGURAZIONE ----------------------------------------------------
CAMPIONATO_ID = "93505"
URL_ELENCO = ("https://www.fipavvicenza.it/risultati-classifiche?ComitatoId=8&StId=2388"
              "&DataDa=&StatoGara=&CId=93505&SId=&btFiltro=CERCA")
URL_DETTAGLIO = "https://www.fipavvicenza.it/mobile/risultati.asp?CampionatoId={cid}&GaraId={gid}"
OUTPUT = Path("dati.json")
CACHE_COORDINATE = Path("coordinate.json")   # coordinate trovate una volta sola e poi riusate
URL_GEO = "https://nominatim.openstreetmap.org/search"   # geocodifica gratuita di OpenStreetMap
PAUSA_SECONDI = 1.0
USER_AGENT = "calendario-volley-rosa/1.0 (progetto personale non commerciale)"

# Controlla il file robots.txt del sito prima di partire. Disattivalo solo se hai il permesso del Comitato.
RISPETTA_ROBOTS = True

# Squadre, nello STESSO ORDINE dei loghi del sito (la prima è la tua).
# (nome ufficiale FIPAV, nome mostrato, impianto, indirizzo, latitudine, longitudine)
# Le coordinate sono indicative: servono solo allo schizzo della mappa, i link a Maps usano l'indirizzo.
SQUADRE = [
    ("COMUNELLO VOLLEY ROSA'", "Comunello Volley Rosà", "Palasport · Tezze sul Brenta", "Via Tre Case, 54, 36056 Baracche VI", 45.706, 11.752),
    ("CESUNA 2DM", "Cesuna 2DM", "Palasport Auditorium di Galliosito · Gallio", "Via Roma, 31, 36032 Gallio VI", 45.889, 11.535),
    ("AURORA 76 TRYTECH", "Aurora 76 Trytech", "S.M. Virgilio · Camisano Vicentino", "Via Europa, 45, 36043 Camisano Vicentino VI", 45.525, 11.788),
    ("S.T.A.I. VOLLEY CAMPIGLIA", "S.T.A.I. Volley Campiglia", "Comunale Campiglia · Campiglia dei Berici", "Via G. Pascoli, 36020 Campiglia dei Berici VI", 45.396, 11.519),
    ("MELEDO 2DIVM", "Meledo 2DIVM", "S.M. \"Muttoni\" · Sarego", "Via Damiano Chiesa, 36040 Meledo VI", 45.485, 11.376),
    ("ROBUR 2DM ROSSA", "Robur 2DM Rossa", "Centro Sportivo Tommaso Assi · Thiene", "Via San Gaetano, 33H, 36016 Thiene VI", 45.7107, 11.4778),
    ("USD ALTAIR", "USD Altair", "S.E. Tiepolo · Vicenza", "Via Palemone Remnio, 14, 36100 Vicenza VI", 45.553, 11.543),
    ("MARMI FAEDO II DIV.M", "Marmi Faedo II Div.M", "S.M. Crosara · Cornedo Vicentino", "Via G. G. Trissino, 1, 36073 Cornedo Vicentino VI", 45.609, 11.396),
    ("XFORM VBV", "XForm VBV", "S.M. Zanella · Bolzano Vicentino", "Via C. Battisti, 18, 36050 Bolzano Vicentino VI", 45.585, 11.636),
    ("ROBUR 2DM GIALLA", "Robur 2DM Gialla", "Comunale M. Ausiliatrice · Thiene", "Via San Gaetano, 87, 36016 Thiene VI", 45.7085, 11.4755),
]
# ------------------------------------------------------------------------------

FILE_COORDINATE = Path("coordinate.json")  # lo crea geocodifica.py: coordinate esatte dei palazzetti
INDICE = {s[0]: i for i, s in enumerate(SQUADRE)}

SESSIONE = requests.Session()
SESSIONE.headers.update({"User-Agent": USER_AGENT})
SESSIONE.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])))


def pulisci(testo: str) -> str:
    return re.sub(r"\s+", " ", testo.replace("\xa0", " ")).strip()


def scarica(url: str) -> str:
    risposta = SESSIONE.get(url, timeout=30)
    risposta.raise_for_status()
    return risposta.text


def verifica_robots() -> None:
    """Si ferma se robots.txt non consente l'accesso automatico alle pagine che usiamo."""
    try:
        risposta = SESSIONE.get("https://www.fipavvicenza.it/robots.txt", timeout=30)
    except requests.RequestException as errore:
        raise SystemExit(f"Non riesco a leggere robots.txt ({errore}): per prudenza mi fermo.")
    if risposta.status_code == 404:
        return
    risposta.raise_for_status()
    regole = RobotFileParser()
    regole.parse(risposta.text.splitlines())
    for url in (URL_ELENCO, URL_DETTAGLIO.format(cid=CAMPIONATO_ID, gid="1")):
        if not regole.can_fetch(USER_AGENT, url):
            raise SystemExit("robots.txt del sito non consente l'accesso automatico a:\n  " + url +
                             "\nChiedi l'ok al Comitato prima di continuare (vedi RISPETTA_ROBOTS in cima al file).")


def distanza_km(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def geocodifica(indirizzo: str, approssimata):
    """Cerca l'indirizzo su OpenStreetMap (Nominatim). Restituisce (lat, lon), oppure None se la rete non risponde."""
    senza_civico = re.sub(r",\s*\d+\w*(?=,)", "", indirizzo)
    for domanda in dict.fromkeys([indirizzo, senza_civico]):
        time.sleep(1.1)  # regola di Nominatim: al massimo 1 richiesta al secondo
        try:
            risposta = SESSIONE.get(URL_GEO, params={"q": domanda, "format": "jsonv2", "limit": 1,
                                                     "countrycodes": "it"}, timeout=30)
            risposta.raise_for_status()
            trovati = risposta.json()
        except (requests.RequestException, ValueError):
            return None
        if trovati:
            punto = (round(float(trovati[0]["lat"]), 5), round(float(trovati[0]["lon"]), 5))
            if distanza_km(punto, approssimata) <= 20:  # scarta risultati lontani (indirizzo omonimo altrove)
                return punto
    return approssimata  # non trovato: resta la posizione indicativa (correggila a mano in coordinate.json)


def coordinate_squadre():
    """Coordinate dei palazzetti: dalla cache, oppure cercate su OpenStreetMap la prima volta."""
    cache = json.loads(CACHE_COORDINATE.read_text(encoding="utf-8")) if CACHE_COORDINATE.exists() else {}
    nuove = False
    for s in SQUADRE:
        if s[3] not in cache and s[0] not in cache:  # la cache può essere indicizzata per indirizzo o per nome ufficiale
            punto = geocodifica(s[3], (s[4], s[5]))
            if punto:
                cache[s[3]] = list(punto)
                nuove = True
    if nuove:
        CACHE_COORDINATE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return [tuple(cache.get(s[3]) or cache.get(s[0]) or (s[4], s[5])) for s in SQUADRE]


def numero(testo: str) -> float:
    return float(testo.replace(".", "").replace(",", "."))


def parse_elenco(html: str):
    """Pagina 'Risultati e classifiche': restituisce (partite, classifica)."""
    soup = BeautifulSoup(html, "html.parser")
    tab_gare = soup.select_one("table.tbl-risultati")
    tab_class = soup.select_one("table.tbl-classifica")
    if not tab_gare or not tab_class:
        raise ValueError("Tabelle non trovate: il sito è cambiato?")

    partite = []
    for riga in tab_gare.select("tr"):
        celle = riga.find_all("td")
        cella_ris = riga.select_one("td.risultato")
        trovato = re.search(r"Risultato_(\d+)", cella_ris.get("id", "")) if cella_ris else None
        if len(celle) < 6 or not trovato:
            continue
        gara, giornata, quando, casa, ospite, risultato = (pulisci(celle[k].get_text(" ")) for k in range(6))
        if casa not in INDICE or ospite not in INDICE:
            raise ValueError(f"Squadra sconosciuta ({casa} / {ospite}): aggiorna SQUADRE in scraper.py")
        try:
            data = datetime.strptime(quando, "%d/%m/%y %H:%M").strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            print(f"[-] Gara {gara}: data non ancora definita, la salto")
            continue
        partite.append({"id": trovato.group(1), "n": int(gara), "g": int(giornata), "d": data,
                        "h": INDICE[casa], "a": INDICE[ospite], "r": "" if risultato == "-" else risultato})

    classifica = []
    for riga in tab_class.select("tr"):
        c = [pulisci(x.get_text(" ")) for x in riga.find_all("td")]
        if len(c) < 13 or not c[0].isdigit():
            continue
        if c[1] not in INDICE:
            raise ValueError(f"Squadra sconosciuta in classifica ({c[1]}): aggiorna SQUADRE in scraper.py")
        classifica.append({"sq": INDICE[c[1]], "pt": int(c[2]), "g": int(c[3]), "v": int(c[4]), "p": int(c[5]),
                           "sf": int(c[6]), "ss": int(c[7]), "qs": numero(c[8]),
                           "pf": int(c[9]), "ps": int(c[10]), "qp": numero(c[11]), "pen": int(c[12])})
    if len(partite) == 0 or len(classifica) != len(SQUADRE):
        raise ValueError("Dati incompleti: partite o classifica non lette correttamente")
    return partite, classifica


def parse_dettaglio(html: str):
    """Pagina mobile di una gara giocata: restituisce ([set casa, set ospite], [[parz. casa, parz. ospite], ...]) o None."""
    soup = BeautifulSoup(html, "html.parser")

    def lato(selettore):
        box = soup.select_one(selettore)
        set_vinti = box.select_one(".set") if box else None
        if not set_vinti or not set_vinti.get_text().strip().isdigit():
            return None
        parziali = [int(x.get_text()) for x in box.select(".parziale") if x.get_text().strip().isdigit()]
        return int(set_vinti.get_text()), parziali

    casa, ospite = lato("#risultatoCasa"), lato("#risultatoOspite")
    if not casa or not ospite:
        return None
    return [casa[0], ospite[0]], [[a, b] for a, b in zip(casa[1], ospite[1])]


def costruisci_dati(partite, classifica, precedenti, leggi_dettaglio, coord=None):
    """Mette insieme il dizionario finale. leggi_dettaglio(id) -> html (usata solo per le gare nuove)."""
    coord = json.loads(FILE_COORDINATE.read_text(encoding="utf-8")) if FILE_COORDINATE.exists() else {}
    for p in partite:
        if not p["r"]:
            continue
        prima = precedenti.get(p["id"])
        if prima and prima.get("r") == p["r"] and prima.get("p"):
            p["s"], p["p"] = prima["s"], prima["p"]  # già letta: nessuna nuova richiesta
            continue
        time.sleep(PAUSA_SECONDI)
        dettaglio = parse_dettaglio(leggi_dettaglio(p["id"]))
        if dettaglio:
            p["s"], p["p"] = dettaglio
        else:  # ripiego: almeno i set dalla pagina elenco (es. "3 - 1")
            trovato = re.search(r"(\d+)\s*-\s*(\d+)", p["r"])
            if trovato:
                p["s"] = [int(trovato.group(1)), int(trovato.group(2))]
    partite.sort(key=lambda p: (p["g"], p["d"]))
    return {
        "campionato": "Seconda divisione maschile - girone unico",
        "squadre": [{"nome": s[1], "impianto": s[2], "indirizzo": s[3],
                     "la": coord.get(s[0], [s[4], s[5]])[0], "lo": coord.get(s[0], [s[4], s[5]])[1]} for s in SQUADRE],
        "partite": partite,
        "classifica": classifica,
    }


def main() -> None:
    if RISPETTA_ROBOTS:
        verifica_robots()
    try:
        partite, classifica = parse_elenco(scarica(URL_ELENCO))
        precedenti = {}
        if OUTPUT.exists():
            precedenti = {p["id"]: p for p in json.loads(OUTPUT.read_text(encoding="utf-8")).get("partite", [])}
        dati = costruisci_dati(
            partite, classifica, precedenti,
            lambda gid: scarica(URL_DETTAGLIO.format(cid=CAMPIONATO_ID, gid=gid)),
            coordinate_squadre())
    except (requests.RequestException, ValueError) as errore:
        raise SystemExit(f"Errore: {errore}\nIl file {OUTPUT} NON è stato aggiornato.")

    # Riscrive il file solo se i dati sono cambiati (così non ci sono commit inutili)
    if OUTPUT.exists():
        vecchi = json.loads(OUTPUT.read_text(encoding="utf-8"))
        vecchi.pop("aggiornato", None)
        if vecchi == dati:
            print("Nessuna novità.")
            return
    dati["aggiornato"] = datetime.now(ZoneInfo("Europe/Rome")).isoformat(timespec="seconds")
    OUTPUT.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")
    giocate = sum(1 for p in dati["partite"] if p.get("s"))
    print(f"Aggiornato {OUTPUT}: {len(dati['partite'])} partite ({giocate} giocate).")


if __name__ == "__main__":
    main()
