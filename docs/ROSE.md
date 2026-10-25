# 3615 rose — prototype personnel

État au 16 octobre 2026, soir : branche `minitel_rose` à `43f1093`, au-dessus
du WIP `70d33b1` (préférences de personnage séparées du prompt commun) et de
l'édition du prompt commun par le propriétaire le même soir (commit du
16 octobre, 23 h 28). Basé sur `origin/develop` à `8f6ceb7`. Aucune fusion ou
déploiement Pi. Usage strictement personnel par le propriétaire. Les invités,
la distribution et une exploitation commerciale sont hors périmètre.

## Sources et décisions

Références : conversation d'origine des 15–16 octobre 2026,
`3615_IA_Minitel_Rose_Draft_v0.1.md` v10,
`3615_IA_Minitel_Rose_Handoff_2026-10-15.md` v8 et
`Minitel_AI_Project.md` v148, lus le 16 octobre. La recherche de conversation
a fourni un résumé des décisions, pas l'export intégral. Cet export reste
nécessaire pour vérifier les nuances absentes des documents ; aucune nouvelle
proposition ci-dessous n'est pas présentée comme une décision acceptée par Julian.
Le laboratoire de profils de rencontre est une autre expérience.

Décisions conservées : mot de passe fixe local, français uniquement,
session séparée et bornée, personnages humains sans connaissance du monde
interne du Minitel (révision du 17 octobre : accrocs informatiques supprimés),
un homme et une femme asexuels et chaleureux, sorties fiables,
facture France Télécom en francs avec équivalent euro et taxe du cul,
sortie immédiate avec amende fictive sur rejet de modération identifiable.

## Lancer l'atelier

Depuis le dépôt, avec le Python de l'installation :

```bash
.venv/bin/python scripts/rose_console.py --set-password
.venv/bin/python scripts/rose_console.py --mock --name Jacqueline
.venv/bin/python scripts/rose_console.py --name Jacqueline
```

La première commande demande deux saisies privées. Elle crée
`config/rose-password.txt`, ignoré par Git, permissions `0600`. Aucun mot de
passe domestique n'a été choisi ou ajouté au dépôt. Une variable locale
`ROSE_PASSWORD`, si définie, prend priorité sur ce fichier ; une valeur vide
verrouille le service. Comparaison exacte, espaces et casse compris, sans
nouvelle ligne. Saisie ASCII, 1–128 caractères ; RETOUR et SOMMAIRE sont
réservés à l'annulation. Ne pas passer le mot de passe sur la ligne de commande.
Le fichier doit être lisible par le compte qui lance le service.

`--mock` utilise quelques répliques écrites, sans API ; cela vérifie le
fonctionnement, pas les personnalités. Sans ce flag, les appels utilisent les
valeurs existantes `MISTRAL_KEY` et `MISTRAL_MODEL` du `.env`/environnement.
Pas de nouveau fournisseur, compte ou clé. Clé absente : service indisponible,
sans appel ni facture. Terminal interactif 40 × 24 minimum ; `ssh -t` si besoin.

Sans `--name`, un accueil composé pour l'écran Minitel apparaît **après** le
mot de passe. Il demande avec qui converser : **Monsieur / Mademoiselle**,
avec **M / F** également acceptés (`Messieur` est aussi reconnu). Ce choix
désigne le correspondant, jamais le genre du participant. Seules les
biographies du genre choisi sont ensuite affichées : deux par page, SUITE
pour avancer, nom directement saisissable à tout moment. Un nom connu de
l'autre liste ne contourne pas le choix ; un alias inconnu est associé au
hasard uniquement à un profil du genre choisi. Les raccourcis nommés directs
(`3615 JACQUELINE`, `--name Jacqueline`) conservent leur accès direct après
le mot de passe, sans sélection redondante.
Jacqueline/Jaqueline et Jean-Marc/Jean Marc sont des alias stables. Un nom
inconnu garde exactement le nom demandé comme alias. Pour une entrée directe
comme `3615 ROBERT` ou `--name Robert`, une petite requête Mistral identifie
l'association usuelle du prénom : `M`, `F` ou `unclear`. Le programme choisit
ensuite au hasard un profil du genre correspondant, fixé pour toute la session.
Le genre du participant n'est jamais inféré du nom.

La requête utilise la clé et le modèle déjà configurés, température 0,
8 tokens maximum, timeout de 8 s maximum, une seule tentative. Elle ne reçoit
que le nom demandé et `config/rose_name_prompt.txt`, aucun mot de passe,
historique, outil ou contexte personnel. Les sorties sont validées strictement ;
un texte explicatif ou mal formé n'est jamais interprété comme une catégorie.
Le prompt vise les prénoms européens courants, avec l'usage français comme
référence lorsqu'il est établi, et permet de signaler une véritable ambiguïté.

