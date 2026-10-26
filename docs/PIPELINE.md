# Pipeline

## 1. Préparation des textures (`scripts/prepare_textures.py`)

Entrée : une photo de face et une photo de dos, posées à plat sur fond uni
(`assets/photos/blue-*.png`).

- **Détourage** : le fond est estimé sur les bords de l'image. On mesure
  l'écart de teinte de chaque pixel par rapport au fond plutôt que l'écart de
  couleur brut, ce qui rejette l'ombre portée (une version assombrie du fond).
  La paire bleue est utilisée car son contraste de teinte avec le fond est net ;
  la paire sauge perd le haut de la capuche, trop proche du fond.
- **Carte d'ombrage** : luminance de la photo rapportée à sa propre lumière
  (1 = neutre). On garde la maille, les coutures, les côtes, les cordons et
  les petits plis, mais pas la couleur ni l'éclairage de la prise de vue
  (divisé par un flou de `detail_flatten_px`) : l'éclairage 3D s'en charge, et
  les photos de face et de dos s'accordent. La bande assombrie du bord (le
  tissu qui s'enroule sur la table) est remplacée par le pixel propre le plus
  proche, puis le motif s'efface vers le neutre sur `detail_rim_fade_px` : là
  se rejoignent face et dos, sur la tranche qu'on voit de face quand le sweat
  est de profil.
- **Volume** : la distance au contour donne une hauteur en quart de cercle
  (`round_m`) jusqu'à une épaisseur constante (`thickness_m`) par face. Le
  cercle arrive à la verticale sur le contour : face et dos s'y rejoignent en
  tranche arrondie, sans arête.
- **Grille** : un sommet tous les `grid_step_px` pixels ; les sommets hors
  silhouette sont collés sur le point de contour le plus proche, pour que le
  bord du maillage suive la photo au lieu de la grille. Ces sommets de contour
  sont partagés par les moitiés avant et arrière : la couture est soudée,
  l'ombrage passe d'une face à l'autre sans ligne de démarcation. Un lissage
  de Taubin (sans rétrécissement) régularise ensuite la tranche
  (`rim_smoothing` passes).

## 2. Capuche rabattue et encolure ouverte (`rail/hood.py`)

Sur la photo, la capuche est dressée et l'encolure fermée ; sur un cintre, la
capuche tombe dans le dos, ses côtés restent dressés de part et d'autre du cou
et ses bords avant descendent en V jusqu'aux cordons. Entre les deux, on voit le
cintre et, dessous, l'intérieur du dos.

- **Ligne de repli** (`stand`) : la hauteur où la feuille cesse d'être debout
  et bascule vers l'arrière varie selon la position en travers. Elle suit le
  bord de capuche de la photo :
  - dans l'ouverture du visage (`open_half_width_m`), la doublure se replie
    bas (`center_m` au-dessus de la couture du cou) : il reste l'intérieur du
    dos, avec sa couture, sous le cintre ;
  - à côté, la ligne monte vers les côtés de la capuche (`ramp_m`, 3 cm, en
    direction de `ear_m`) ;
  - le bras du cintre la plafonne : le repli se fait à `cover_m` de son bord
    haut (négatif, donc en dessous, d'à peu près la hauteur du pli
    retourné). Chaque côté se rabat ainsi sur le bras comme un revers : son
    sommet est posé sur le bras là où ils se croisent, à environ 7 cm du
    centre, et le bras entre dans la capuche juste dessous. Ensuite, le revers
    suit le bras jusqu'à l'épaule, un peu plus plein vers l'extérieur
    (`cover_slope`, jusqu'à `cover_max_m`). Le sommet est arrondi sur
    `cap_round_m`. `ear_m` n'est atteint que sans cintre ;
  - le long des bords avant, le repli monte vite et la doublure passe de
    repoussée à repliée en quelques colonnes du maillage, qui se replient à
    des hauteurs très différentes : la crête est lissée après le repli
    (`edge_passes`), sans quoi le tissu se déchire autour du bras ;
  - à partir de `shoulder_from_m`, le repli reste `shoulder_clear_m`
    au-dessus de la ligne d'épaule : seule la capuche se replie, jamais le
    haut de l'épaule voisine (sinon un trou sombre s'ouvre au pied du
    revers) ;
  - à plat, le bas de la capuche déborde sur les épaules au-delà de la
    couture ; une fois pendu, ce débord se range contre le flanc du revers
    (`tuck_from_m`, `tuck_keep`, `tuck_rise_m`) au lieu de se replier en
    aileron au-dessus de l'épaule ;
  - au pied du revers (`level_from_m`, `level_len_m`), le côté de la
    capuche bascule à plat derrière l'épaule, sans crête qui ferait une
    languette ;
  - le dos des côtés dressés prolonge le dos du sweat jusque sous le pli
    (`fill_rise_m`, `fill_fade_m`) : la capuche pendante s'y appuie, sans
    laisser de fente visible de profil.
