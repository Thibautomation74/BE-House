#!/usr/bin/env python3
"""
Collecte les annonces de maisons a vendre depuis les alertes email des portails
belges, les dedoublonne, les filtre, et ecrit docs/data/listings.json.

On ne scrape aucun portail. Immoweb appartient au groupe Axel Springer (comme
SeLoger), donc probablement derriere la meme protection anti-bot. On laisse les
portails envoyer leurs alertes vers une boite dediee, et on lit cette boite en
IMAP : legal, robuste, insensible au blocage d'IP.
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import re
import sys
from copy import deepcopy
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from bs4 import BeautifulSoup

from notify import send_digest

ROOT = Path(__file__).parent
DATA = ROOT / "docs" / "data"
BRUSSELS = ZoneInfo("Europe/Brussels")

# --------------------------------------------------------------------------
# Portails belges : (nom, motif d'URL, motif d'identifiant)
# Pour ajouter une agence : une ligne ici, rien d'autre a toucher.
# --------------------------------------------------------------------------
PORTALS = [
    ("Immoweb",    re.compile(r"immoweb\.be/(?:fr|nl|en)/annonce", re.I), re.compile(r"/(\d{6,})")),
    ("Zimmo",      re.compile(r"zimmo\.be/", re.I),                       re.compile(r"/([A-Za-z0-9]{5,})/?$")),
    ("Immovlan",   re.compile(r"immovlan\.be/", re.I),                    re.compile(r"([A-Z]{2}\d{6,}|\d{6,})")),
    ("Immoscoop",  re.compile(r"immoscoop\.be/", re.I),                   re.compile(r"/(\d{5,})")),
    ("Realo",      re.compile(r"realo\.be/", re.I),                       re.compile(r"/(\d{6,})")),
    ("Logic-Immo", re.compile(r"logic-immo\.be/", re.I),                  re.compile(r"(\d{6,})")),
    ("Biddit",     re.compile(r"biddit\.be/", re.I),                      re.compile(r"/(\d{4,})")),
    ("ERA",        re.compile(r"era\.be/", re.I),                         re.compile(r"/(\d{5,})")),
    ("Century 21", re.compile(r"century21\.be/", re.I),                   re.compile(r"/(\d{5,})")),
    ("Trevi",      re.compile(r"trevi\.be/", re.I),                       re.compile(r"/(\d{4,})")),
    ("We Invest",  re.compile(r"weinvest\.be/", re.I),                    re.compile(r"/([\w\-]{5,})/?$")),
    ("Easyimmo",   re.compile(r"easyimmo\.be/", re.I),                     re.compile(r"/([\w\-]{4,})/?$")),
]

# Prix : "450.000 €", "€ 450.000", "450 000 EUR", "425000 €".
#
# Le (?<!\d) est indispensable : sans lui, "Ref. 11482093 425.000 €" est lu
# comme un seul nombre, le numero de reference se collant au prix. On exige
# donc soit des groupes de 3 chiffres separes proprement, soit un nombre
# compact de 4 a 7 chiffres — jamais un melange.
_MONTANT = r"(?<!\d)(\d{1,3}(?:[.\s\u00a0]\d{3})+|\d{4,7})"
RE_PRICE_A = re.compile(_MONTANT + r"\s*(?:€|EUR\b|euros?)", re.I)
RE_PRICE_B = re.compile(r"(?:€|EUR)\s*" + _MONTANT, re.I)

RE_BEDROOMS = re.compile(r"(\d{1,2})\s*(?:chambres?\b|ch\.|slaapkamers?\b|bedrooms?\b)", re.I)
RE_BATHROOMS = re.compile(r"(\d{1,2})\s*(?:salles?\s+de\s+bains?|badkamers?)", re.I)
RE_FACADES = re.compile(r"(\d)\s*(?:fa[cç]ades?|gevels?)", re.I)
RE_PEB = re.compile(r"\b(?:PEB|EPC)\s*[:\-]?\s*([A-G])\b", re.I)
# Code postal belge : 4 chiffres — exactement comme une surface de terrain.
# "terrain 1100 m²" serait lu comme le code postal 1100, et "1400 m²" comme
# Nivelles : l'annonce basculerait dans la mauvaise zone. On exclut donc tout
# nombre suivi d'une unite, et on interroge d'abord l'URL, ou les portails
# belges placent le code postal de maniere fiable : .../floreffe/5150/11000003
RE_ZIP = re.compile(r"\b([1-9]\d{3})\b(?!\s*(?:m\s*(?:²|2\b)|€|EUR|ares?|ca\b))", re.I)
RE_ZIP_URL = re.compile(r"/([1-9]\d{3})/")

# Terrain : forme metrique explicitement etiquetee
RE_TERRAIN_M2 = re.compile(
    r"(?:terrain|jardin|parcelle|superficie\s+(?:du\s+)?terrain|grond|tuin|perceel)"
    r"[^\d]{0,25}(\d[\d\s\u00a0.]{0,7})\s*m\s*(?:²|2\b)", re.I)
# Terrain : notation belge en ares / centiares, ex "7a 50ca" ou "12 ares"
RE_TERRAIN_ARES = re.compile(r"(\d{1,3})\s*a(?:res?)?\s*(?:(\d{1,2})\s*ca)?", re.I)
# Surface habitable etiquetee
RE_HAB_LABEL = re.compile(
    r"(?:habitable|hab\.|woonopp|bewoonbare?)[^\d]{0,15}(\d[\d\s\u00a0.]{1,6})\s*m\s*(?:²|2\b)", re.I)
# Meme precaution que pour les prix, sur les surfaces.
RE_ANY_M2 = re.compile(r"(?<!\d)(\d{1,3}(?:[.\s\u00a0]\d{3})+|\d{1,4})\s*m\s*(?:²|2\b)", re.I)

RE_TRACKING = re.compile(r"[?&](utm_[^&]+|xtor[^&]*|cmpid[^&]*|mtm_[^&]*|pk_[^&]*)", re.I)


BASE_FIELDS = ("id", "portal", "url", "title", "price", "living_area",
               "land_area", "bedrooms", "bathrooms", "facades", "peb", "zipcode")


@dataclass
class Listing:
    id: str
    portal: str
    url: str
    title: str = ""
    price: int | None = None
    living_area: float | None = None
    land_area: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    facades: int | None = None
    peb: str = ""
    zipcode: str = ""
    price_per_sqm: int | None = None
    first_seen: str = ""
    age_days: int = 0
    is_new: bool = True
    flags: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
def clean_url(url: str) -> str:
    return RE_TRACKING.sub("", url.split("#")[0]).rstrip("?&")


def to_int(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)   # "450.000" et "450 000" -> 450000
    return int(digits) if digits else None


def decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def html_of(msg: email.message.Message) -> str:
    parts = []
    targets = [msg] if not msg.is_multipart() else list(msg.walk())
    for part in targets:
        if part.get_content_type() in ("text/html", "text/plain"):
            payload = part.get_payload(decode=True) or b""
            parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    return "\n".join(parts)


def identify(url: str) -> tuple[str, str] | None:
    for name, url_re, id_re in PORTALS:
        if url_re.search(url):
            match = id_re.search(url.split("?")[0].rstrip("/"))
            raw = match.group(1) if match else url.split("?")[0][-40:]
            return name, f"{re.sub(r'[^a-z0-9]', '', name.lower())}:{raw}"
    return None


# --------------------------------------------------------------------------
def extract_land(text: str) -> tuple[int | None, str]:
    """Retourne (surface du terrain en m2, texte prive de la mention terrain).

    On retire la mention du terrain avant de chercher la surface habitable,
    sinon '700 m2 de jardin' serait lu comme 700 m2 habitables.
    """
    match = RE_TERRAIN_M2.search(text)
    if match:
        return to_int(match.group(1)), text[: match.start()] + " " + text[match.end():]

    for ares in RE_TERRAIN_ARES.finditer(text):
        # On exige le mot 'terrain'/'jardin' a proximite pour eviter les faux positifs
        window = text[max(0, ares.start() - 40): ares.start()]
        if re.search(r"terrain|jardin|parcelle|grond|tuin", window, re.I):
            centiares = int(ares.group(2)) if ares.group(2) else 0
            return int(ares.group(1)) * 100 + centiares, text[: ares.start()] + " " + text[ares.end():]

    return None, text


def parse_email(raw_html: str) -> list[Listing]:
    soup = BeautifulSoup(raw_html, "html.parser")
    found: dict[str, Listing] = {}

    for anchor in soup.find_all("a", href=True):
        url = clean_url(anchor["href"])
        ident = identify(url)
        if not ident:
            continue
        portal, listing_id = ident
        if listing_id in found:
            continue

        # Remontee du bloc contextuel, en s'arretant des qu'il contient une
        # seconde annonce : sinon on melangerait le prix d'une annonce avec la
        # surface de la suivante.
        block = anchor
        for _ in range(5):
            parent = block.parent
            if parent is None:
                break
            siblings = sum(1 for a in parent.find_all("a", href=True) if identify(clean_url(a["href"])))
            if siblings > 1:
                break
            block = parent
            if len(block.get_text(" ", strip=True)) > 70:
                break
        text = block.get_text(" ", strip=True)

        price = to_int((RE_PRICE_A.search(text) or [None, None])[1] if RE_PRICE_A.search(text) else None)
        if price is None:
            match_b = RE_PRICE_B.search(text)
            price = to_int(match_b.group(1)) if match_b else None

        land, rest = extract_land(text)

        hab_match = RE_HAB_LABEL.search(rest) or RE_ANY_M2.search(rest)
        living = to_int(hab_match.group(1)) if hab_match else None

        beds = RE_BEDROOMS.search(text)
        baths = RE_BATHROOMS.search(text)
        facades = RE_FACADES.search(text)
        peb = RE_PEB.search(text)
        # L'URL prime sur le texte : elle n'est pas polluee par les surfaces.
        zip_url = RE_ZIP_URL.search(url)
        zipc = zip_url or RE_ZIP.search(rest)

        if price is None and living is None and land is None:
            continue  # lien de service (compte, desinscription), pas une annonce

        found[listing_id] = Listing(
            id=listing_id,
            portal=portal,
            url=url,
            title=(anchor.get_text(" ", strip=True) or text[:90])[:140],
            price=price,
            living_area=float(living) if living else None,
            land_area=land,
            bedrooms=int(beds.group(1)) if beds else None,
            bathrooms=int(baths.group(1)) if baths else None,
            facades=int(facades.group(1)) if facades else None,
            peb=peb.group(1).upper() if peb else "",
            zipcode=zipc.group(1) if zipc else "",
        )

    return list(found.values())


# --------------------------------------------------------------------------
def in_zone(zipcode: str, ranges: list, excluded: list | None = None) -> bool:
    if not zipcode:
        return True
    code = int(zipcode)
    for entry in excluded or []:
        if isinstance(entry, list):
            if entry[0] <= code <= entry[1]:
                return False
        elif code == int(entry):
            return False
    if not ranges:
        return True
    for entry in ranges:
        if isinstance(entry, list):
            if entry[0] <= code <= entry[1]:
                return True
        elif code == int(entry):
            return True
    return False


def apply_criteria(listings: list[Listing], crit: dict) -> tuple[list[Listing], dict]:
    """Regle de conduite : on ne rejette que sur une donnee lue.
    Une information absente produit un drapeau 'a verifier', pas une exclusion.

    Retourne aussi le decompte des rejets par motif. Chaque annonce est
    imputee au PREMIER critere qui la recale, pour repondre a la question
    'lequel de mes criteres me coute le plus d'annonces'.
    """
    kept: list[Listing] = []
    rejects: dict[str, int] = {}
    souples = set(crit.get("soft", []))

    def reject(reason: str) -> None:
        rejects[reason] = rejects.get(reason, 0) + 1

    def controle(item: Listing, cle: str, valeur, seuil, motif: str, ecart: str) -> bool:
        """Retourne True s'il faut rejeter. Un critere declare souple ne
        rejette jamais : il depose une pastille d'ecart sur la fiche."""
        if valeur is None or not seuil or valeur >= seuil:
            return False
        if cle in souples:
            item.misses.append(ecart)
            return False
        reject(motif)
        return True

    for item in listings:
        if item.price is not None and crit.get("price_max") and item.price > crit["price_max"]:
            reject("budget"); continue
        if item.price is not None and crit.get("price_min") and item.price < crit["price_min"]:
            reject("budget"); continue
        if not in_zone(item.zipcode, crit.get("zipcode_ranges", []),
                       crit.get("zipcode_exclude", [])):
            reject("hors zone"); continue

        if controle(item, "bedrooms_min", item.bedrooms, crit.get("bedrooms_min"),
                    "chambres", f"{item.bedrooms} ch."):
            continue
        if controle(item, "bathrooms_min", item.bathrooms, crit.get("bathrooms_min"),
                    "salle de bain", f"{item.bathrooms} sdb"):
            continue
        if controle(item, "facades_min", item.facades, crit.get("facades_min"),
                    "façades", f"{item.facades} façades"):
            continue
        if controle(item, "land_min", item.land_area, crit.get("land_min"),
                    "terrain", f"terrain {item.land_area} m²"):
            continue

        low = item.title.lower()
        if any(word.lower() in low for word in crit.get("exclude_keywords", [])):
            reject("travaux / mot exclu"); continue

        if item.land_area is None and crit.get("land_min"):
            item.unknown.append(f"terrain ({crit['land_min']} m² min)")
        if item.facades is None and crit.get("facades_min"):
            item.unknown.append(f"façades ({crit['facades_min']} min)")
        item.unknown.extend(crit.get("always_verify", []))

        if item.price and item.living_area and item.living_area > 20:
            item.price_per_sqm = round(item.price / item.living_area)
            ref = crit.get("reference_price_per_sqm")
            if ref:
                delta = (item.price_per_sqm - ref) / ref
                if delta <= -0.05:
                    item.flags.append("sous la référence")
                elif delta >= 0.10:
                    item.flags.append("au-dessus du marché")
        kept.append(item)

    return kept, rejects


