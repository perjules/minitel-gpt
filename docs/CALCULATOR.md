# Calculateur et théâtre mathématique

Au prompt normal, tapez `17 * 23`, `-2 * (3 + 4)` ou `0,1 + 0.2`, puis **ENVOI**.
Les expressions simples déclenchent automatiquement le calcul local. **CALC** puis
ENVOI ouvre une petite console : les nombres seuls y sont aussi acceptés. **RETOUR**
reprend la conversation précédente, sans effacer le nom sélectionné ni les états
Gmail/mémoire suspendus. En console, CORRECTION reste la touche de suppression de
caractère. En admin, tapez `RETOUR`, `QUITTER` ou `EXIT` pour quitter. `CALC expression`
est également accepté pour un calcul explicite sans entrer dans la console.

Les deux voies utilisent le même moteur. Il n'y a ni annonce au démarrage, ni menu.
La détection automatique est volontairement conservatrice : uniquement des nombres,
espaces et signes arithmétiques, avec au moins un opérateur ou une parenthèse. Un
nombre isolé conserve son routage normal. Les sélections, clarifications mémoire,
destinataires en attente et brouillons Gmail priment; CALC permet une entrée explicite
même depuis ces contextes. Un `2+2` pendant une révision de mail reste dans le flux
mail; tapez CALC pour faire un calcul indépendant.

## Calculs ordinaires

Syntaxe : entiers, décimales, signes unaires `+ -`, opérateurs `+ - * /` et parenthèses.
Point et virgule sont interchangeables comme séparateurs décimaux; aucun séparateur
de milliers. Les priorités usuelles s'appliquent. Pas de fonctions, exposants `**`,
modulo, division entière, lettres, code ou commandes système. La notation scientifique
est un **format de sortie**, pas une syntaxe d'entrée dans cette première version.

Les résultats utilisent au plus **six chiffres significatifs**, sans zéros décimaux
superflus. `~` signale un arrondi : `1/3` donne `~0.333333`, `7/3` donne `~2.33333`.
La sortie devient scientifique à partir de `1e6` ou pour les valeurs non nulles
inférieures à `1e-4` en valeur absolue : `123456789+0` donne `~1.23457e8`.

Python valide un arbre arithmétique restreint, sans eval, exécution de code ni shell.
Limites : une ligne de 256 caractères, 16 niveaux de parenthèses, 160 éléments de
syntaxe, 80 chiffres par littéral et un budget de 1024 bits pour les nombres
intermédiaires. Les expressions invalides/trop grandes donnent une erreur locale.
Leur interprétation n'est pas déléguée à un modèle.

Chaque calcul est accompagné d'une courte phrase choisie au hasard dans un pool de
32 exemples par langue. Chaque phrase combine un matériel du mainframe et une
opération numérique, du plausible à l'absurde. Le résultat reste bien visible.
Aucun appel API ni délai artificiel pour les calculs ordinaires.

## Division par zéro

Elle est détectée pendant l'évaluation, y compris `17/(2-2)`, `1/(-0.0)` ou `0/0`.
C'est la seule opération du calculateur pouvant appeler une API : **une requête
Mistral Chat Completions**, avec la clé et le modèle Mistral déjà configurés, pour
générer une nouvelle scène. Elle reçoit uniquement l'expression validée, la langue,
les consignes et l'exemple approuvé. Aucun historique, souvenir, mail, donnée PRONOTE,
outil ou recherche web n'est envoyé avec cette requête. Aucun retry automatique.
Le fournisseur des réponses ordinaires peut être différent; ce générateur utilise
la configuration Mistral existante.

La réponse JSON comporte trois actes : escalade, maintenance, reprise. Python valide
la forme, les tailles, la casse, l'absence de contrôles/URLs et les repères K7, K2F,
Alcatel, X.25, 1984 et poste vacant. Il insère lui-même le résultat réel :
**« Division par zéro : résultat indéfini. »** avant les tentatives de maintenance.
La réponse du modèle ne décide pas de l'issue mathématique.

Timeout HTTP : 12 secondes; l'attente terminal reste soumise au deadline existant du
service et aux touches d'interruption. Si la clé manque ou si la génération échoue,
est tronquée ou invalide, le texte français fourni et sa traduction anglaise servent
de repli statique. Les diagnostics indiquent seulement génération/repli, jamais le
payload interne ou des credentials. Une annulation utilisateur abandonne l'affichage;
elle ne lance pas la scène de repli et ne garantit pas l'annulation/facturation du
travail déjà lancé chez le fournisseur.