`unclear` quitte sans démarrer de personnage ni produire de facture :
`Le nom "<nom saisi>" est introuvable dans le registre central.` Le nom est
repris tel que saisi. Une erreur de service/format et un rejet explicite reçoivent leur
propre message français, sans deviner un genre. RETOUR et SOMMAIRE peuvent
annuler la consultation ; un résultat tardif est écarté et le worker restant
est transmis à la protection contre les requêtes simultanées. La consultation
précède l'ouverture de conversation et ne compte pas comme message envoyé.
Elle ne produit jamais une facture de conversation à elle seule.

Les noms connus du catalogue courant et les choix M/F déjà exprimés dans
l'accueil évitent cet appel supplémentaire. En mode test, la même règle
sélectionne uniquement un profil du catalogue de test. `--mock` ne contacte
aucun fournisseur : quelques labels d'exemple sont écrits localement, les
autres noms donnent `unclear`.

Un nom inconnu a aussi **10 %** de chances de recevoir une réponse de
l'annuaire central : `Dernière connexion : jj/mm/aaaa`, avec une date valide
choisie entre le 1er janvier 1980 et le 31 décembre 1989, puis
`Correspondant indisponible.` Cette branche quitte sans personnage, appel de
classification ou facture. Elle s'applique également aux alias inconnus saisis
dans une liste filtrée et au catalogue de test ; jamais aux noms connus.
Le tirage reste derrière la porte et le contrôle de configuration. La date est
une réplique éphémère, sans état de présence, journal, cache ou mémorisation.

### Catalogue de test séparé

```bash
.venv/bin/python scripts/rose_console.py --test
.venv/bin/python scripts/rose_console.py --test --mock --name "Test A"
```

Dans la console habituelle, la commande **`3615 TEST`** ouvre ce même catalogue.
Elle est reconnue uniquement au prompt ordinaire, avec les mêmes protections
de contexte que `3615`. Le flag `--test` sélectionne seulement
`config/rose_test_personas.json`, qui contient actuellement les stubs **Test A**
et **Test B**. Leur schéma est identique au catalogue principal ; on pourra
les remplacer ou en ajouter sans modifier le programme. Le nombre de pages
s'adapte à la taille du catalogue. La porte, le harness, le fournisseur,
le rendu, l'isolation et les sorties utilisent le même code. En mode test,
la facture est entièrement omise : aucune préparation ni aucun défilement,
y compris après un rejet de modération. La session est effacée et reverrouillée
normalement ; SOMMAIRE réaffiche l'accueil ordinaire. Le mode normal conserve
sa facture.
`--test` conserve les appels Mistral réels ; ajouter `--mock` pour les simuler.
Les entrées ordinaires continuent d'utiliser `config/rose_personas.json`.

Un mauvais mot de passe quitte toujours immédiatement, sans appel modèle,
mais affiche désormais le refus des services centraux d'accréditation :
« L'enthousiasme ne vaut pas habilitation. » Aucun nouvel acquittement requis.

Dans l'application habituelle :

```bash
.venv/bin/python scripts/terminal_console.py
```

Puis `3615 JACQUELINE`, `3615 JEAN-MARC`, `3615 ROBERT` ou `3615` au prompt
ordinaire. Les commandes ne sont pas interceptées dans CALC, PRONOTE,
une composition/révision mail ou une résolution de mémoire en attente.
Le test web d'administration n'expose pas cette fonctionnalité. GUIDE dans
rose montre uniquement son aide locale.

| Action | Console / Minitel |
|---|---|
| Envoyer | Entrée / ENVOI |
| Sortir | Ctrl-R / RETOUR, `RETOUR`, demande d'arrêt reconnue, mot d'arrêt |
| Réinitialiser | Ctrl-P / SOMMAIRE |
| Aide locale | Ctrl-G / GUIDE, `AIDE` |
| Facture : total immédiat | Ctrl-N / SUITE |
| Facture : interrompre | Ctrl-R / RETOUR ou Ctrl-P / SOMMAIRE |
| Fermer la console | Ctrl-Q / Ctrl-C |

Le mot d'arrêt, choisi indépendamment du profil, reste fixe et est ajouté
par le programme à chaque réponse. Saisie entière, casse/espaces ignorés ;
une mention au milieu d'une phrase ne déclenche pas la sortie.
Les demandes d'arrêt usuelles françaises et quelques formes anglaises sont
reconnues localement, sans classification AI supplémentaire. Ce vocabulaire
est explicite et extensible ; ce n'est pas une reconnaissance sémantique de
toutes les paraphrases. RETOUR et le mot d'arrêt restent les sorties garanties.