- **Épaisseurs**, face par face de la feuille :
  - le devant, jusqu'à la ligne de repli, n'est que légèrement aminci
    (`neck_front_scale`) et la capuche démarre à la même épaisseur : elle part
    du devant du sweat, sans fente entre les épaules et le pli ;
  - la face arrière (intérieure une fois repliée) est amincie à une épaisseur
    de tissu (`neck_scale`) et se pose contre le dos ;
  - dans le pli, la face extérieure redescend vite à une épaisseur de tissu
    (`fold_scale` sur `fold_thin_m`) : pli franc et serré, posé sur le cintre,
    au lieu d'un boudin ;
  - la poche suit la distance au bord libre de la capuche
    (`bag_reach_m`, `bag_exponent`, profondeur `bag_m` en mètres) : elle est
    la plus profonde au milieu, le long de la couture centrale, et s'amincit
    vers les bords. En travers, c'est une arête ; de profil, un triangle, le
    plus profond à mi-hauteur et pointu en bas. Elle s'installe sur
    `bag_start_m` + `bag_len_m` après le pli ;
  - la couture centrale pince légèrement l'arête (`seam_depth`,
    `seam_width_m`) ;
  - le bord de la capuche s'arrondit sur `edge_round_m` (1,5 cm) au lieu des
    5 cm du gonflage.
- **Taille** : environ 19 cm de haut, pli compris, et 6,5 cm de profondeur
  derrière le dos à mi-hauteur (`length_scale`, `bag_m`).
- **Texture de la coque** : une fois rabattue, la capuche montre sa coque
  extérieure, couture centrale comprise. À plat, seule la photo de dos la
  montre (celle de face montre la doublure et le bord). Le matériau avant
  passe donc à la photo de dos autour du pli (attribut `hood_shell`,
  `shell_from_m` et `shell_len_m`). Il bascule un peu avant le pli
  (`shell_from_m` négatif) : sur la photo de face, le haut des côtés
  tombe dans les coins de l'ouverture, où la doublure est dans l'ombre.
- **Encolure** (`recess`) : dans le V entre les deux bords de capuche, de
  `depth_m` sous la couture du cou jusqu'à la ligne de repli, la doublure est
  repoussée contre le dos ; les bords de capuche et les cordons restent devant.
  Au-dessus du repli central, le V continue de s'évaser (`flare`, en largeur
  par hauteur, plafonné à `flare_max_m` de plus) : les bords avant montent en
  diagonale sur les côtés, comme des revers, jusqu'au bras du cintre.
  Chaque bord de capuche est un ourlet épais : un bourrelet de `roll_m` vers
  l'avant, à `roll_offset_m` de l'ouverture sur `roll_width_m`, du V jusqu'en
  haut du revers.
