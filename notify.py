#!/usr/bin/env python3
"""
Construit et envoie le digest quotidien par email.

Contrainte : le HTML des emails n'est pas du HTML de navigateur. Pas de flex,
pas de grid, pas de variables CSS, pas de polices distantes, pas de <style>
fiable. On ecrit donc en tableaux avec styles en ligne, comme en 2005 — c'est
la seule maniere d'obtenir le meme rendu sur Mail, Gmail et Outlook.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

# Palette reprise de la page, en valeurs litterales (pas de var CSS en email)
INK = "#0E1826"
INK2 = "#5C6A7A"
INK3 = "#8B97A5"
RULE = "#C0C9D3"
CARD = "#F8F9FB"
STAMP = "#A4392F"
GOOD = "#1C6650"
AMBER = "#8A6414"

SANS = "Helvetica Neue, Helvetica, Arial, sans-serif"
MONO = "SFMono-Regular, Menlo, Consolas, monospace"


def euro(n: int | None) -> str:
    return f"{n:,} €".replace(",", "\u00a0") if n else "prix non lu"


def num(n) -> str:
    return f"{int(n):,}".replace(",", "\u00a0")


# --------------------------------------------------------------------------
def listing_block(item: dict, land_min: int) -> str:
    """Une annonce = un bloc encadre, cliquable, avec le terrain en evidence."""
    known = item.get("land_area") is not None
    short = known and item["land_area"] < land_min

    if known:
        land_txt, land_col = f"{num(item['land_area'])} m² de terrain", (STAMP if short else GOOD)
    else:
        land_txt, land_col = "terrain non précisé", AMBER

    dims = " · ".join(filter(None, [
        f"{item['bedrooms']} ch." if item.get("bedrooms") else None,
        f"{num(item['living_area'])} m² hab." if item.get("living_area") else None,
        f"{item['facades']} façades" if item.get("facades") else None,
        f"PEB {item['peb']}" if item.get("peb") else None,
        item.get("zipcode") or None,
    ]))

    checks = ""
    if item.get("unknown"):
        checks = (f'<div style="font:400 11px {MONO};color:{AMBER};padding-top:8px">'
                  f'À vérifier sur les photos : {", ".join(item["unknown"])}</div>')

    stamp = ('<span style="font:600 10px {m};color:{c};letter-spacing:1.5px">NOUVEAU</span>'
             .format(m=MONO, c=STAMP)) if item.get("is_new") else ""

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="margin:0 0 12px;border:1px solid {RULE};background:{CARD};
              border-left:3px solid {STAMP if item.get('is_new') else RULE}">
  <tr><td style="padding:16px 18px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="font:400 10px {MONO};color:{INK3};letter-spacing:1px">{item.get('portal','')}</td>
        <td align="right">{stamp}</td>
      </tr>
    </table>
    <div style="font:600 17px {SANS};color:{INK};padding:8px 0 6px;line-height:1.25">
      {item.get('title','Annonce')}
    </div>
    <div style="font:600 21px {MONO};color:{INK};padding-bottom:4px">{euro(item.get('price'))}</div>
    <div style="font:400 13px {SANS};color:{INK2};padding-bottom:10px">{dims}</div>
    <div style="font:600 13px {MONO};color:{land_col};padding-bottom:12px">{land_txt}</div>
    <a href="{item.get('url','#')}"
       style="display:inline-block;background:{INK};color:#F8F9FB;text-decoration:none;
              font:600 12px {SANS};letter-spacing:.5px;padding:9px 18px;border-radius:2px">
      Voir l'annonce sur {item.get('portal','le site')}
    </a>
    {checks}
  </td></tr>
</table>"""