## Isolation et fonctionnement

- `services/rose.py` : catalogue, état transitoire, frontière modèle injectable,
  worker annulable, conversion et facture. `services/rose_terminal.py` : saisie,
  porte locale, affichage, attente et défilement. L'intégration dans la boucle
  principale intervient avant l'historique, les outils et la journalisation.
- `config/rose_prompt.txt` et `config/rose_personas.json` : rédaction modifiable,
  indépendante des transitions et du calcul. Dix profils, cinq hommes/cinq
  femmes. Bertrand et Mireille remplacent provisoirement Patrick et Colette.
  Jacqueline et Jean-Marc restent vanilla et ouverts aux avances non explicites.
  Les deux profils asexuels restent romantiques et chaleureux par les mots,
  sans toucher, sexe, déshabillage ou promesse d'escalade.
- Révision du 17 octobre : le prompt commun ne décrit plus les capacités
  domestiques absentes, la mythologie du Minitel, le mot d'arrêt ou la facture.
  Ces garanties restent dans Python et dans cette spécification. La voix porte
  le tempérament ; `kinks` sélectionne zéro ou plusieurs instructions dans
  `config/rose_kinks.json`. Le flag `asexual` charge séparément
  `config/rose_asexual_prompt.txt`, exclusivement pour les deux profils concernés.
  Jacqueline, Jean-Marc et les stubs de test ont une liste vide.
  Seuls les modules sélectionnés sont envoyés ; détails, catégories, sources et
  exemple d'édition : [ROSE_PREFERENCES.md](ROSE_PREFERENCES.md).
- Édition du propriétaire du 16 octobre au soir (commit `43f1093`) : le prompt
  commun demande désormais une voix explicitement érotique et des descriptions
  graphiques d'actes sexuels et de tenues. Ce texte est conservé au contenu
  près ; ses coquilles de saisie mobile (`suggstives` → suggestifs,
  `chsudes` → chauds, `erotique` → érotique, `sous vetements` → sous-vêtements,
  double espace) sont corrigées dans un commit séparé sans toucher au sens.
  Sa reformulation n'appartient qu'au propriétaire. Cette édition **n'est pas
  validée** : aucun appel réel ni test de personnalité ne l'a éprouvée, et le
  décompte de 336 tests documenté ci-dessous a été observé avant elle.
- Contradiction apparente non résolue : le prompt commun édité exige des
  descriptions graphiques, alors que le module asexuel de Bertrand et Mireille
  les interdit dans les mêmes payloads. Le programme n'arbitre pas ; le module
  suit simplement le prompt commun dans le message system, et la documentation
  Mistral recommande d'éviter les contradictions. À éprouver, puis à trancher
  par le propriétaire (par exemple en réservant la consigne graphique aux seuls
  profils non asexuels).
- Les rédactions fournies par l'assistant (voix, banque de modules, module
  asexuel, répliques mock) restent non graphiques : limite de l'assistant
  auteur, pas constat sur Mistral. Aucun résultat de modération, limite
  empirique ou politique du fournisseur n'est déduit de ce choix ni d'une
  réponse obtenue. Les Guardrails personnalisés Mistral restent des réglages
  de modération d'entrée optionnels ; ce prototype n'en envoie aucun et ne
  fait aucun appel de modération séparé.
- La langue ordinaire n'est jamais modifiée. Porte, aide, erreurs, dialogue
  demandé au modèle et facture restent français. Le respect linguistique et
  littéraire du modèle reste à éprouver ; aucune détection linguistique factice.
- Aucun outil, recherche web, mémoire générale, courrier, PRONOTE, imprimante,
  collection de profils ou contexte personnel n'est transmis au modèle rose.
  Pas de conversation serveur reprise ni de journal intime local automatique.
  La requête ordinaire de chat n'ajoute aucune garantie sur la rétention du
  fournisseur. La politique de rétention future n'est pas arrêtée.
- Historique limité à 24 messages / 8 000 caractères, saisie à 800 caractères,
  génération à 180 tokens et réponse à 700 caractères. Rendu sûr sur 39 colonnes,
  au plus 20 lignes ; une réponse trop longue est explicitement abrégée.