- **Repli** : tout ce qui est au-dessus de la ligne de repli (la couture du
  cou, `hinge_px` sur la photo de face, sert de référence) se plie vers
  l'arrière le long d'une courbe dont l'angle croît avec la distance au repli
  (`crease_*`, `fold_*`).
  - Une fois pendante, la capuche repose sur le dos (`rest_gap_m`) : sa face
    intérieure est posée sur la surface réelle du dos drapé, repoussée là où
    elle s'y enfoncerait et ramenée là où elle flotterait (autour des
    épaules, le dos s'incurve vers l'avant). L'épaisseur de cette face est
    lue point par point : elle s'amincit vers les bords de la capuche, qui se
    posent donc aussi sur le dos. Vers ces bords, la capuche s'enfonce même
    légèrement dans le dos (`rest_edge_m`, négatif) : sinon l'interstice sous
    le bord, vu de côté, sort en trait noir, comme une fente entre la capuche
    et le dos.
  - Vu de dos, son bas suit une courbe régulière jusqu'à la couture centrale
    (`hang`) : une parabole `bottom_m` sous la couture du cou au milieu, qui
    remonte de `curve` × x² vers les épaules. Laissée telle quelle, chaque
    colonne pendrait d'autant que la capuche est haute sur la photo au-dessus
    de son repli : les revers, repliés plus haut, laisseraient une encoche de
    chaque côté, et les côtés de la capuche pendraient en pointes. La longueur
    de chaque colonne est donc étirée ou raccourcie pour que son bout (le bord
    de la capuche, lu par tranches de `cell_m`) tombe sur la courbe.
- **Une forme par sweat** (`variation`) : chaque sweat tire ses propres
  écarts autour des réglages de base, de façon reproductible (même `seed`,
  même tringle). La capuche tombe un peu de travers (`shift_m` : glissée sur
  un côté, `drop` : un côté plus bas, `puff` : un côté plus gonflé) et sa
  forme varie (`crease_deg`, `bag_m`, `bag_reach_m`, `seam_depth`).
- La capuche repliée est animée comme le haut du dos sur lequel elle repose.

## 3. Mise en forme sur cintre (`scripts/drape.py`, `rail/drape.py`)

La photo est prise à plat : manches écartées, bas raide. Une simulation de
tissu, lancée une seule fois (environ 30 s), fait pendre le vêtement :

- Un maillage basse résolution (4 700 sommets) de la même silhouette, fermé
  (avant et arrière soudés le long de la couture), est épinglé sur une bande
  sous la ligne d'épaule, là où le cintre le tient. Son profil de bord est plus
  doux que celui du maillage de rendu (il ne fait que le piloter) : des faces
  trop raides empêchent la liaison *Surface Deform*.
- 90 images de gravité sur un cintre immobile. La capuche est épinglée
  pendant la simulation : elle est repliée ensuite, de façon déterministe.
- Le maillage haute résolution suit par un modificateur *Surface Deform*
  (lié une fois, sur la pose à plat). Sa pose finale est enregistrée dans
  `build/textures/drape.npz`.
- À la construction de la scène, cette pose remplace la pose à plat dans le
  maillage partagé ; l'armature est recalée dessus.

Pourquoi pas une simulation à chaque image : testée en laboratoire, elle donne
un beau mouvement, mais la capuche n'est pas contrôlable, le balancement est
trop fort et les pauses ne s'immobilisent jamais d'elles-mêmes (il faut un gel
artificiel qui saute à la reprise). La mise en forme seule apporte l'essentiel
du réalisme ; le mouvement reste sur les ressorts, déterministe et réglable.
`drape.enabled: false` revient à la pose à plat.

## 4. Manches et pliures (`rail/sleeves.py`, `rail/wrinkles.py`)

- **Poignets** (`sleeves`) : sur la photo à plat, la manche finit en boudin.
  Les derniers 11 % de la manche (`cuff_start`) deviennent un bord-côte plus
  étroit (`cuff_scale`) aux bords presque droits (`cuff_round_m`), avec un
  léger bouffant juste au-dessus (`cuff_puff`). L'épaisseur est mise à
  l'échelle autour du plan médian de la manche ; avant et arrière restent
  soudés.