Le texte défile automatiquement, avec environ 0,22 seconde ajoutée par ligne; le
transfert UART ajoute son propre temps. **SUITE** retire les pauses ajoutées,
**RETOUR** interrompt et reprend la conversation, **SOMMAIRE** réinitialise la session,
**GUIDE** conserve son retour à l'aide. Le playback est plafonné à 90 secondes, interroge
le clavier entre les lignes, purge les données série en attente à l'interruption et
rétablit toujours le mode page. Aucun producteur de frames en arrière-plan.
Sur le texte de référence, les tests simulés mesurent environ 23 secondes de pauses;
la durée réelle sur le Minitel n'est pas encore mesurée.

Le générateur crée uniquement du texte. Les allocations, calculs avancés, pannes et
appels Alcatel/X.25 n'ont aucune action externe. L'application continue de fonctionner.
Le théâtre visible figure dans les journaux datés sous le canal `theatre`; une scène
préparée complète est archivée comme les réponses paginées, avec un événement si
l'affichage est interrompu. Seuls expression/résultat alimentent l'historique factuel
et la réponse précédente utilisable par la mémoire. Aucun diagnostic fictif n'est
automatiquement mémorisé, ni repris comme preuve. SOMMAIRE efface le mode CALC.

## Contenu et vérification

Sources faisant autorité : `config/calculator.json` (phrases, scènes de référence,
libellés FR/EN) et `config/calculator_prompt.txt` (consignes de génération).
`services/calculator.py` est le moteur partagé par le terminal et le test admin.
Les mots peuvent évoluer indépendamment du parseur. Pas de nouvelle dépendance.

**Administration → Personnalités → Tester la personnalité** utilise le même moteur.
CALC et RETOUR y fonctionnent; la scène défile dans l'aperçu. Une nouvelle soumission
ou SOMMAIRE abandonne les frames du résultat précédent; une réponse HTTP tardive ne
remplace pas la session réinitialisée. La langue reprend le choix enregistré du nom,
ou le français pour un espace sans choix enregistré. La page Gmail reste dédiée au
test Gmail; pour simuler CALC, utilisez le test de personnalité.

Livraison locale sur **develop**, publication seulement sur demande explicite.
Après publication, sur le Pi, conservez le travail local puis :

```sh
cd /home/minitel/minitel-gpt
git status --short
git switch develop
git pull --ff-only origin develop
sudo systemctl restart minitel-chatgpt admin-ui
systemctl is-active minitel-chatgpt admin-ui
```

Acceptation admin/live :

1. Tester les expressions ci-dessus, la priorité et les virgules; vérifier six chiffres
   significatifs, une phrase matériel/méthode et l'absence d'appel modèle ordinaire.
2. Entrer CALC, taper un nombre isolé et une erreur, sortir avec RETOUR. Vérifier la
   conversation, les souvenirs et les contextes mail précédents sans envoi réel.
3. Tester `17/0` puis `17/(2-2)` : comparer la nouveauté des scènes, le résultat
   indéfini, l'escalade et la maintenance abandonnée. Contrôler le diagnostic
   `calculator scene=generated` ou `scene=fallback`. Observer la latence réelle.
4. Interrompre pendant la génération et pendant le défilement, puis vérifier l'absence
   de frames tardives et un nouveau calcul/conversation. Vérifier que « garde cette
   réponse » ne sauvegarde que l'issue mathématique, jamais les diagnostics fictifs.
5. Refaire ces contrôles au Minitel déballé : caractères, largeur, vitesse UART,
   SUITE/RETOUR/SOMMAIRE/GUIDE, reprise et switch utilisateur.

Preuves de cette livraison : offline avec API simulée, fichiers/journaux réels,
client Flask, décodeur UART et horloge simulée. Pas d'appel modèle live, de test admin
sur le Pi ni de validation physique revendiqué. Le Minitel reste emballé; la validation
physique est en attente. Les mêmes prérequis/tests optionnels Gmail et PRONOTE restent :

```sh
uv run --locked --all-extras pytest -v
```