def zone_section(label: str, subtitle: str, items: list[dict], land_min: int) -> str:
    if not items:
        return (f'<div style="font:600 20px {SANS};color:{INK};padding:26px 0 4px">{label}</div>'
                f'<div style="font:400 13px {SANS};color:{INK3};padding-bottom:14px">'
                f'Aucune nouvelle annonce ce matin.</div>')

    head = (f'<div style="font:600 20px {SANS};color:{INK};padding:26px 0 2px">{label}</div>'
            f'<div style="font:400 12px {MONO};color:{INK3};padding-bottom:14px">'
            f'{subtitle} — {len(items)} annonce{"s" if len(items) > 1 else ""}</div>')
    return head + "".join(listing_block(i, land_min) for i in items)


def build_html(sections: list[tuple], page_url: str, date_label: str) -> str:
    body = "".join(zone_section(*s) for s in sections)
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#E5E9ED">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#E5E9ED"><tr><td align="center" style="padding:24px 12px 40px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
       style="width:600px;max-width:100%">
  <tr><td style="border-bottom:2px solid {INK};padding-bottom:12px">
    <div style="font:400 11px {MONO};color:{INK2};letter-spacing:2px">VEILLE MAISON</div>
    <div style="font:400 11px {MONO};color:{INK3};padding-top:3px">{date_label}</div>
  </td></tr>
  <tr><td>{body}</td></tr>
  <tr><td style="border-top:1px solid {RULE};padding-top:14px;margin-top:20px">
    <div style="font:400 11px {MONO};color:{INK3};line-height:1.7">
      Toutes les zones et tous les portails :
      <a href="{page_url}" style="color:{INK2}">{page_url}</a><br>
      Une annonce n'est écartée que sur une donnée effectivement lue.
      Les mentions en ambre signalent une information absente de l'alerte,
      pas un défaut du bien.
    </div>
  </td></tr>
</table></td></tr></table></body></html>"""


def build_text(sections: list[tuple], page_url: str, date_label: str) -> str:
    out = [f"VEILLE MAISON — {date_label}", ""]
    for label, subtitle, items, _ in sections:
        out.append(f"== {label} ==")
        if not items:
            out.append("Aucune nouvelle annonce ce matin.\n")
            continue
        for i in items:
            land = f"{num(i['land_area'])} m² terrain" if i.get("land_area") else "terrain non précisé"
            out.append(f"- {i.get('title','')}\n  {euro(i.get('price'))} · {land}\n  {i.get('url','')}")
        out.append("")
    out.append(f"Toutes les zones : {page_url}")
    return "\n".join(out)


# --------------------------------------------------------------------------
def send_digest(sections: list[tuple], cfg: dict, page_url: str, date_label: str) -> None:
    conf = cfg.get("notify") or {}
    if not conf.get("enabled"):
        return

    total = sum(len(items) for _, _, items, _ in sections)
    if total == 0 and not conf.get("send_when_empty", False):
        print("Digest non envoye : aucune nouvelle annonce.")
        return

    user = os.environ.get("SMTP_USER") or os.environ.get("IMAP_USER") or conf.get("from", "")
    password = os.environ.get("SMTP_PASSWORD") or os.environ.get("IMAP_PASSWORD", "")
    recipients = conf.get("to") or []
    if not (user and password and recipients):
        print("Digest non envoye : expediteur, mot de passe ou destinataire manquant.")
        return

    msg = EmailMessage()
    msg["Subject"] = (f"{total} maison{'s' if total > 1 else ''} à voir — {date_label}"
                      if total else f"Veille maison — {date_label}")
    msg["From"] = formataddr(("Veille maison", user))
    msg["To"] = ", ".join(recipients)
    msg.set_content(build_text(sections, page_url, date_label))
    msg.add_alternative(build_html(sections, page_url, date_label), subtype="html")

    host = conf.get("smtp_host", "smtp.gmail.com")
    port = int(conf.get("smtp_port", 465))

    if port == 465:
        with smtplib.SMTP_SSL(host, port) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)

    print(f"Digest envoye a {len(recipients)} destinataire(s) : {total} annonce(s).")