def load_store() -> dict:
    """Le magasin garde chaque annonce vue, avec sa date de premiere apparition.

    Sans lui, la page ne pourrait montrer que les annonces du matin meme :
    les emails d'hier ne reviennent pas.
    """
    path = DATA / "store.json"
    if not path.exists():
        # Reprise de l'ancien format (identifiant -> date), sans les donnees.
        legacy = DATA / "history.json"
        if legacy.exists():
            try:
                old = json.loads(legacy.read_text())
                return {k: {"first_seen": v, "data": {}} for k, v in old.items()
                        if isinstance(v, str)}
            except Exception:
                pass
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def update_store(store: dict, fresh: list[Listing], today: str, keep_days: int) -> dict:
    """Ajoute les annonces du jour et purge celles qui depassent la fenetre."""
    for item in fresh:
        entry = store.get(item.id)
        data = {k: getattr(item, k) for k in BASE_FIELDS}
        if entry:
            entry["data"] = data          # on rafraichit prix et libelle
        else:
            store[item.id] = {"first_seen": today, "data": data}

    limit = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=keep_days - 1)).date()
    return {
        key: val for key, val in store.items()
        if val.get("data") and
        datetime.strptime(val["first_seen"], "%Y-%m-%d").date() >= limit
    }


def store_to_listings(store: dict, today: str) -> list[Listing]:
    """Reconstruit les objets, en calculant l'age de chaque annonce en jours."""
    now = datetime.strptime(today, "%Y-%m-%d").date()
    out = []
    for key, val in store.items():
        data = {k: v for k, v in val["data"].items() if k in BASE_FIELDS}
        if not data.get("id"):
            continue
        item = Listing(**data)
        item.first_seen = val["first_seen"]
        item.age_days = (now - datetime.strptime(val["first_seen"], "%Y-%m-%d").date()).days
        item.is_new = item.age_days == 0
        out.append(item)
    return out


