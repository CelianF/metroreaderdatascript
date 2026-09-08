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

**Refuse de s'exécuter sans `--force`, et c'est volontaire.**
`arrets-transporteur` ne contient aucune information de ligne, donc `line_id` et
`lines` sortiraient vides — ce qui casse le T7 et la recherche d'arrêt par
ligne. Il faut d'abord joindre le jeu
[arrets-lignes](https://data.iledefrance-mobilites.fr/explore/dataset/arrets-lignes/).
En l'état, la commande ne produit qu'un brouillon.

## À faire

- joindre `arrets-lignes` pour peupler `line_id` et `lines`, et lever le garde-fou de `stations`
- établir la correspondance entre les identifiants de ligne IDFM et les numéros de course portés par la carte
- générer `NavigoLines.json`, aujourd'hui non couvert
