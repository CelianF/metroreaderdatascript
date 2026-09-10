# metroreaderdatascript

Génération des tables de données de [PassReader](https://github.com/CelianF/metroreader)
à partir de l'[open data Île-de-France Mobilités](https://data.iledefrance-mobilites.fr/).

Aucune dépendance, juste Python 3.

## Le lien entre la puce et l'open data

Une carte Navigo enregistre ses événements avec un code exploitant et un code
arrêt propres à la billettique. Le rapprochement avec le référentiel IDFM a été
établi en comparant des cartes réelles au jeu
[Référentiel des arrêts : Arrêts transporteur](https://data.iledefrance-mobilites.fr/explore/dataset/arrets-transporteur/) :

| Sur la carte | Dans l'open data |
|---|---|
| code arrêt | champ `privatecode` |
| code exploitant | `fournisseurid − 300` pour les réseaux en délégation |
| numéro de DSP | `fournisseurid − 500` |

SNCF et RATP sont hors DSP : elles portent 2 et 3 sur la carte, pour les
fournisseurs 1 et 59 côté IDFM.

La règle se vérifie sur les noms : le fournisseur 526 est « PARIS SACLAY », donc
l'exploitant 226, donc la DSP 26.

## Le piège de l'export

L'API d'export d'Opendatasoft sert **un dump pré-calculé quand l'URL ne porte
aucune clause**, et ce dump retarde de plusieurs jours sans que rien ne le
signale : `cache-control` annonce `no-store`, et le jeu paraît complet.

Au 9 septembre 2026, `arrets-lignes` rendait ainsi 74 422 enregistrements pour
74 294 annoncés au catalogue, avec « Bourg-la-Reine RER » là où le référentiel
disait « Gare de Bourg-la-Reine » depuis le renommage des gares. La moindre
clause — ici `select=*` — force le calcul à la volée et rend le jeu à jour.

`fetch()` porte donc cette clause, et compare systématiquement le nombre
d'enregistrements reçus à celui que le catalogue annonce. Si l'astuce cesse un
jour de marcher, l'écart s'affiche au lieu de passer inaperçu.

## La limite, et elle est sérieuse

**IDFM ne renseigne pas `privatecode` pour tous les transporteurs.** Là où
l'exploitant ne l'a pas déclaré, le référentiel y recopie l'identifiant
technique à huit chiffres, et les arrêts de ce réseau deviennent introuvables à
partir d'une carte.

```
python3 build_data.py audit
```

Au 8 septembre 2026, **13 réseaux en DSP sont à 0 %**, soit 14 959 arrêts sans
code exploitable : Marne et Brie, Paris Saclay, Mantois, Massy–Juvisy, Bièvre,
Croix du Sud, Défense Seine Ouest, Boucle Nord de Seine, Plaine Saint-Denis,
Transdev Ourcq, RATP Cap Boucles de Marne, Pompadour et Seine Orly. Les DSP de
petite couronne, récentes, sont toutes concernées.

Ce n'est pas réparable côté script : c'est un signalement à faire à IDFM ou aux
transporteurs concernés.

## Commandes

### `providers`

Produit `Providers.json` : identifiant carte, numéro de DSP et nom de réseau
pour les 47 délégations, plus les exploitants hors DSP.

```
python3 build_data.py providers --merge ../metroreader/metroreader/Data/Providers.json
```

Le nom de l'exploitant — la société, par opposition au nom du réseau — n'est pas
publié. `--merge` reprend donc ceux déjà saisis dans le fichier existant, et
laisse les autres vides ; l'app affiche alors « Exploitant inconnu ».

### `verify`

Vérifie les invariants dont le code de l'app dépend, avant de livrer un fichier
de stations :

```
python3 build_data.py verify ../metroreader/metroreader/Data/NavigoStations.json
```

Le plus fragile est le **rattrapage du T7** : la carte annonce l'identifiant
d'arrêt sans son bit 15, alors que le référentiel le porte avec, et l'app
rattrape par un XOR `0x8000` réservé à `line_id == 17`. Il exige donc des arrêts
portant `provider_id 59` et `line_id 17`. Un fichier régénéré qui perdrait les
`line_id` casserait silencieusement ces 35 arrêts.

### `stations`

> **Une régénération complète perd de la donnée.** La table livrée à l'app
> contient 9 081 arrêts de bus RATP, dont **3 867 qu'IDFM ne publie plus** —
> sur les 4 169 codes communs, les noms concordent à 98,8 %, donc ces orphelins
> sont bons. Ils sont aujourd'hui recopiés dans `StopCorrections.json`, qui
> survit à la régénération ; ne pas les en retirer. Même remarque pour les
> `line_id` de `NavigoLines.json`, qui ne se dérivent d'aucun champ publié.


**Refuse de s'exécuter sans `--force`, et c'est volontaire.**
`arrets-transporteur` ne contient aucune information de ligne, donc `line_id` et
`lines` sortiraient vides — ce qui casse le T7 et la recherche d'arrêt par
ligne. Il faut d'abord joindre le jeu
[arrets-lignes](https://data.iledefrance-mobilites.fr/explore/dataset/arrets-lignes/).
En l'état, la commande ne produit qu'un brouillon.

## À faire

- `decode_privatecode` lit l'exploitant dans le premier triplet, mais 139 lignes
  du référentiel y portent `000` et le logent dans le deuxième : `000535201`
  vaut 535 − 300 = 235, Mantois. La règle tient sur 138 des 139, les noms
  d'exploitants le confirment. Ces lignes sont aujourd'hui ignorées en silence.
- joindre `arrets-lignes` pour peupler `line_id` et `lines`, et lever le garde-fou de `stations`
- signaler à IDFM les arrêts dont le nom a changé sur le terrain sans changer au
  référentiel : « Pont Royal RER » s'appelle Bagneux RER depuis le prolongement
  de la ligne 4