- **Formes de manche** (`variation.sleeves`) : chaque manche a sa propre
  épaisseur (`fullness`). Environ la moitié des sweats ont une manche,
  parfois les deux, très légèrement avancée et pliée au coude
  (`forward_deg`, `elbow_deg`, quelques degrés seulement) ; la pose est
  appliquée comme une peau à deux os (épaule, coude) pour que le tissu reste
  continu.
- **Plis de manche** : chaque manche reçoit 0 à 2 plis larges et doux, en
  diagonale (`sleeve_fold_m`, `sleeve_fold_width_m`), sur la face avant,
  arrière ou les deux. Pas d'ondulations régulières : elles donnaient un effet
  doudoune.

Une fois la forme posée, de légères pliures sont ajoutées le long des normales :
somme d'ondes en crête, presque verticales (un vêtement suspendu plie dans sa
longueur), visibles par plaques (`patch_m`) et non partout. Plus marquées sur
les manches (`sleeve_boost`), absentes du col, de la capuche et du logo. Face et
dos ont leurs propres plis, fondus sur la tranche pour ne pas recréer de
couture. Chaque sweat a son propre tirage.

## 5. Scène (`scripts/build_scene.py`, `rail/scene.py`)

- Un maillage par sweat, issu de la même silhouette : face avant (UV sur la
  photo de face) et face arrière (UV sur la photo de dos, en miroir). Chacun
  porte sa propre capuche, sa pose de manches et ses pliures.
- Matériau par sweat : `couleur × ombrage × moucheture chinée`, plus le logo
  posé par UV sur la poitrine. Principled BSDF très mat avec un léger sheen.
- Hiérarchie par emplacement : `slot` (glisse en X, se balance autour de Y) →
  `hook` (crochet fixe sur la tringle) et `swivel` (pivote autour de Z) →
  `hanger`, `rig` (armature) et `hoodie` (déformé par l'armature), remontés de
  `hanger.lift_m` puisque la capuche ne dépasse plus.
- Cintre en bois : planche à section rectangulaire arrondie, plus haute au
  centre (`height_m`) qu'aux bouts (`tip_height_m`). Bras inclinés (`slope`)
  depuis un sommet arrondi sous le crochet (`apex_z_m`, `crown_radius_m`),
  galbés vers le bas en allant vers les bouts (`curve`) comme un cintre
  contour : ils entrent dans la capuche sous le haut des revers puis passent
  sous les épaules. À partir de `cap_from_m` (sur `cap_len_m`), ils restent
  au moins `drop_m` sous la ligne d'épaule. Le bord haut suit exactement ce
  profil (les sections pendent sous lui). On voit le cintre en V inversé entre
  les revers ; le crochet part du sommet. Hêtre verni : fines
  lignes de veinage dans la longueur des bras (UV `grain`), légères variations
  de ton, vernis satiné.
- Armature tissu : un os fixe pour la capuche et les épaules (tenues par le
  cintre), un os de buste et un os de bas en chaîne, un os par manche depuis la
  couture d'épaule. Les poids sont calculés sur la pose à plat (manches et
  corps y sont séparés), avec des fondus pour éviter les déchirures ; les os
  sont placés là où la mise en forme a emmené leurs points d'attache.
- Mur en *shadow catcher* : avec le fond transparent, il ne garde que les
  ombres, dans l'alpha. Matériau plâtre mat : très légères variations de ton et
  grain fin.
- Éclairage : `key` (face, légèrement à gauche), `fill` (droite), `wash`
  (au-dessus, qui fait le dégradé vertical du mur) et un fond d'ambiance.
- Cycles, gestion des couleurs « Standard » pour que les hex de la config se
  retrouvent tels quels à l'image.
- Les clés d'animation (une par image, linéaires) sont écrites en bloc dans les
  F-curves.

## 6. Chorégraphie (`rail/motion.py`)

Python pur, testable sans Blender : `python3 -m rail.motion`.

- Tout dérive de deux valeurs : la position de défilement `s` (en nombre de
  sweats) et l'intensité de mise en avant `a` (0 = tous de profil). Entre deux
  sweats, `s` suit la courbe `rail.easing` (cubic-bezier).
