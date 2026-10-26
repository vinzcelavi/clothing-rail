# clothing-rail

Animation 3D d'une tringle de sweats à capuche : les sweats pendent de profil,
celui mis en avant pivote face caméra, puis la tringle glisse vers le suivant.
Le tissu suit le mouvement avec un léger retard puis se stabilise. Le rendu est
une séquence PNG transparente (ombres portées comprises) plus une plaque de
fond (le mur seul), à composer dans After Effects, sans génération vidéo par IA.

Les sweats sont construits à partir de photos produit à plat (face et dos) :
la silhouette est détourée, gonflée en volume, sa capuche est rabattue dans le
dos, elle est mise en forme sur un cintre en bois par une simulation de tissu,
puis recolorée par shader. Une seule paire de photos
suffit pour toutes les couleurs.

> Langue : documentation en français, code et commentaires en anglais.

## Prérequis

- Blender 5.2 (`blender` dans le `PATH`)
- [uv](https://docs.astral.sh/uv/) pour la préparation des textures
- ffmpeg pour l'aperçu MP4

## Utilisation

```sh
make stills    # une image par sweat mis en avant, en basse résolution (~30 s)
make preview   # toute l'animation en demi-résolution + renders/preview/preview.mp4 (~1 h)
make render    # plaque + séquence finale dans renders/final/ (plusieurs heures, reprend où elle s'est arrêtée)
```

Chaque commande reconstruit d'abord la scène (`build/rail.blend`) à partir de
`config/rail.json`. La préparation des textures et la mise en forme sur cintre
(`make drape`, environ 30 s) ne sont relancées que si la config, les photos ou
leurs scripts changent. On peut ouvrir ce fichier dans Blender pour inspecter la
scène ou faire défiler l'animation.

## Réglages (`config/rail.json`)

| Clé | Rôle |
| --- | --- |
| `items` | Les sweats mis en avant, dans l'ordre : nom, couleur hex, `heather` (effet chiné de 0 à 1), couleur du logo (`null` = pas de logo) |
| `fillers_before`, `fillers_after` | Sweats de remplissage aux deux bouts de la tringle |
| `fillers_between` | Sweats intercalés entre deux sweats mis en avant (une liste par intervalle) : ils restent de profil et défilent pendant la transition |
| `timing` | Durées en secondes : pause d'intro, rotation, pause sur chaque sweat, sortie ; `slide_per_slot_s` allonge une transition par sweat intercalé traversé |
| `rail.easing` | Courbe des transitions, en `cubic-bezier(x1, y1, x2, y2)` comme en CSS ou dans After Effects ; un `y2` au-dessus de 1 donne un léger dépassement |
| `rail` (reste) | Écartement de profil / de face, écart irrégulier de chaque cintre (`gap_jitter_m`), sens de rotation, fenêtres de rotation et d'écartement, décalage d'entrée et de sortie |
| `sway` | Balancement des cintres sur la tringle, et délai de stabilisation après chaque mouvement (`settle_s`) |
| `fabric` | Ressorts du tissu : buste, bas, torsion, manches (fréquence, amortissement, intensité, angle max) et position des os |
| `output` | Résolution, images/s, fond transparent |
| `render` | Échantillons Cycles, exposition, flou de bouger |
| `garment.logo` | Position et largeur du logo sur la photo de face, en pixels |
| `garment` (reste) | Épaisseur et arrondi du volume, finesse du maillage, nettoyage de la texture photo (bord, éclairage de prise de vue) |
| `drape` | Simulation de mise en forme sur cintre : raideur du tissu, zone d'épinglage ; `enabled: false` garde la pose à plat |
| `hood` | Capuche rabattue et encolure : hauteur de la couture du cou sur la photo, ligne de repli (`stand` : doublure repliée au centre, côtés rabattus en revers sur les bras du cintre), épaisseurs (col, poche), courbe du repli, bas de la capuche vu de dos (`hang`), creux et évasement de l'encolure (`recess`) ; `enabled: false` laisse la capuche dressée |
| `hanger` | Cintre en bois : couleur, dimensions, sommet, pente et galbe des bras, hauteur sur la tringle (`lift_m`) |
| `sleeves` | Poignets en bord-côte : début, largeur, arrondi, bouffant au-dessus |
| `wrinkles` | Pliures légères : intensité, renfort sur les manches, plis diagonaux de manche, longueur d'onde, taille des plaques |
| `variation` | Ce qui change d'un sweat à l'autre : écarts de capuche, épaisseur des manches, nombre de plis, part de manches pliées et leurs angles ; changer `seed` donne une autre tringle |
| `camera`, `lights`, `stage` | Cadrage, éclairage (dont `wash`, qui fait le dégradé du mur), tringle et mur |

Les couleurs des sweats et le dégradé du mur ont été relevés sur la vidéo de
référence, puis recalés par l'exposition (`render.exposure`), la lumière `wash`
et la couleur du mur : le rendu tombe à quelques pourcents près.

Pour vérifier le mouvement sans Blender : `python3 -m rail.motion` affiche la
durée, les angles maximum du tissu, le nombre d'images de pause et l'écart
minimum entre deux sweats voisins (négatif = ils se touchent).

## Logo

Sans fichier `assets/logo.png`, un logo provisoire « TKNP » est généré
(Arial Black incliné). Pour utiliser le vrai logo, déposer un PNG transparent
dans `assets/logo.png` : seule sa transparence compte, la couleur vient de la
config.

## Import dans After Effects

- Importer `renders/final/background.png` : c'est le mur seul, éclairé comme
  l'animation. Il sert de fond fixe sous tout le reste.
- Importer `renders/final/frame_0001.png` en cochant « Séquence PNG », à 30 i/s.
- Couche alpha : « Directe – sans cache » (Straight).
- Placer la séquence au-dessus de la plaque : les ombres portées sont dans
  l'alpha. Les textes et cartes se glissent entre les deux, ou au-dessus.
- Les pauses sont des images identiques (copiées, pas recalculées) : aucun
  scintillement de bruit pendant les arrêts.

## Temps de rendu (MacBook M2, GPU Metal)

- Aperçu (540×960, 16 échantillons) : environ 5 à 10 s par image.
- Final (1080×1920, 96 échantillons) : environ 42 s par image.
- Sur les 623 images, environ 180 sont des copies de pauses ; il en reste
  environ 445 à calculer, soit 5 à 6 h en final. Baisser `render.samples` à 64
  descend vers 3 h 30.
- Le tout premier rendu sur la machine compile les noyaux Metal (environ 2 min).

Le fonctionnement détaillé et les limites actuelles sont décrits dans
[docs/PIPELINE.md](docs/PIPELINE.md).
