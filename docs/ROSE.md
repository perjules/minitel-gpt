# 3615 rose — prototype personnel

État au 16 octobre 2026 : framework implémenté localement sur `minitel_rose`,
basé sur `origin/develop` à `8f6ceb7`. Aucun push, fusion ou déploiement Pi.
Usage strictement personnel par le propriétaire. Les invités, la distribution
et une exploitation commerciale sont hors périmètre.

## Sources et décisions

Références : conversation d'origine des 15–16 octobre 2026,
`3615_IA_Minitel_Rose_Draft_v0.1.md` v10,
`3615_IA_Minitel_Rose_Handoff_2026-10-15.md` v8 et
`Minitel_AI_Project.md` v148, lus le 16 octobre. La recherche de conversation
a fourni un résumé des décisions, pas l'export intégral. Cet export reste
nécessaire pour vérifier les nuances absentes des documents ; aucune nouvelle
proposition ci-dessous n'est présentée comme une décision acceptée par Julian.
Le laboratoire de profils de rencontre est une autre expérience.

Décisions conservées : mot de passe fixe local, français uniquement,
session séparée et bornée, personnages essentiellement humains avec de très
rares accrocs, un homme et une femme asexuels et chaleureux, sorties fiables,
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

Sans `--name`, le catalogue apparaît **après** le mot de passe. Deux biographies
par page, SUITE pour avancer, nom directement saisissable à tout moment.
Jacqueline/Jaqueline et Jean-Marc/Jean Marc sont des alias stables. Un nom
inconnu choisit un profil statique au hasard une seule fois et garde le nom
demandé comme alias. Le genre du participant n'est jamais inféré du nom.

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
  console est supprimé. Sur UART, commande PRO3 clavier→écran OFF puis restauration
  ON ; fonctionnement exact sur le Matra Minitel 1 **non vérifié physiquement**.
  Ne pas conclure que la saisie matérielle est dissimulée à partir du test console.
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
Les 24 premiers contrôles rose passent : porte exacte/sans écho console,
annulation, dépassement de saisie sans fuite, alias, profil/mot d'arrêt fixes,
état borné, sortie/verrouillage, réponse tardive, deadline, comptage/conversion,
défilement/skip/annulation et langue ordinaire anglaise préservée. Aucun
contrôle simulé ne prouve la qualité des personnalités.

Playtest effectué dans un vrai pseudo-terminal avec `--mock` : mot de passe
invisible, conversation, RETOUR, début du relevé automatique et SUITE vers le
total 204,50 F / 31,18 EUR après un message. Correction de l'ouverture mock
qui répondait initialement à son propre signal d'entrée. Correction aussi du
bloc final qui aurait été effacé par le nouvel accueil après SOMMAIRE.

Pas de clé Mistral autorisée dans cet environnement ; **aucun appel réel**,
modèle réellement observé, mesure de coût ou rejet réel. Configuration prévue :
modèle existant `MISTRAL_MODEL` (défaut `mistral-small-latest`), température 0,8,
180 tokens, une seule tentative, timeout 30 s maximum. Aucun test physique.

```bash
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/ruff check services/rose.py services/rose_terminal.py scripts/rose_console.py tests/test_rose.py
.venv/bin/ruff check --select F services/minitel_chatgpt.py services/console_terminal.py
```

Avec la configuration autorisée du propriétaire, essayer les deux vanilla et
les deux asexuels : ouverture timide, bravade, sous-entendu plus appuyé, refus
de thème, changement de langue, tutoiement, ambiguïté romantique, avance directe,
question sur la nature du personnage, dix tours et répétition. Évaluer les
réponses effectivement obtenues, sans transformer la séance en recherche de
contournement. Les limites empiriques et la rédaction finale restent à régler
à partir de ces résultats. Sur Minitel : écho, accents/translittération,
touches, annulation pendant requête, débit réel et rythme de la facture.
