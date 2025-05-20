#!/usr/bin/env python3
import os
import time
import random
import logging
import requests
from bs4 import BeautifulSoup

# 1. CONFIGURATION
ASINS               = [
    "B0DZ2QNKM2",  # exemple 1
    "B0DFD23Q9B",  # exemple 2
    # ajoute ici tes ASINs
]
CHECK_INTERVAL      = 30  # secondes entre chaque cycle
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

if not DISCORD_WEBHOOK_URL:
    print("[ERROR] La variable DISCORD_WEBHOOK_URL n’est pas définie !")
    exit(1)

# 2. USER-AGENTS
UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.4 Safari/605.1.15",
]

# 3. LOGGING
logging.basicConfig(
    filename="watcher.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# 4. Récupération du HTML
def fetch_html(asin: str) -> str:
    url = f"https://www.amazon.fr/dp/{asin}"
    headers = {"User-Agent": random.choice(UAS)}
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    return r.text

# 5. Extraction du titre
def get_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    elt = soup.select_one("#productTitle")
    return elt.get_text(strip=True) if elt else None

# 6. Extraction de l’image principale
def get_image_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    img = soup.select_one("#imgTagWrapperId img")
    if img:
        return img.get("data-old-hires") or img.get("src")
    landing = soup.select_one("#landingImage")
    if landing:
        return landing.get("data-old-hires") or landing.get("src")
    return None

# 7. Disponibilité
def est_disponible(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    btn   = bool(soup.select_one("#add-to-cart-button"))
    avail = soup.select_one("#availability")
    text  = avail.get_text(strip=True) if avail else ""
    dispo = btn or ("En stock" in text)
    print(f"[DEBUG][dispo] btn={btn}, avail={text!r} → {dispo}")
    return dispo

# 8. Vendu/expédié strictement par "Amazon"
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
    print(f"[DEBUG][vendeur] merchant-info={v_txt!r}, fulfiller-info={e_txt!r}")
    is_amazon = any(txt == "Amazon" for txt in (v_txt, e_txt) if txt)
    print(f"[DEBUG][vendeur] résultat strict Amazon: {is_amazon}")
    return is_amazon

# 9. Extraction du prix (optionnel)
def get_price(html: str) -> float | None:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.select_one(".a-price .a-offscreen")
    if tag and tag.text:
        txt = tag.text.strip().replace('\u202f','').replace(' ','')
        num = ''.join(ch for ch in txt if ch.isdigit() or ch in ',.')
        try:
            return float(num.replace(',', '.'))
        except ValueError:
            pass
    return None

# 10. Notification Discord avec embed
def notifier_discord(asin: str, title: str | None, image_url: str | None, price: float | None):
    url    = f"https://www.amazon.fr/dp/{asin}"
    prix   = f"{price:.2f} €" if price is not None else "non lu"
    embed  = {
        "title": title or f"Produit {asin}",
        "url": url,
        "description": f"Vendu/expédié par Amazon\nPrix : {prix}",
        "thumbnail": {"url": image_url} if image_url else None
    }
    payload = {"embeds": [embed]}
    print(f"[DEBUG][notify_discord] payload={payload!r}")
    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    print(f"[DEBUG][notify_discord] status={resp.status_code}, text={resp.text!r}")
    resp.raise_for_status()
    logging.info("Notif Discord envoyée pour %s (%s)", asin, prix)

# 11. Boucle principale avec gestion des réapparitions
def main():
    previous_status = {asin: False for asin in ASINS}
    logging.info("Watcher multi-ASIN (Discord+Embed) démarré pour %s", ASINS)

    while True:
        for asin in ASINS:
            html      = fetch_html(asin)
            dispo     = est_disponible(html)
            vendeur   = vendu_par_amazon(html)
            title     = get_title(html)
            image_url = get_image_url(html)
            price     = get_price(html)
            current   = dispo and vendeur

            print(f"[DEBUG][{asin}] dispo={dispo}, vendeur={vendeur}, "
                  f"prev={previous_status[asin]}, current={current}, "
                  f"title={title!r}, img={image_url!r}, prix={price}")

            # Notification seulement à la transition False → True
            if current and not previous_status[asin]:
                notifier_discord(asin, title, image_url, price)

            previous_status[asin] = current

        time.sleep(CHECK_INTERVAL + random.uniform(-5, 5))

if __name__ == "__main__":
    main()
