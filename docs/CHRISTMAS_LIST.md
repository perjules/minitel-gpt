# Liste au Père Noël

Saisir `LISTE AU PERE NOEL` (accents acceptés), `LISTE DE NOEL` ou `NOEL`
dans la conversation Minitel ou son aperçu dans l'administration.
La liste appartient au profil sélectionné, y compris le profil MinitelGPT sans nom.
Elle est **publique pour tous les utilisateurs du Minitel**; seul son propriétaire
peut la modifier. Les profils restent des sélecteurs sans authentification.

| Commande | Effet |
|---|---|
| `AJOUTER souhaits` / `A: souhaits` | Un appel Mistral extrait un ou plusieurs articles et les ajoute. |
| `TEXTE article` / `T: article` | Ajoute exactement un article sans IA. |
| `SUPPRIMER 2` / `S: 2` | Supprime la deuxième ligne et renumérote la liste. |
| `COCHER 2` / `C: 2` | Coche la deuxième ligne. |
| `DECOCHER 2` / `D: 2` | Décoche la deuxième ligne. |
| `LIRE Alice` / `L: Alice` | Consulte la liste publique d'un profil existant, sans créer de profil. |
| `LIRE` / `L:` | Revient à sa propre liste et la recharge. |
| `NOEL de Alice` | Ouvre directement la liste publique d'Alice. |
| `AIDE` | Affiche la liste et ses commandes. |
| `QUITTER` / `Q:` | Revient à la conversation. |

Les commandes et raccourcis restent dans le service Noël jusqu'à QUITTER.
SOMMAIRE réinitialise la conversation; LOGOUT revient à la connexion.
Sur Minitel, le lecteur de documents Gmail permet SUITE/RETOUR et la saisie d'une
commande depuis chaque page. Les numéros désignent les articles, pas les lignes
physiques après retour à la ligne à l'écran.

Exemple : `A: un vélo rouge taille M et le livre Dune` crée deux articles :

```markdown
1. [ ] Un vélo rouge taille M
2. [ ] Le livre Dune
```

`C: 2` donne `2. [X] Le livre Dune`. Aucun appel IA pour lire, supprimer,
cocher ou décocher. En cas d'erreur ou d'interprétation IA invalide, aucun ajout;
`T:` reste disponible. Aucun outil, recherche ou historique de conversation n'est
transmis à l'interpréteur d'ajouts. Il utilise la clé et le modèle Mistral général.
L'ajout est enregistré directement, sans étape de confirmation.

## Stockage et limites

Deux fichiers en clair dans le dossier existant du profil sous `MINITEL_MEMORY_DIR`
(par défaut `~/.local/share/3615-ia`) : `christmas-list.json`, source de vérité,
et `christmas-list.md`, copie lisible régénérée à la consultation ou modification.
Aucune migration SQL, permission Gmail ou dépendance supplémentaire.

```json
{
  "version": 1,
  "user_id": 2,
  "revision": 1,
  "items": [{"text": "Un vélo rouge", "checked": false}]
}
```

Verrou de profil existant, remplacement atomique des fichiers et révision vérifiée
avant mutation : si une autre session a modifié la liste, aucune commande basée
sur d'anciens numéros n'est appliquée. La nouvelle liste est affichée; recommencer
la commande après lecture. Une fermeture de session bloque un ajout IA tardif.
Un JSON endommagé est conservé, jamais remplacé par une liste vide. Un échec de
copie Markdown est signalé sans annuler un JSON déjà enregistré.

Maximum 200 articles, 300 caractères par article, 30 articles par interprétation
IA et 2000 caractères de consigne. Pas de remise à zéro annuelle automatique.
Renommer un profil conserve ses fichiers; le supprimer les supprime. Comme les
brouillons et le journal, une fusion exceptionnelle de profils conserve la liste
source dans l'archive `merged-profiles`; elle ne concatène pas les deux listes.
Les échanges du service sont enregistrés dans les sessions publiques ordinaires.

## Vérification

Tests hors ligne : stockage, cases, numérotation, utilisateurs, lecture publique,
refus des écritures sur une autre liste, absence de création implicite de profil,
révisions concurrentes, réponse IA invalide, fermeture pendant un ajout, JSON
endommagé, copie Markdown, priorité de routage et aperçu navigateur.

Sur Pi/Minitel : ouvrir NOEL, ajouter deux cadeaux avec A:, cocher puis supprimer,
parcourir plusieurs pages avec SUITE/RETOUR, consulter depuis un autre profil,
puis tester Q:, SOMMAIRE, LOGOUT et la persistance après redémarrage.
Ces contrôles matériels et le libellé réel du modèle restent à valider.

### Diagnostic des ajouts IA

Le choix de `A:` constitue déjà une demande d'ajout : les noms seuls (`UNE LOUPE`)
et les phrases courtes sont acceptés sans répéter « ajoute » ou « pour Noël ».
Les ajouts utilisent désormais `response_format: json_schema` avec un schéma strict :
objet contenant uniquement `items`, de 1 à 30 chaînes de 1 à 300 caractères.
La contrainte `minItems: 1` exclut les réponses vides constatées avec le simple
mode `json_object`, qui ne garantissait que du JSON syntaxiquement valide.
La formulation vague d'un souhait est conservée plutôt qu'omise. Python vérifie
toujours la réponse avant tout enregistrement; pas de repli silencieux vers du
texte brut ni de nouvel essai automatique. Les autres appels du helper conservent
leur format actuel. Référence : [Mistral — formats de réponse](https://docs.mistral.ai/api/endpoint/chat).

Les instructions de ce service utilisent le contexte général, sans les règles
de traitement du contenu d'un email. Une réponse vide est signalée comme
« Aucun cadeau reconnu », distinctement d'une erreur API ou d'un JSON invalide.

Après un essai, extraire les traces sur le Pi :

```bash
sudo journalctl --namespace=minitel-gpt -u minitel-chatgpt \
  --since "5 minutes ago" --no-pager | grep -E 'llm_call|christmas_add'
```

`llm_call status=ok` confirme la réception d'une réponse, pas son acceptation
par le service. `christmas_add` indique `status=ok items=N` ou un motif fixe :
`no_items`, `invalid_json`, `invalid_schema`, `invalid_item`, `api_…`.
Aucun texte saisi, cadeau, réponse brute ou secret dans ces traces de diagnostic.
