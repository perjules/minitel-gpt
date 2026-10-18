# Lecture de scripts rose

Le mode script du lanceur personnel permet des essais reproductibles sans
clavier ni écran. Il utilise le même `rose.Session`, le même modèle Mistral,
les mêmes voix, modules, tempo, limites et historique borné. Il ne charge pas
le module d'usure, toujours désactivé. Aucun actif de personnalité n'est modifié.

Créer un fichier UTF-8, par exemple `essai.json` :

```json
{
  "persona": "Jean-Marc",
  "prompts": [
    "Bonsoir.",
    "Parlez-moi de votre soirée."
  ]
}
```

Depuis le dépôt, après activation de `.venv` :

```bash
python scripts/rose_console.py --script essai.json
python scripts/rose_console.py -s essai.json -i
python scripts/rose_console.py -s essai.json --mock
python scripts/rose_console.py -s essai.json --log logs/rose-scripts/mon-essai.jsonl
```

## Exécution

- Sans `-i`, aucun terminal interactif n'est requis. Une exécution réussie
  n'écrit rien sur stdout ou stderr. Les erreurs de lancement sont signalées
  brièvement sur stderr ; le dialogue n'y est jamais imprimé.
- Avec `--interactive` / `-i`, le terminal habituel est requis. Les échanges
  scriptés s'affichent au fil de l'exécution, puis le prompt `>` permet de
  poursuivre **la même session**, sans nouvelle ouverture ou réinitialisation.
  Les tours saisis ensuite sont également journalisés. Les touches de sortie
  habituelles restent disponibles ; Ctrl-R quitte, Ctrl-P réinitialise/quitte,
  Ctrl-Q/Ctrl-C interrompt. Aucun accueil final n'est ajouté.
- Le mot de passe, l'accueil, le catalogue, l'annuaire aléatoire, les bandeaux
  administratifs et la facture sont omis, aussi pour un profil normal.
  Les instructions de voix et de tempo restent actives.
- La réponse d'ouverture habituelle est générée avant la première entrée de
  `prompts`. Ensuite, chaque message est traité une fois, dans l'ordre.
  Une liste vide est autorisée : ouverture seule, ou ouverture puis clavier avec `-i`.
- `RETOUR`, `SOMMAIRE`, les demandes d'arrêt reconnues et le mot d'arrêt de la
  session terminent la lecture ; les messages suivants ne sont pas envoyés.
  `AIDE` et les saisies vides gardent leur traitement local, sans appel modèle.
- Une erreur de service récupérable est enregistrée ; le message suivant peut
  être essayé, sans retry automatique. Un rejet de modération identifiable ou
  une échéance locale arrête la lecture, toujours sans facture. Aucun nouveau
  guardrail ou seuil n'est introduit. Une réponse HTTP tardive peut continuer
  chez le fournisseur ; son résultat local est écarté après fermeture.

`persona` doit nommer un profil ou un alias connu du catalogue normal ou du
catalogue de test. La résolution respecte la casse indifférente et les alias
déjà existants ; aucun nom inconnu ne déclenche une classification ou un tirage.
`--test` limite explicitement la recherche au catalogue de test ; sans ce flag,
les deux catalogues sont recherchés. Un nom ambigu est rejeté.
`prompts` doit être une liste de chaînes, chacune limitée aux 800 caractères
de la saisie ordinaire. Tout le fichier est validé avant un appel modèle.
`--name` et `--set-password` ne sont pas combinables avec `--script`.
`--interactive` et `--log` nécessitent `--script`.

## Journal complet, explicitement demandé

Le mode script constitue une **exception volontaire** au défaut transitoire
des sessions ordinaires. Il conserve du texte potentiellement intime sur disque.
Le propriétaire choisit la conservation et la suppression de ces fichiers.

Sans `--log`, un fichier unique est créé dans `logs/rose-scripts/`, avec le
nom du script, l'heure UTC et un suffixe aléatoire. Le chemin n'est pas affiché
en mode silencieux ; utiliser `--log` pour choisir un nom connu.
Le fichier est créé exclusivement, avec permissions `0600` ; un fichier
existant n'est jamais écrasé. Le répertoire `logs/` est ignoré par Git.
Un chemin choisi ailleurs avec `--log` doit être exclu des publications par
le propriétaire. Un échec d'écriture interrompt l'essai ; aucune nouvelle
requête ne doit partir sans journalisation possible.

Chaque ligne JSONL est un événement UTF-8 horodaté :

| Événement | Contenu |
|---|---|
| `script`, `session` | Persona résolue, catalogue, messages prévus, modèle, paramètres, mode simulé, mot d'arrêt |
| `user` | Texte intégral soumis, origine script/clavier ; les commandes locales sont incluses |
| `request` | Message système assemblé et contexte effectivement remis au modèle, y compris le signal d'ouverture |
| `provider_response` | Texte intégral retourné par Mistral, avant nettoyage et limite de 700 caractères ; statut HTTP, raison de fin, compteurs de tokens disponibles, classe d'erreur de transport/parsing le cas échéant |
| `assistant` | Réponse acceptée par la session, avant translittération et mise en page écran |
| `local_input`, `turn_result`, `adapter_error` | Commandes locales, erreurs/annulations et classe d'erreur d'un adaptateur, sans corps d'erreur arbitraire |
| `interactive_start`, `session_end`, `run_error` | Passage au clavier, fin/raison/décompte et erreur de lancement éventuelle |

Le journal reste complet lorsque l'historique fourni au modèle est élagué.
Les identifiants de tour relient requêtes et réponses. En mode `--mock`,
les réponses sont des exemples écrits, sans événement HTTP fournisseur.
Les clés API, en-têtes d'authentification, mots de passe et variables
d'environnement ne sont pas journalisés. Les corps arbitraires d'erreur HTTP
ne le sont pas non plus ; les statuts et le résultat local restent disponibles.
La borne de sortie ordinaire et le comportement sur réponse inachevée restent
inchangés : le texte brut reste visible dans `provider_response`, mais une
réponse refusée par l'adaptateur n'entre pas dans l'historique de conversation.

Codes de sortie : `0` pour une lecture terminée ou une sortie locale normale,
`1` si un appel a échoué, a été rejeté ou a dépassé l'échéance,
`2` pour un script/une configuration/un journal invalide ou indisponible,
`130` pour une interruption clavier pendant la session.

Les tests de ce mode utilisent des modèles et réponses HTTP simulés. Ils
vérifient les contrats du harnais, pas les personnalités ou la modération réelles.