def fetch_emails(cfg: dict) -> list[str]:
    conf = cfg["imap"]
    user = os.environ.get("IMAP_USER") or conf.get("user", "")
    password = os.environ.get("IMAP_PASSWORD", "")
    host = os.environ.get("IMAP_HOST") or conf["host"]
    if not password:
        sys.exit("IMAP_PASSWORD absent. Definis le secret avant de lancer la collecte.")

    since = (datetime.now(timezone.utc) - timedelta(hours=int(conf.get("lookback_hours", 26))))
    bodies: list[str] = []

    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(user, password)
        imap.select(conf.get("folder", "INBOX"))
        status, data = imap.search(None, f'(SINCE "{since:%d-%b-%Y}")')
        if status != "OK":
            return bodies
        for num in data[0].split():
            # BODY.PEEK[] et non RFC822 : RFC822 marque le message comme LU.
            # Sur une boite personnelle, cela viderait tous les non-lus chaque
            # matin. PEEK lit sans rien modifier.
            status, payload = imap.fetch(num, "(BODY.PEEK[])")
            if status != "OK" or not payload:
                continue

            # iCloud intercale des lignes de service (de simples bytes) entre
            # les vraies reponses (des tuples). On ne garde que les tuples.
            raw = next((part[1] for part in payload
                        if isinstance(part, tuple) and len(part) > 1
                        and isinstance(part[1], (bytes, bytearray))), None)
            if not raw:
                continue

            try:
                msg = email.message_from_bytes(raw)
            except Exception as err:      # un message illisible ne doit pas
                print(f"  message ignore ({err})")  # arreter toute la collecte
                continue

            sender = decode(msg.get("From", "")).lower()
            allowed = conf.get("senders") or []
            if allowed and not any(s.lower() in sender for s in allowed):
                continue
            bodies.append(html_of(msg))
    return bodies


