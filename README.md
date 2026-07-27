# Veille maison — Wallonie, digest quotidien à 7h

Page statique qui affiche chaque matin les maisons correspondant à tes critères,
en deux zones distinctes. Tourne seule, gratuitement, sans appel à un LLM.

```
Immoweb / Zimmo / Immovlan / … ──alertes email──► thibaultmt@icloud.com
                                                        │
                                    GitHub Actions (cron 7h, Europe/Brussels)
                                                        │
                              collect.py : IMAP → parse → filtre par zone → JSON
                                                        │
                                          GitHub Pages ──► ta page
```

**On ne scrape aucun portail.** Immoweb appartient au groupe Axel Springer, aux
côtés de SeLoger, donc très probablement derrière la même protection anti-bot.
Les alertes email des portails font le travail ; on ne fait que les agréger.

---

## Ce que tu as téléchargé

```
immo-watch/
├── collect.py                    le collecteur — ne pas modifier
├── config.yaml                   connexion à ta boîte mail
├── requirements.txt              dépendances Python
├── README.md                     ce fichier
├── zones/
│   ├── 1-nord.yaml               critères Brabant wallon
│   └── 2-sud.yaml                critères Namurois
├── docs/
│   ├── index.html                la page
│   └── data/                     généré chaque matin (exemples fournis)
└── .github/workflows/daily.yml   le cron
```

Les seuls fichiers que tu auras à rouvrir sont `config.yaml` et `zones/*.yaml`.

---

## Installation, dans l'ordre

### 1. Créer le dépôt

Sur github.com : **New repository**, nom `immo-watch`, visibilité **Public**
(les minutes Actions sont illimitées sur les dépôts publics, plafonnées sur les
privés — tu tomberais en panne au bout de quelques mois sans comprendre pourquoi).
Ne coche aucune option d'initialisation.

### 2. Envoyer les fichiers

**En ligne de commande**, depuis le dossier décompressé :

```bash
cd immo-watch
git init && git add -A
git commit -m "Veille immo"
git branch -M main
git remote add origin https://github.com/TON-PSEUDO/immo-watch.git
git push -u origin main
```

