# 3615 rose — instructions de personnage

Révision du 17 octobre 2026, pour le prototype personnel. Les formulations
ci-dessous sont des choix éditoriaux provisoires à éprouver ; la recherche
n'établit pas qu'un prompt produit le comportement attendu.

## Répartition

| Élément | Responsabilité |
|---|---|
| `config/rose_prompt.txt` | Français, format court, cadre de conversation et comportement commun |
| `voice` dans les catalogues | Tempérament, style et façon d'écouter du personnage |
| `kinks` dans les catalogues | Liste d'identifiants de modules ; `[]` pour aucun |
| `config/rose_kinks.json` | Banque éditable ; chaque entrée a un label, une catégorie et une instruction de 1–2 phrases |
| `asexual: true` | Charge uniquement `config/rose_asexual_prompt.txt` ; ce n'est pas une entrée de la banque de kinks |
| Python | Porte, routage, historique transitoire, sorties, mot d'arrêt, facture et isolation réelle |

Le message `system` envoyé à chaque appel est : prompt commun + voix et genre
du profil + nom affiché + instructions sélectionnées. Labels et catégories
restent des métadonnées éditoriales locales. Le modèle ne reçoit ni la banque
complète, ni biographies, tarifs, mot d'arrêt, contexte domestique ou monde
interne du Minitel. Le classificateur de prénom reste indépendant.

Les modules utilisent le même rôle `system` que le texte commun : ce découpage
est une organisation des fichiers, pas une hiérarchie supplémentaire imposée
par l'API. Les instructions doivent donc rester compatibles. Le prompt commun
est relu à chaque requête ; le profil et ses modules sont figés à l'entrée en
session. Ouvrir une nouvelle session après une modification des modules.
Une liste absente équivaut à `[]` ; l'ordre est conservé et les doublons sont
supprimés. Un identifiant inconnu, un type incorrect ou un fichier sélectionné
illisible suspend localement la mise en relation sans conversation ni facture.

Exemple de configuration, sans nouvel appel API :

```json
{"asexual": false, "kinks": ["uniforms", "praise"]}
```

Les deux profils asexuels restent chaleureux et romantiques, avec leur limite
verbale propre déjà décidée pour ce prototype. Cette limite de personnage ne
prétend pas décrire toutes les personnes asexuelles. Si de futurs modules se
contredisent, résoudre leur contenu avant le playtest ; aucune priorité magique
entre modules n'est ajoutée.

## Catalogue initial

Les instructions exactes de 1–2 phrases sont dans
[`config/rose_kinks.json`](../config/rose_kinks.json), source unique de rédaction.
Les catégories servent à distinguer des notions de nature différente, sans
échelle de gravité, diagnostic ou prétention à une taxonomie universelle.

| Identifiant | Objet | Catégorie éditoriale | Affectation actuelle |
|---|---|---|---|
| `large_breasts` | Préférence d'apparence | Apparence | Non affecté |
| `dirty_talk` | Registre direct et familier | Langage | Non affecté |
| `uniforms` | Uniformes | Vêtement | Brigitte |
| `light_spanking` | Fessée légère, mention sans scène | Activité | Non affecté |
| `lingerie` | Lingerie et tissus | Vêtement | Non affecté |
| `bdsm` | Jeux de rôle et conventions négociées | Ensemble de pratiques | Non affecté |
| `bondage` | Cordages et nœuds | Activité | Armand |
| `foot_fetish` | Pieds et chaussures | Centre d'intérêt corporel | Lucien |
| `roleplay` | Circonstances et rôles imaginaires | Interaction | Non affecté |
| `praise` | Compliments et encouragements | Langage | Non affecté |
| `leading_conversation` | Guider l'échange | Interaction | Solange |
| `guided_conversation` | Se laisser guider | Interaction | René |
| `firm_feedback` | Réparties fermes | Interaction | Gertrude |

