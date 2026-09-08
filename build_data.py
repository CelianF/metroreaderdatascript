#!/usr/bin/env python3
"""Génère les tables de données de PassReader depuis l'open data Île-de-France Mobilités.

Le lien entre la puce et l'open data tient en deux règles, établies en comparant
le contenu de cartes réelles au référentiel IDFM :

  - le code arrêt porté par la carte est le champ `privatecode` du jeu
    « Référentiel des arrêts : Arrêts transporteur » ;
  - le code exploitant porté par la carte vaut `fournisseurid - 300` pour les
    réseaux en délégation, et le numéro de DSP vaut `fournisseurid - 500`.
    SNCF et la RATP sont hors DSP : ils portent respectivement 2 et 3 côté
    carte, pour les fournisseurs 1 et 59 côté IDFM.

Attention : IDFM ne renseigne pas `privatecode` pour tous les transporteurs.
Là où l'exploitant ne l'a pas déclaré, le référentiel recopie l'identifiant
technique à 8 chiffres, et les arrêts de ce réseau sont alors introuvables à
partir d'une carte. La commande `audit` mesure l'étendue du problème.

Usage :
    python3 build_data.py providers  [--merge chemin/vers/Providers.json] [-o …]
    python3 build_data.py stations   [-o …]
    python3 build_data.py audit
"""

import argparse
import json
import re
import sys
import urllib.request

DATASET = "arrets-transporteur"
EXPORT_URL = (
    "https://data.iledefrance-mobilites.fr/api/explore/v2.1"
    "/catalog/datasets/{dataset}/exports/json"
)

# Fournisseurs IDFM hors délégation, avec le code qu'ils portent sur la carte.
NON_DSP = {1: 2, 59: 3}

# Sigles qui gardent leur casse dans les noms de réseau.
ACRONYMES = {"RATP", "SNCF", "GPSO", "ADP", "SRL", "GC", "IDF", "TVM"}

# Mots de liaison, en minuscules sauf en tête de nom.
PETITS = {"de", "du", "des", "d", "la", "le", "les", "l", "et", "en", "sur", "au", "aux"}

# Le référentiel est en capitales non accentuées.
ACCENTS = [("Ile-de-France", "Île-de-France"), ("Vallee", "Vallée"), ("Bievre", "Bièvre"),
           ("Evry", "Évry"), ("Velizy", "Vélizy"), ("Senart", "Sénart"),
           ("Essone", "Essonne"), ("Coeur", "Cœur"), ("Aeroport", "Aéroport")]


def fetch(dataset=DATASET):
    """Télécharge un jeu complet. L'export ne demande ni clé ni pagination."""
    url = EXPORT_URL.format(dataset=dataset)
    print("téléchargement de %s…" % dataset, file=sys.stderr)
    with urllib.request.urlopen(url, timeout=300) as r:
        data = json.load(r)
    print("  %d enregistrements" % len(data), file=sys.stderr)
    return data


def intercode_id(fournisseur):
    """Code exploitant tel que la carte le porte, ou None si inconnu."""
    if fournisseur in NON_DSP:
        return NON_DSP[fournisseur]
    if 500 < fournisseur < 600:
        return fournisseur - 300
    return None


def dsp_number(fournisseur):
    """Numéro de lot de la délégation de service public, ou None."""
    if 501 <= fournisseur <= 560:
        return fournisseur - 500
    return None


def pretty(name):
    """« CENTRE ET SUD YVELINES » → « Centre et Sud Yvelines »."""
    morceaux = re.split(r"([ \-'’–])", name)
    sortie, premier = [], True
    for m in morceaux:
        if not m or m in " -'’–":
            sortie.append(m)
            continue
        bas = m.lower()
        if m.upper() in ACRONYMES:
            sortie.append(m.upper())
        elif not premier and bas in PETITS:
            sortie.append(bas)
        else:
            sortie.append(bas[:1].upper() + bas[1:])
        premier = False
    texte = "".join(sortie)
    for brut, accentue in ACCENTS:
        texte = texte.replace(brut, accentue)
    return texte