- Mot de passe exclu des payloads, historiques, journaux et impression. L'écho
  console est supprimé. Sur UART, les aiguillages clavier→écran et clavier→modem
  sont interrogés puis coupés avec acquittement avant la demande de mot de passe.
  Leurs états précédents sont restaurés à la sortie, même après annulation ou
  erreur. L'écho local normal passe par le modem bouclé : activer en plus le chemin
  direct clavier→écran doublait chaque caractère après la porte. Sans réponse
  protocole valide, la porte refuse de demander le mot de passe. Les réponses
  de status ne deviennent pas des frappes. Référence :
  [STUM, protocole et aiguillages](https://jbellue.github.io/stum1b/#le-protocole).
  Après mise à jour, éteindre/rallumer une fois le Minitel pour effacer l'aiguillage
  erroné laissé par l'ancien code. Les tests simulent les deux chemins d'écho ;
  vérifier physiquement le masquage, les sélections et le retour au prompt normal.
- Sortie : verrouillage et effacement de l'historique avant la facture.
  Aucun worker ne modifie l'état ; les réponses tardives sont écartées.
  Une requête déjà envoyée peut continuer chez le fournisseur après l'arrêt.
  Le programme empêche d'en empiler une autre tant que ce worker tourne.
  Redémarrage, échec ou annulation de porte ne mémorisent aucun déverrouillage.

## Facture et modération

Tarifs **provisoires** : 180 F de frais fixes répartis, deux suppléments de
personnage totalisant 21 F, plus 3,50 F par message utilisateur soumis à la
frontière modèle. Exemple à trois messages : **211,50 F / 32,24 EUR**.
Un message dont l'envoi est tenté puis échoue ou est rejeté compte ; les
ouvertures générées, mots de passe, aides, pagination, saisies vides, sorties et
entrées ignorées pendant une attente ne comptent pas. Ce décompte n'est pas une
mesure de tokens, de livraison ou de facturation réelle du fournisseur.

Arithmétique entière en centimes de francs ; conversion finale avec Decimal,
diviseur exact `6.55957`, un arrondi à deux décimales. Les références compactes
197 F → 30,03 EUR et 218 F → 33,23 EUR sont testées séparément.
Pas de frais de progression inventés : les suppléments sont fixes. Un
forfait de disponibilité du mot d'arrêt n'affirme pas qu'il a été utilisé.

59 lignes normalement, 62 avec amende ; environ trois écrans. Délai ajouté
provisoire 0,32 s/ligne, doublé aux blancs, environ 23 s avant le bloc final
dans la console, auquel s'ajoute le transport sur UART. SUITE saute au total,
RETOUR/SOMMAIRE interrompent. Le bloc final est repositionné en haut et reste
visible avec le prompt suivant ; même un SOMMAIRE n'efface pas immédiatement
la facture pour un nouvel accueil. La console isolée attend ensuite ENVOI
pour fermer son aperçu ; l'application ordinaire n'ajoute pas cet arrêt.

Amende provisoire : **500 F**, `Amende pour propos hors forfait`. Elle nécessite
le rejet structuré Mistral documenté : HTTP 403, résultats `moderation_llm_v2`,
action `block`, violation globale et au moins une catégorie `violated: true`.
Une simple phrase de refus reste une réponse ; 403 générique, authentification,
réseau, limite de débit, erreur de modération (`3201`) ou format ambigu donnent
une erreur récupérable, sans amende. Aucun nouveau seuil/guardrail, appel de
classification ou retrait de restriction fournisseur n'est ajouté. Le mapping
est testé sur un **fixture synthétique documenté**, pas une capture réelle.
Rejet identifiable : pas de réponse de personnage ni retry, directement la
facture ; aucun texte déclencheur reproduit. Tout montant est une fiction,
sans action de paiement ou dette réelle.

Sources techniques vérifiées le 16 octobre :
[Mistral : guardrails et formats d'erreur](https://docs.mistral.ai/studio/safety-moderation),
[chat completions](https://docs.mistral.ai/api/endpoint/chat),
[STUM1B : codes modules/aiguillages](https://jbellue.github.io/stum1b/).
La référence protocole 1B n'établit pas le support exact du Minitel 1 possédé.

## Validation et prochaine séance

Les 284 tests existants passent dans cet environnement avec leurs dépendances.
Les 52 contrôles rose passent : porte exacte/sans écho console,
annulation, dépassement de saisie sans fuite, alias, profil/mot d'arrêt fixes,
état borné, sortie/verrouillage, réponse tardive, deadline, comptage/conversion,
défilement/skip/annulation et langue ordinaire anglaise préservée. Aucun
contrôle simulé ne prouve la qualité des personnalités.

Playtest effectué dans un vrai pseudo-terminal avec `--mock` : mot de passe
invisible, conversation, RETOUR, début du relevé automatique et SUITE vers le
total 204,50 F / 31,18 EUR après un message. Correction de l'ouverture mock
qui répondait initialement à son propre signal d'entrée. Correction aussi du
bloc final qui aurait été effacé par le nouvel accueil après SOMMAIRE.
Un deuxième parcours console avec Mireille a confirmé le bon signal d'ouverture,
une réplique simulée en français à une demande mêlant anglais et intimité,
la sortie par mot d'arrêt avec casse/espaces variés, puis le relevé complet
jusqu'au total. Ce sont des répliques d'exemple écrites, pas une validation
de comportement Mistral. Le pied de console est désormais français pendant
la porte et la conversation, puis son libellé ordinaire est restauré.
Le décompte distingue `1 message`. Les contrôles du catalogue de test couvrent la
sélection indépendante des profils, le flag CLI, la commande `3615 TEST`,
la porte commune et les sorties sans facture, y compris après rejet de
modération, avec effacement de l'état. L'accueil, ses choix M/F, les listes
filtrées, les alias aléatoires respectant le choix et le refus d'accréditation
sont également couverts. Sept contrôles supplémentaires vérifient la petite
requête de prénom, ses labels stricts, la sélection et l'alias, les erreurs,
l'annulation/réponse tardive, le comptage et l'absence d'appel avant la porte.
Bilan combiné : **336 tests passent**. Le tirage 10 %, les bornes des années 80
et la sortie sans appel/facture sont également vérifiés. Les fixtures de prénoms sont synthétiques,
pas une mesure de justesse du classificateur réel. Tester des prénoms européens
courants et ambigus avec le modèle configuré avant de conclure à sa fiabilité.
Trois régressions du 17 octobre vérifient tous les payloads des dix profils et
des deux stubs, la sélection de plusieurs modules sans doublon, l'alias, le
snapshot par session, l'effacement et l'échec local d'un identifiant invalide
sans génération ni facture. Le mock asexuel utilise désormais le module.
Ruff et `git diff --check` passent. Aucune validation réelle des nouveaux prompts.

L'édition du prompt commun du 16 octobre au soir (`43f1093`) est **postérieure**
à ces exécutions : les 336 tests et Ruff ont été observés à `70d33b1`, et cette
édition n'est couverte par aucun de ces essais. La correction orthographique
qui l'accompagne ne change pas le sens et n'est pas non plus validée. De
nouveaux contrôles hors ligne (`tests/test_rose_assets.py`) vérifient seulement
la présence des lignes structurelles du prompt commun et du module asexuel,
ainsi que le schéma complet de la banque de modules, pour qu'une édition
manuelle qui supprimerait une garantie soit vue par la suite ; ils ne valident
ni le contenu érotique ni un comportement de modèle. L'environnement de
rédaction de cette itération n'exécute ni Python ni appel API : relancer la
suite complète sur le dépôt avant toute conclusion.

Retour du propriétaire, 16 octobre, via `terminal_console.py` : boucle globale
fonctionnelle, mot de passe du `.env` appliqué et mauvaise saisie rejetée,
descriptions correctement rendues, entrée en conversation et premières réponses
en personnage. Il s'agit d'un premier essai rapporté par le propriétaire,
sans playtest approfondi ni validation physique. Le modèle/configuration exacts
de cette séance ne sont pas relevés ici. Aucun changement de police n'est
effectué dans cette itération.

Pas de clé Mistral autorisée dans cet environnement ; **aucun appel réel**,
modèle réellement observé, mesure de coût ou rejet réel. Configuration prévue :
modèle existant `MISTRAL_MODEL` (défaut `mistral-small-latest`), température 0,8,
180 tokens, une seule tentative, timeout 30 s maximum. Aucun test physique.

```bash
uv run --locked --all-extras pytest -q
uv run --locked --all-extras ruff check services/rose.py services/rose_terminal.py scripts/rose_console.py tests/test_rose.py tests/test_rose_assets.py
uv run --locked --all-extras ruff check --select F services/minitel_chatgpt.py services/console_terminal.py
```

Avec la configuration autorisée du propriétaire, essayer les deux vanilla et
les deux asexuels : ouverture timide, bravade, sous-entendu plus appuyé, refus
de thème, changement de langue, tutoiement, ambiguïté romantique, avance directe,
question sur la nature du personnage, dix tours et répétition. Évaluer les
réponses effectivement obtenues, sans transformer la séance en recherche de
contournement. Les limites empiriques et la rédaction finale restent à régler
à partir de ces résultats. Sur Minitel : écho, accents/translittération,
touches, annulation pendant requête, débit réel et rythme de la facture.