Jacqueline et Jean-Marc restent vanilla avec `kinks: []`. Bertrand et Mireille
ont aussi une liste vide, avec leur module asexuel distinct. Les profils de la
grille de test restent vierges de modules. Les affectations reprennent les motifs
déjà présents dans les voix ; aucune préférence supplémentaire n'est imposée
à un profil existant. Biographies et frais restent inchangés.

**Limite de cette livraison :** le propriétaire souhaite un modèle ouvert à la
description explicite d'actes sexuels et du corps. Le 16 octobre au soir
(commit `43f1093`), il a lui-même modifié `config/rose_prompt.txt` en ce sens ;
ce texte est conservé au contenu près, ses coquilles de saisie mobile étant
corrigées à part sans changer le sens, et sans validation de comportement ni
réécriture par l'assistant. Les rédactions fournies par l'assistant (banque,
voix, module asexuel, répliques mock) restent des indices de personnage non
graphiques : l'assistant ne fournit pas de consignes de génération érotique
explicite. Cela n'établit aucune limite empirique de Mistral, ni sa politique,
ni le comportement des Guardrails personnalisés (optionnels, non envoyés par
ce prototype). Le prompt commun édité contredit frontalement le module asexuel
chargé pour Bertrand et Mireille ; le programme n'arbitre pas cette
contradiction, à trancher après essai. Les noms des entrées ne garantissent
pas non plus leur interprétation ; les formulations et la notion de vulgarité
restent à éprouver avec le modèle configuré.

## Recherche et portée