def collect_fournisseurs(rows):
    """fournisseurid -> (nom, nombre d'arrêts, nombre de privatecode exploitables)."""
    out = {}
    for r in rows:
        fid = r.get("fournisseurid")
        if fid is None:
            continue
        fid = int(fid)
        nom = r.get("fournisseurname") or ""
        code = str(r.get("privatecode") or "")
        # un privatecode utilisable est un petit entier : au-delà, le référentiel
        # a recopié l'identifiant technique faute de code déclaré
        ok = code.isdigit() and int(code) < 100000
        n, k = out.get(fid, (nom, 0, 0))[1:], None
        cur = out.setdefault(fid, [nom, 0, 0])
        cur[1] += 1
        cur[2] += 1 if ok else 0
    return {k: tuple(v) for k, v in out.items()}


def cmd_providers(args):
    rows = fetch()
    fournisseurs = collect_fournisseurs(rows)

    existant = {}
    reseaux = []
    if args.merge:
        with open(args.merge, encoding="utf-8") as f:
            ancien = json.load(f)
        existant = {p["id"]: p for p in ancien.get("serviceProviders", [])}
        reseaux = ancien.get("networks", [])

    sortie = {}

    # 1. ce qu'on tire d'IDFM
    for fid, (nom, _, _) in sorted(fournisseurs.items()):
        pid = intercode_id(fid)
        if pid is None or pid in NON_DSP.values():
            continue  # SNCF et RATP gardent leur libellé simple
        entree = {"id": pid, "dsp": dsp_number(fid), "network": pretty(nom)}
        # l'exploitant est saisi à la main : on reprend ce qui existait
        ancien_nom = existant.get(pid, {})
        entree["operator"] = ancien_nom.get("operator") or ancien_nom.get("name") or ""
        if entree["dsp"] is None:
            del entree["dsp"]
        sortie[pid] = entree

    # 2. ce qui n'existe pas côté IDFM garde son libellé
    for pid, p in existant.items():
        if pid in sortie:
            continue
        sortie[pid] = {"id": pid, "name": p.get("name") or p.get("network") or ""}

    providers = [sortie[k] for k in sorted(sortie)]
    doc = {"serviceProviders": providers, "networks": reseaux}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")

    dsp = sum(1 for p in providers if "dsp" in p)
    sans = sum(1 for p in providers if "network" in p and not p["operator"])
    print("%s : %d exploitants, dont %d en DSP, %d sans exploitant renseigné"
          % (args.out, len(providers), dsp, sans))


MODES = {"bus": "Bus urbain", "metro": "Métro", "tram": "Tramway",
         "rail": "Train", "train": "Train", "funicular": "Câble",
         "cablecar": "Câble", "ferry": "Navette fluviale"}


def cmd_stations(args):
    if not args.force:
        sys.exit(
            "Refus : le fichier produit n'est PAS un remplacement de "
            "NavigoStations.json.\n"
            "  arrets-transporteur ne porte aucune information de ligne, donc "
            "line_id et lines\n"
            "  resteraient vides. Or l'app s'en sert :\n"
            "    - le rattrapage du T7 exige des arrêts avec provider_id 59 et "
            "line_id 17\n"
            "      (la carte annonce l'identifiant sans le bit 15, le référentiel "
            "le porte avec) ;\n"
            "    - la recherche d'arrêt tente d'abord une correspondance sur la "
            "ligne.\n"
            "  Il faut joindre le jeu « arrets-lignes » avant que ce soit "
            "utilisable.\n"
            "  Passer --force pour produire quand même un brouillon."
        )
    rows = fetch()
    out, ignores = [], 0
    for r in rows:
        fid = r.get("fournisseurid")
        code = str(r.get("privatecode") or "")
        if fid is None or not code.isdigit():
            ignores += 1
            continue
        pid = intercode_id(int(fid))
        if pid is None:
            ignores += 1
            continue
        types = r.get("arttype") or []
        mode = MODES.get(types[0] if types else "", None)
        pt = r.get("artgeopoint") or {}
        if mode is None or "lat" not in pt:
            ignores += 1
            continue
        out.append({
            "name": r.get("artname") or "",
            "provider_id": pid,
            "line_id": None,
            "location_id": int(code),
            "mode": mode,
            "lat": pt["lat"],
            "lon": pt["lon"],
            "lines": [],
        })
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("%s : %d arrêts écrits, %d ignorés" % (args.out, len(out), ignores))
    print("ATTENTION : le mode vient de arttype, qui ne distingue pas bus urbain "
          "et interurbain, et le tableau lines n'est pas renseigné. À valider "
          "avant de remplacer le fichier de l'app.", file=sys.stderr)