**Piège du glisser-déposer :** le dossier `.github` commence par un point, donc
il est masqué par le Finder (macOS : `Cmd + Maj + .` pour l'afficher) et par
l'explorateur Windows. Un envoi par glisser-déposer sur github.com l'oublie
silencieusement — et sans lui, aucun cron. Si tu passes par le navigateur,
recrée le fichier à la main : **Add file → Create new file**, puis tape
`.github/workflows/daily.yml` dans le champ du nom (les `/` créent les dossiers)
et colle le contenu.

### 3. Enregistrer les identifiants

Sur iCloud, ton mot de passe habituel ne fonctionnera pas : il faut un mot de
passe d'application. appleid.apple.com → **Connexion et sécurité** → **Mots de
passe pour applications** → en générer un, le copier tout de suite (il ne
réapparaît jamais).

Puis dans le dépôt : **Settings → Secrets and variables → Actions →
New repository secret**, deux fois :

| Nom | Valeur |
|---|---|
| `IMAP_USER` | `thibaultmt@icloud.com` |
| `IMAP_PASSWORD` | le mot de passe d'application |

### 4. Autoriser le workflow à écrire

**Settings → Actions → General → Workflow permissions → Read and write
permissions**. Sans ça, la collecte réussit mais le `git push` échoue et la page
ne se met jamais à jour. C'est le blocage le plus fréquent.

### 5. Publier la page

**Settings → Pages** → Source : *Deploy from a branch* → branche `main`, dossier
`/docs` → Save. L'adresse apparaît après une minute :
`https://TON-PSEUDO.github.io/immo-watch/`. Mets-la en favori sur ton téléphone.

### 6. Créer les alertes sur les portails

Une alerte par zone et par portail, toutes vers `thibaultmt@icloud.com`, en
fréquence la plus rapide disponible.

Réglages communs : maison, à vendre, 450 000 € max, 3 chambres min, terrain
500 m² min si le filtre existe, état du bien « bon » / « à emménager » si proposé.

| Zone | Communes à saisir |
|---|---|
| Nord | Ottignies-LLN, Wavre, Chastre, Walhain, Court-Saint-Étienne, Mont-Saint-Guibert, Villers-la-Ville, Genappe, Perwez |
| Sud | Namur, Gembloux, Sombreffe, Floreffe, Fosses-la-Ville, Jemeppe-sur-Sambre, Éghezée, La Bruyère, Andenne |

À vérifier au passage : le filtre **nombre de façades** existe-t-il chez Immoweb ?
Je n'ai pas pu le confirmer. S'il existe, coche 3 et 4 — ça allègera d'autant le
travail du script.

### 7. Premier essai

**Actions → Veille immo quotidienne → Run workflow.** L'exécution manuelle passe
`force=1` et ignore le contrôle d'heure. Ouvre les logs : le nombre d'annonces
lues et le détail des rejets par motif s'y affichent. Puis recharge la page.

---

## Changer d'adresse email

Trois niveaux, du plus rapide au plus permanent :
- secret GitHub `IMAP_USER` (+ `IMAP_HOST` si tu changes de fournisseur)
- champs `imap.user` / `imap.host` dans `config.yaml`
- Gmail `imap.gmail.com` · iCloud `imap.mail.me.com` · Outlook `outlook.office365.com`

## Ajouter ou retoucher une zone

Une zone = un fichier dans `zones/`. Le préfixe numérique fixe l'ordre des
onglets. Dupliquer `2-sud.yaml` en `3-est.yaml`, changer `label`, `subtitle` et
`zipcode_ranges` suffit : la page découvre la nouvelle zone toute seule.

Les critères communs sont **volontairement dupliqués** dans les deux fichiers.
C'est le but de la séparation : pouvoir monter le budget au nord sans toucher au
sud, ou descendre le terrain d'un côté seulement.

## Règle de filtrage

Le script **n'écarte une annonce que sur une donnée qu'il a effectivement lue.**
Une information absente produit un drapeau ambre « à vérifier », jamais une
exclusion. Mieux vaut contrôler trois annonces de trop que jeter la bonne parce
que l'agence n'a pas mentionné le terrain.

## Mesurer avant de retoucher

Chaque exécution compte les annonces écartées **par motif**, imputées au premier
critère qui recale (`budget`, `hors zone`, `chambres`, `façades`, `terrain`,
`travaux / mot exclu`). Le décompte s'affiche en pied de page et dans les logs.

Après deux semaines, le motif en tête de chaque zone te dit lequel de tes
critères te coûte réellement des annonces. Attention à sa portée : il ne compte
que ce qui a franchi le filtre des portails. Une maison écartée par ton alerte
Immoweb n'y apparaîtra jamais.

## Ce que le système ne peut pas faire

| Critère | Pourquoi |
|---|---|
| Cuisine ouverte | Aucun portail ne le filtre, jamais dans l'email. |
| Pas de pierre apparente à l'intérieur | Nécessite de regarder les photos. |
| Environnement calme | Nécessite de croiser l'adresse avec une carte de bruit (E411, N4, ligne 161, aéroport de Charleroi). Projet distinct. |
| Absence de travaux | Filtré sur mots-clés du titre. Une annonce disant « quelques rafraîchissements » passera. |

Ces trois premiers points restent manuels. Le système te fait passer d'une
cinquantaine d'annonces reçues à une dizaine à regarder.

## Limites connues

| Point | Réalité |
|---|---|
| Heure | Le cron GitHub glisse de 5 à 30 min. Compte 7h–7h30. |
| Fraîcheur | Dépend de la fréquence d'envoi des portails, pas du script. |
| Zone nord | Produira peu de résultats à 450 000 €. C'est le marché, pas un bug — et c'est précisément ce que la séparation en deux zones permet de mesurer. |
| Sud de Sombreffe | L'orbite de Charleroi (6000-6299) est exclue, donc le quart sud-ouest du rayon de Sombreffe est neutralisé. |
| Doublons | Dédupliqués par portail + identifiant. Une maison sur Immoweb et Zimmo apparaît deux fois — volontaire, les prix diffèrent parfois. |
| Easyimmo | Ajouté sur demande. Plateforme orientée vendeur : si elle n'envoie pas d'alertes acheteur, le motif ne matchera jamais et ne coûtera rien. |
| Néerlandais | La Flandre est hors périmètre, mais les regex NL (`slaapkamers`, `gevels`, `grond`, `tuin`) restent actives : certaines agences bilingues envoient en NL sur des biens wallons. |
| `reference_price_per_sqm` | À `null`. Tant qu'il l'est, aucun jugement « sous/au-dessus du marché » n'est affiché — un chiffre inventé serait pire que rien. |

## Ajouter un portail ou une agence

Une ligne dans `PORTALS` (collect.py) : nom, motif d'URL, motif d'identifiant.
Trevi, We Invest, Century 21, ERA, Biddit et Easyimmo sont déjà câblés.