- La tringle porte, de gauche à droite, `fillers_before`, puis les sweats mis
  en avant (`items`) avec entre deux d'entre eux les sweats intercalés de
  `fillers_between`, puis `fillers_after`.
- Rotation du sweat k : seuls les sweats mis en avant pivotent. Il fait face
  d'autant plus que `|k - s|` est petit (fenêtre `turn_window`). Les sweats
  intercalés restent de profil : pendant une transition, le sortant pivote, le
  portant défile devant eux, puis l'entrant pivote. Une transition dure
  `turn_s`, plus `slide_per_slot_s` par sweat intercalé traversé.
- Position du sweat k : écartement de profil (`profile_gap_m`), chaque cintre
  décalé au hasard d'au plus `gap_jitter_m` (même tirage pour une même
  `variation.seed`), plus une place supplémentaire qui s'ouvre autour du
  centre de l'image (fenêtre `space_window`, un peu plus large pour laisser
  passer les manches). Cette position ne fait qu'augmenter avec `k - s` : tous
  les sweats glissent dans le même sens, sur la même courbe, sans
  arrêt-redémarrage. La place supplémentaire suit donc le défilement : les
  sweats intercalés la traversent, comme des cintres qu'on fait glisser à
  l'endroit où l'on a écarté la tringle.
- Ressorts amortis, pilotés par l'accélération réelle de chaque sweat dans son
  propre repère :
  - balancement du cintre sur la tringle ;
  - buste et bas qui fléchissent (dans le plan et hors du plan) ;
  - torsion : le bas suit la rotation du cintre avec retard ;
  - manches : traînée pendant la rotation, légère ouverture par la force
    centrifuge.
- Chaque ressort est borné en douceur (`max_deg`) et tout s'éteint
  `settle_s` secondes après chaque mouvement : les pauses sont parfaitement
  immobiles, donc copiées au rendu.
- Contrôle anti-collision : `python3 -m rail.motion` mesure l'écart minimum
  entre voisins sur toute l'animation, avec l'emprise au sol d'un sweat
  mesurée sur le maillage (`FOOTPRINT` : corps large et mince, capuche
  étroite et plus profonde). Avec des cintres à 12-16 cm, les capuches
  frôlent le voisin, comme sur un portant bien rempli.

## 7. Rendu (`scripts/render.py`)

- GPU Metal détecté automatiquement.
- Avant de calculer une image, on compare les transformations des objets animés
  et la pose des armatures (y compris la fenêtre de flou de bouger) avec l'image
  précédente ; si rien ne bouge, le PNG précédent est copié.
- `--plate` rend le mur seul, opaque (`background.png`) ; l'aperçu MP4 est
  composé dessus.
- `--resume` garde les images déjà présentes, pour reprendre un long rendu.

## Limites actuelles et pistes

- **Capuche** : forme géométrique fixe (poche lisse, sans plis propres). De
  dos, jamais vu dans l'animation, on verrait la doublure.
- **Tissu procédural** : les ressorts donnent du souple au mouvement (retard,
  torsion, manches qui traînent) mais aucun pli ne se forme pendant le
  mouvement : la forme pendue est fixe, seule l'armature la plie.
- **Manches** : dans la silhouette à plat, elles touchent le corps jusqu'aux
  poignets ; une fois pendues, elles ne peuvent pas s'en écarter beaucoup.
- Pistes, du moins cher au plus réaliste :
  1. Un vrai modèle 3D généré une fois à partir des photos (outils
     image-vers-3D : Hunyuan3D, TRELLIS, Meshy, Tripo), branché à la place du
     maillage gonflé ; matériaux, mise en forme et animation restent valables.
  2. Simulation de tissu à chaque image (le prototype existe), avec gel des
     pauses et capuche épinglée en douceur.
  3. Modèle fait sur mesure dans Marvelous Designer ou CLO3D.
- **Ombres séparées** : sortir les ombres dans une séquence à part donnerait
  le contrôle de leur intensité dans After Effects ; aujourd'hui elles sont
  dans l'alpha de la séquence.