def cmd_audit(args):
    rows = fetch()
    fournisseurs = collect_fournisseurs(rows)
    print("%-6s %-6s %-34s %7s %7s  %s" % ("fourn", "carte", "réseau", "arrêts", "utilis.", "couverture"))
    total_ko = 0
    for fid, (nom, n, ok) in sorted(fournisseurs.items()):
        pid = intercode_id(fid)
        pct = 100.0 * ok / n if n else 0
        if pct < 50:
            total_ko += n - ok
        print("%-6s %-6s %-34s %7d %7d  %5.1f%%%s"
              % (fid, pid if pid is not None else "—", nom[:34], n, ok, pct,
                 "   <-- inexploitable" if pct < 50 else ""))
    print("\n%d arrêts sans code billettique exploitable" % total_ko)


def cmd_verify(args):
    """Vérifie les invariants dont dépend le code de l'app."""
    with open(args.stations, encoding="utf-8") as f:
        st = json.load(f)

    ok = True

    def check(nom, condition, detail=""):
        nonlocal ok
        ok = ok and condition
        print("  %s %s%s" % ("OK  " if condition else "ÉCHEC", nom,
                             ("  — " + detail) if detail else ""))

    # NavigoStations.find mappe la carte vers ces exploitants
    n_sncf = sum(1 for s in st if s.get("provider_id") == 1)
    n_ratp = sum(1 for s in st if s.get("provider_id") == 59)
    check("SNCF sous l'exploitant 1", n_sncf > 0, "%d arrêts" % n_sncf)
    check("RATP sous l'exploitant 59", n_ratp > 0, "%d arrêts" % n_ratp)

    # Rattrapage T7 : la carte annonce l'identifiant sans le bit 15
    t7 = [s for s in st if s.get("provider_id") == 59 and s.get("line_id") == 17]
    check("T7 présent avec line_id 17", len(t7) > 0, "%d arrêts" % len(t7))
    ids = {s["location_id"] for s in t7}
    apparies = sorted(i for i in ids if i >= 0x8000 and (i ^ 0x8000) not in ids)
    check("T7 : identifiants à bit 15 présents",
          any(i >= 0x8000 for i in ids),
          "%d identifiants au-dessus de 0x8000" % sum(1 for i in ids if i >= 0x8000))
    if apparies:
        exemples = ", ".join("%d → %d" % (i ^ 0x8000, i) for i in apparies[:4])
        print("       le XOR 0x8000 rattrape : %s" % exemples)

    # Des line_id tout court, sans quoi la première recherche ne sert à rien
    avec = sum(1 for s in st if s.get("line_id") is not None)
    check("line_id renseignés", avec > 0, "%d arrêts sur %d" % (avec, len(st)))

    print("\n%s" % ("Tous les invariants sont respectés." if ok
                    else "AU MOINS UN INVARIANT EST CASSÉ — ne pas livrer ce fichier."))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("providers", help="génère Providers.json")
    p.add_argument("--merge", help="Providers.json existant, pour garder les exploitants saisis à la main")
    p.add_argument("-o", "--out", default="Providers.json")
    p.set_defaults(func=cmd_providers)

    p = sub.add_parser("stations", help="brouillon de NavigoStations.json, incomplet")
    p.add_argument("-o", "--out", default="NavigoStations.json")
    p.add_argument("--force", action="store_true", help="produire malgré l'absence des lignes")
    p.set_defaults(func=cmd_stations)

    p = sub.add_parser("verify", help="vérifie les invariants dont l'app dépend")
    p.add_argument("stations", help="chemin vers NavigoStations.json")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("audit", help="mesure la couverture des codes billettiques")
    p.set_defaults(func=cmd_audit)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
