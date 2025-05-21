#!/usr/bin/env python3
import os
import time
import random
import logging
import json
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- FILES ---
ASINS_FILE = "asins.txt"
STATE_FILE = "state.json"

# --- CONFIGURATION ---
CHECK_INTERVAL = 30  # secondes entre chaque cycle
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
if not DISCORD_WEBHOOK_URL:
    logging.error("La variable DISCORD_WEBHOOK_URL n’est pas définie !")
    exit(1)

# --- LOGGING ---
logging.basicConfig(
    filename="watcher.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# --- HTTP SESSION AVEC RETRIES ---
session = requests.Session()
retries = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[500, 502, 503, 504],
    allowed_methods=["GET"]
)
adapter = HTTPAdapter(max_retries=retries)
session.mount("https://", adapter)
session.mount("http://", adapter)

# --- USER-AGENTS & HEADERS ---
UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.4 Safari/605.1.15",
]
COMMON_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"
}

# --- HELPERS: load ASINs and state ---
def load_asins(path=ASINS_FILE):
    if not os.path.exists(path):
        logging.error("Le fichier %s est introuvable.", path)
        exit(1)
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception as e:
            logging.error("Impossible de lire %s : %s", STATE_FILE, e)
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        logging.error("Impossible de sauvegarder %s : %s", STATE_FILE, e)

ASINS = load_asins()

# --- SCRAPING FUNCTIONS ---
def fetch_html(asin: str) -> str:
    url = f"https://www.amazon.fr/dp/{asin}"
    headers = {**COMMON_HEADERS, "User-Agent": random.choice(UAS)}
    resp = session.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text

def get_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    elt = soup.select_one("#productTitle")
    return elt.get_text(strip=True) if elt else None

def get_image_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    img = soup.select_one("#imgTagWrapperId img") or soup.select_one("#landingImage")
    if img:
        return img.get("data-old-hires") or img.get("src")
    return None

def est_disponible(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    btn   = bool(soup.select_one("#add-to-cart-button"))
    avail = soup.select_one("#availability")
    text  = avail.get_text(strip=True) if avail else ""
    return btn or ("En stock" in text)

def vendu_par_amazon(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    v_elt = soup.select_one(
        '[offer-display-feature-name="desktop-merchant-info"] .offer-display-feature-text-message'
    )
    e_elt = soup.select_one(
        '[offer-display-feature-name="desktop-fulfiller-info"] .offer-display-feature-text-message'
    )
    v_txt = v_elt.get_text(strip=True) if v_elt else ""
    e_txt = e_elt.get_text(strip=True) if e_elt else ""
    return any(txt == "Amazon" for txt in (v_txt, e_txt) if txt)

def get_price(html: str) -> float | None:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.select_one(".a-price .a-offscreen")
    if tag and tag.text:
        txt = tag.text.strip().replace('\u202f', '').replace(' ', '')
        num = ''.join(ch for ch in txt if ch.isdigit() or ch in '.,')
        try:
            return float(num.replace(',', '.'))
        except ValueError:
            logging.warning("Impossible de convertir le prix %r", txt)
    return None

# --- NOTIFICATION ---
def notifier_discord(asin: str, title: str | None, image_url: str | None, price: float | None):
    url   = f"https://www.amazon.fr/dp/{asin}"
    prix  = f"{price:.2f} €" if price is not None else "non lu"
    embed = {
        "title": title or f"Produit {asin}",
        "url": url,
        "description": f"Vendu/expédié par Amazon\nPrix : {prix}",
        "thumbnail": {"url": image_url} if image_url else None
    }
    resp = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
    resp.raise_for_status()
    logging.info("Notif Discord envoyée pour %s (%s)", asin, prix)

# --- BOUCLE PRINCIPALE ---
def main():
    previous = load_state()
    logging.info("Watcher démarré pour %s", ASINS)

    while True:
        changed = False
        for asin in ASINS:
            try:
                html    = fetch_html(asin)
                dispo   = est_disponible(html)
                vendeur = vendu_par_amazon(html)
                current = dispo and vendeur

                # Si remise en stock
                if current and not previous.get(asin, False):
                    title   = get_title(html)
                    img     = get_image_url(html)
                    price   = get_price(html)
                    notifier_discord(asin, title, img, price)

                # Mise à jour de l’état
                if previous.get(asin) != current:
                    previous[asin] = current
                    changed = True

            except requests.exceptions.HTTPError as e:
                logging.error("HTTPError pour %s : %s", asin, e)
                time.sleep(random.uniform(5, 15))
            except Exception as e:
                logging.error("Erreur inattendue pour %s : %s", asin, e)
                time.sleep(random.uniform(5, 15))

        if changed:
            save_state(previous)

        time.sleep(CHECK_INTERVAL + random.uniform(0, 5))

if __name__ == "__main__":
    main()