- [Mistral — Prompting](https://docs.mistral.ai/inference/prompting) : le rôle
  system porte les instructions générales ; structure claire, absence de
  contradictions et évaluation itérative recommandées. Notre composition de
  fichiers est un choix d'implémentation compatible avec ce mécanisme, pas une
  fonction spéciale « persona » ou « kinks » du fournisseur.
- [Dunkley et Brotto — The Role of Consent in the Context of BDSM](https://pubmed.ncbi.nlm.nih.gov/31010393/),
  DOI `10.1177/1079063219842847`, résumé consulté : BDSM est un terme collectif
  et le consentement mutuel éclairé est central dans cette discussion. Cela
  justifie de ne pas traiter cette entrée comme un synonyme de vêtement ou de
  préférence physique ; aucune consigne de pratique physique n'en est tirée.
- [Scorolli et al. — Relative prevalence of different fetishes](https://doi.org/10.1038/sj.ijir.3901547),
  2007, notice/résumé de recherche : distingue des centres d'intérêt liés au
  corps, aux objets associés et au comportement. Aucun pourcentage ni classement
  de population n'est extrapolé de l'échantillon étudié ; texte intégral non lu.
- [AVEN — General FAQ](https://www.asexuality.org/?q=general.html), extraits
  indexés consultés, page complète inaccessible : attirance romantique et
  attirance sexuelle sont distinctes. Le choix romantique de Bertrand/Mireille
  est une définition particulière des personnages, pas une règle générale.

## Vérification

336 tests offline passent, dont 52 rose, avec Ruff sans erreur — bilan observé
à `70d33b1`, **avant** l'édition du prompt commun par le propriétaire
(`43f1093`), non couverte par ces exécutions. Les tests capturent les requêtes
pour les douze profils : seul le module sélectionné apparaît ; les données
d'application restent absentes. Plusieurs modules, déduplication, alias,
stabilité par session et configuration invalide sont également couverts.
De nouveaux contrôles hors ligne (`tests/test_rose_assets.py`) vérifient que
les lignes structurelles du prompt commun et du module asexuel, ainsi que le
schéma de chaque entrée de la banque, survivent aux éditions manuelles ; ils
n'ont pas pu être exécutés dans l'environnement de rédaction et ne valident
aucun contenu éditorial. Aucun nouvel appel réel Mistral ni essai physique ;
modération, naturel, fréquence des motifs et personnalité restent empiriques.

## Tempo et usure (spécification ; code gelé)

Deux nouveaux actifs éditoriaux, inertes tant que l'orchestrateur ne les
charge pas ; `services/rose.py` n'est pas modifié à ce stade.

**Tempo.** Chaque profil des catalogues porte un champ entier `tempo` :
le nombre de premières réponses de théâtre d'attente avant d'aller droit
au but si l'interlocuteur pousse. `0` signifie l'escalade immédiate —
le maximum au sens de la vitesse — et c'est la valeur des 50 profils de
test de `config/rose_test_personas.json` : la grille éprouve le registre,
pas le rythme. Le catalogue principal échelonne de 2 (Brigitte) à 5
(René, Solange). Le texte de consigne ne figure pas dans les voix :
l'orchestrateur insère `config/rose_tempo_prompt.txt` — ou
`config/rose_tempo_romantic_prompt.txt` pour les profils `asexual` —
après la voix du profil, en substituant `{tempo}`. Spécification pour la
future implémentation : champ absent vaut 0 ; valeur non entière = mise
en relation suspendue localement, sans conversation ni facture, comme un
identifiant de kink inconnu.

**Usure.** `config/rose_weariness_prompt.txt` décrit les fuites
discrètes de l'abstraction : méprises rares, dédain de la situation mais
jamais de la personne, aucun aveu direct, le voile reste beige. Rédaction
non genrée : la personne au poste peut être un homme comme une femme.
Charge future décidée par l'orchestrateur (probabiliste ou après un seuil
de messages) ; aucune probabilité ni horloge n'est fixée à ce stade.

Le champ `tempo` ajouté aux deux catalogues est une clé supplémentaire
ignorée par l'orchestrateur actuel ; les tests n'ont pas été relancés
depuis cet ajout. Relancer `pytest` avant tout test en direct.

## Grille M/F (24 octobre 2026)

La grille de test comporte désormais 50 profils : les 25 cellules E1–E5 ×
S1–S5 existent chacune sous les noms `ME…S…` (homme) et `FE…S…` (femme),
par exemple `ME3S4` et `FE3S4`. Les anciens noms sans préfixe sont retirés ;
aucun alias implicite ne privilégie un genre. Le catalogue normal est inchangé.

Les consignes E/S sont conservées mot pour mot dans les deux variantes.
Seuls les accords des adjectifs, les pronoms et les noms de rôle de la phrase
de caractère sont adaptés lorsque nécessaire. Le niveau explicite demandé,
les biographies, frais, listes de modules et le tempo 0 restent identiques.
Le champ `gender` déjà transmis au modèle porte le genre de la variante ;
aucune nouvelle règle de contenu ni modification du prompt partagé n'est ajoutée.

Le tempo est désormais chargé par l'orchestrateur pour les profils concernés ;
la grille reste sans bloc de tempo. Le module d'usure demeure un actif brouillon
entièrement désactivé. La validation de cette édition est hors ligne et simulée ;
elle ne mesure ni l'accord effectif du modèle ni sa modération.

## Retour de jeu du propriétaire (25 octobre 2026)

Le propriétaire rapporte un essai réel Mistral réussi avec `ME5S5` et `FE5S5`.
Dans les deux genres, les personnages accompagnent la conversation sexuellement
explicite, y compris des descriptions crues d'actes, du teasing érotique et une
légère humiliation réciproque, jugée encore très vanilla. Le résultat dépasse
ses attentes ; aucune différence problématique entre les variantes M/F n'est
signalée. Ce retour fournit une première validation empirique positive du
registre maximal dans la configuration testée.

Il s'agit du compte rendu du propriétaire, distinct des tests automatisés
simulés. Le modèle exact, les paramètres et l'interface de cet essai ne sont
pas précisés. Ce retour n'établit pas le comportement des autres cellules,
des autres modèles ou de toutes les conversations possibles. Aucun verbatim
intime n'est ajouté à la documentation et aucune consigne n'est modifiée.
