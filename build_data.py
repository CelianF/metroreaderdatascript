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
import gzip
import json
import re
import sys
import urllib.request

DATASET = "arrets-transporteur"
DATASET_LIGNES = "referentiel-des-lignes"
DATASET_ARRETS_LIGNES = "arrets-lignes"
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
    requete = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(requete, timeout=300) as r:
        brut = r.read()
    # selon le jeu, le serveur répond en gzip qu'on l'ait demandé ou non
    if brut[:2] == b"\x1f\x8b":
        brut = gzip.decompress(brut)
    data = json.loads(brut.decode("utf-8"))
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


def normalise(nom):
    """Clé de rapprochement entre les deux référentiels, qui ne s'accordent ni
    sur la casse, ni sur les accents, ni sur les tirets."""
    import unicodedata
    t = unicodedata.normalize("NFD", nom or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    for c in "-–—'’.":
        t = t.replace(c, " ")
    return " ".join(t.split())


def collect_exploitants():
    """Nom de réseau ou d'exploitant -> société exploitante, depuis le
    référentiel des lignes, seul jeu à publier `operatorname`."""
    index = {}
    for l in fetch(DATASET_LIGNES):
        reseau, societe = l.get("networkname"), l.get("operatorname")
        if not societe:
            continue
        # le jeu des arrêts nomme tantôt le réseau, tantôt la société
        for cle in (reseau, societe):
            if cle:
                index.setdefault(normalise(cle), societe)
    return index


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


def trouve_exploitant(brut, joli, index):
    """Les deux référentiels ne nomment pas toujours le réseau pareil : « BOUCLE
    NORD DE SEINE » d'un côté, « Boucles Nord de Seine » de l'autre. On tente
    l'égalité, puis l'inclusion si elle ne désigne qu'un seul candidat."""
    for cle in (normalise(brut), normalise(joli)):
        if cle in index:
            return index[cle]
    cle = normalise(brut)
    if len(cle) < 10:
        return None
    candidats = {v for k, v in index.items() if cle in k or k in cle}
    return candidats.pop() if len(candidats) == 1 else None


def cmd_providers(args):
    rows = fetch()
    fournisseurs = collect_fournisseurs(rows)
    exploitants = {} if args.no_operators else collect_exploitants()

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
        # l'exploitant vient du référentiel des lignes ; à défaut on garde
        # ce qui avait été saisi à la main
        ancien_nom = existant.get(pid, {})
        entree["operator"] = (trouve_exploitant(nom, entree["network"], exploitants)
                              or ancien_nom.get("operator")
                              or ancien_nom.get("name") or "")
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


MODES = {"bus": "Bus urbain", "metro": "Métro", "tram": "Tramway", "rail": "Train", "metro": "Métro", "tram": "Tramway",
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


def cmd_geo(args):
    """Table de proximité : tous les arrêts, nom et position, sans code billettique.

    Sert uniquement à suggérer un nom quand la carte annonce un identifiant
    inconnu et que l'utilisateur accepte de donner sa position. Comme elle ne
    dépend pas du privatecode, elle couvre aussi les treize réseaux qui n'en ont
    pas déclaré — ceux, précisément, où l'on en a besoin.
    """
    rows = fetch()
    vus, out, ignores = set(), [], 0
    for r in rows:
        types = r.get("arttype") or []
        mode = MODES.get(types[0] if types else "", None)
        pt = r.get("artgeopoint") or {}
        nom = r.get("artname")
        if mode is None or not nom or "lat" not in pt:
            ignores += 1
            continue
        lat, lon = round(pt["lat"], 5), round(pt["lon"], 5)
        # les quais d'un même arrêt font double emploi pour une suggestion
        cle = (nom, mode, round(lat, 4), round(lon, 4))
        if cle in vus:
            continue
        vus.add(cle)
        out.append({"name": nom, "mode": mode, "lat": lat, "lon": lon})
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    import os
    print("%s : %d arrêts, %d ignorés, %.1f Mo"
          % (args.out, len(out), ignores, os.path.getsize(args.out) / 1e6))


def decode_privatecode(code):
    """Le privatecode d'une ligne encode l'exploitant et le numéro de course que
    porte la carte, sur neuf chiffres : EEE SSS CCC. Le dernier tiers est le
    numéro de course, le premier l'exploitant côté IDFM.

    C00139 -> 501501022 : Vexin (fournisseur 501), course 22
    C01374 -> 100110004 : RATP, course 4
    """
    if not code or not str(code).isdigit() or len(str(code)) != 9:
        return None, None
    code = str(code)
    fournisseur = int(code[:3])
    course = int(code[6:])
    # le référentiel des lignes ne numérote pas les exploitants comme celui des
    # arrêts : la RATP y est 100, quand elle est 59 côté arrêts
    if fournisseur == 100:
        return 3, course
    return intercode_id(fournisseur), course


def cmd_lines(args):
    lignes = fetch(DATASET_LIGNES)
    out, ignores = [], 0
    for l in lignes:
        pid, course = decode_privatecode(l.get("privatecode"))
        mode = MODES.get((l.get("transportmode") or "").lower())
        if pid is None or course is None or mode is None:
            ignores += 1
            continue
        out.append({
            "name": l.get("shortname_line") or l.get("name_line") or "",
            "provider_id": pid,
            "line_id": course,
            "public_id": l.get("id_line") or "",
            "mode": mode,
            "background_color": (l.get("colourweb_hexa") or "c5c5c5").lower(),
            "text_color": (l.get("textcolourweb_hexa") or "000000").lower(),
            "is_noctilien": (l.get("shortname_line") or "").upper().startswith("N"),
        })
    ajoutees = 0
    if args.merge:
        with open(args.merge, encoding="utf-8") as f:
            existant = json.load(f)
        # Le fichier en place associe parfois plusieurs numéros de course à une
        # même ligne, ce que le privatecode ne donne pas. On complète donc sans
        # jamais retirer : seules les clés absentes sont ajoutées.
        connues = {(l.get("provider_id"), l.get("line_id"), l.get("mode")) for l in existant}
        nouvelles = [l for l in out
                     if (l["provider_id"], l["line_id"], l["mode"]) not in connues]
        ajoutees = len(nouvelles)
        out = existant + nouvelles

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("%s : %d lignes au total, %d ignorées à la lecture%s"
          % (args.out, len(out), ignores,
             ", %d ajoutées" % ajoutees if args.merge else ""))


def cmd_linestops(args):
    """Table ligne -> arrêts, depuis « Arrêts et lignes associées ».

    Ce jeu associe chaque ligne à ses arrêts par l'identifiant IDFM de la ligne,
    celui que le fichier des lignes appelle public_id. Il ne dépend donc pas du
    code billettique, et couvre les réseaux qui n'en ont pas déclaré.
    """
    rows = fetch(DATASET_ARRETS_LIGNES)
    table, vus, ignores = {}, set(), 0
    for r in rows:
        ligne = (r.get("id") or "").replace("IDFM:", "")
        nom = r.get("stop_name")
        try:
            lat, lon = round(float(r["stop_lat"]), 5), round(float(r["stop_lon"]), 5)
        except (KeyError, TypeError, ValueError):
            ignores += 1
            continue
        if not ligne or not nom:
            ignores += 1
            continue
        # les quais d'un même arrêt font double emploi sur une même ligne
        cle = (ligne, nom)
        if cle in vus:
            continue
        vus.add(cle)
        table.setdefault(ligne, []).append({"name": nom, "lat": lat, "lon": lon})

    for arrets in table.values():
        arrets.sort(key=lambda a: a["name"])

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, separators=(",", ":"))
    import os
    print("%s : %d lignes, %d arrêts, %d ignorés, %.1f Mo"
          % (args.out, len(table), sum(len(v) for v in table.values()), ignores,
             os.path.getsize(args.out) / 1e6))


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
    p.add_argument("--no-operators", action="store_true",
                   help="ne pas interroger le référentiel des lignes")
    p.set_defaults(func=cmd_providers)

    p = sub.add_parser("stations", help="brouillon de NavigoStations.json, incomplet")
    p.add_argument("-o", "--out", default="NavigoStations.json")
    p.add_argument("--force", action="store_true", help="produire malgré l'absence des lignes")
    p.set_defaults(func=cmd_stations)

    p = sub.add_parser("verify", help="vérifie les invariants dont l'app dépend")
    p.add_argument("stations", help="chemin vers NavigoStations.json")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("lines", help="génère NavigoLines.json")
    p.add_argument("-o", "--out", default="NavigoLines.json")
    p.add_argument("--merge", help="NavigoLines.json existant, à compléter sans rien retirer")
    p.set_defaults(func=cmd_lines)

    p = sub.add_parser("linestops", help="génère LineStops.json, arrêts par ligne")
    p.add_argument("-o", "--out", default="LineStops.json")
    p.set_defaults(func=cmd_linestops)

    p = sub.add_parser("geo", help="génère NearbyStops.json, table de proximité")
    p.add_argument("-o", "--out", default="NearbyStops.json")
    p.set_defaults(func=cmd_geo)

    p = sub.add_parser("audit", help="mesure la couverture des codes billettiques")
    p.set_defaults(func=cmd_audit)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