# --------------------------------------------------------------------------
def load_zones() -> list[Path]:
    """Une zone = un fichier zones/*.yaml. En ajouter une : deposer un fichier."""
    return sorted((ROOT / "zones").glob("*.yaml"))


def main() -> None:
    now = datetime.now(BRUSSELS)
    # Passages a 7h, 8h et 9h locales. Le premier attrape les
    # portails matinaux, le second ceux qui envoient plus tard : une alerte
    # arrivee a 7h14 serait perdue jusqu'au lendemain avec un seul passage.
    # Repasser est sans risque, le magasin dedoublonne.
    if os.environ.get("FORCE") != "1":
        heures = {int(h) for h in os.environ.get("SEND_HOURS", "7,8,9").split(",")}
        if now.hour not in heures:
            print(f"Heure locale {now:%H:%M} hors des passages prevus "
                  f"({sorted(heures)}h a Bruxelles), execution ignoree.")
            return

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    today = now.strftime("%Y-%m-%d")

    # La boite est lue et parsee UNE fois, puis filtree par chaque zone.
    raw: list[Listing] = []
    for body in fetch_emails(cfg):
        raw.extend(parse_email(body))
    unique: dict[str, Listing] = {}
    for item in raw:
        unique.setdefault(item.id, item)
    print(f"{len(unique)} annonces distinctes lues dans la boite.")

    DATA.mkdir(parents=True, exist_ok=True)
    keep_days = int(cfg.get("retention_days", 7))
    store = update_store(load_store(), list(unique.values()), today, keep_days)
    pool = store_to_listings(store, today)
    print(f"{len(pool)} annonces dans la fenetre de {keep_days} jours "
          f"(dont {sum(1 for x in pool if x.is_new)} de ce matin).")

    index, sections = [], []
    for path in load_zones():
        zone = yaml.safe_load(path.read_text(encoding="utf-8"))
        crit = zone.get("criteria", {})
        slug = re.sub(r"^\d+-", "", path.stem)

        # deepcopy indispensable : apply_criteria enrichit les objets
        # (drapeaux, prix au m2). Sans copie, la zone 2 heriterait des
        # annotations de la zone 1.
        kept, rejects = apply_criteria(deepcopy(pool), crit)
        # Tri : la fraicheur d'abord, le terrain ensuite. Une annonce du
        # matin passe toujours devant une annonce de mardi, quel que soit
        # son jardin.
        kept.sort(key=lambda x: (x.age_days, -(x.land_area or 0)))

        (DATA / f"{slug}.json").write_text(json.dumps({
            "generated_at": now.isoformat(timespec="minutes"),
            "criteria": crit,
            "count": len(kept),
            "new_count": sum(1 for x in kept if x.is_new),
            "seen": len(unique),
            "retention_days": keep_days,
            "rejected": rejects,
            "listings": [asdict(x) for x in kept],
        }, ensure_ascii=False, indent=1), encoding="utf-8")

        # Le digest ne liste que les nouveautes : recevoir chaque matin les
        # memes annonces que la veille userait l'attention en une semaine.
        sections.append((crit.get("label", slug), crit.get("subtitle", ""),
                         [asdict(x) for x in kept if x.is_new],
                         crit.get("land_min", 0)))

        index.append({"slug": slug, "label": crit.get("label", slug),
                      "subtitle": crit.get("subtitle", ""),
                      "count": len(kept),
                      "new_count": sum(1 for x in kept if x.is_new)})

        print(f"  [{crit.get('label', slug)}] {len(kept)} retenues, "
              f"{sum(1 for x in kept if x.is_new)} nouvelles")
        for reason, n in sorted(rejects.items(), key=lambda kv: -kv[1]):
            print(f"      recalees sur {reason} : {n}")

    (DATA / "store.json").write_text(json.dumps(store, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    (DATA / "zones.json").write_text(json.dumps(
        {"generated_at": now.isoformat(timespec="minutes"), "seen": len(unique),
         "retention_days": keep_days, "zones": index},
        ensure_ascii=False, indent=1), encoding="utf-8")

    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]
    date_label = f"{jours[now.weekday()]} {now.day} {mois[now.month - 1]}"

    # Garde-fou : deux passages quotidiens ne doivent pas produire deux mails.
    marque = DATA / "last_digest.txt"
    deja_envoye = marque.exists() and marque.read_text().strip() == today

    try:
        if deja_envoye:
            print("Digest deja envoye aujourd'hui, second passage silencieux.")
        else:
            send_digest(sections, cfg, cfg.get("page_url", ""), date_label)
            if (cfg.get("notify") or {}).get("enabled"):
                marque.write_text(today)
    except Exception as err:
        # Un echec d'envoi ne doit pas faire echouer la collecte : les donnees
        # sont deja ecrites et la page reste a jour.
        print(f"Envoi du digest impossible : {err}")


if __name__ == "__main__":
    main()
