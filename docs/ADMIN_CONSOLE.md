# Administration de l’appareil

L’interface conserve quatre sections : Tableau de bord, Configuration IA,
Services, Maintenance. Les routes historiques et les protections de session/CSRF
restent en place. Les liens directs utilisent `/?section=...` ; la navigation
locale conserve les champs saisis. Gmail et PRONOTE restent des pages séparées.

Le tableau de bord est en lecture seule. Les mesures système sont prises à
l’ouverture/actualisation de la page, avec des délais bornés pour les commandes.
Une mesure indisponible ne bloque pas la page. Les mesures détaillées du stockage
ne sont chargées qu’à l’ouverture de Maintenance. Aucun suivi périodique n’est
ajouté. La vérification Git reste une action explicite ; son résultat est celui
de la dernière vérification dans la session du navigateur.

Les boutons de Maintenance utilisent les mécanismes existants. Un déploiement
planifié n’est pas un succès confirmé : actualiser Maintenance pour lire sa phase.
Le redémarrage concerne le service Minitel, pas le Pi. NetworkManager et le portail
de provisionnement restent responsables du Wi-Fi.

## Constat de sécurité à examiner lors de la revue des fonctionnalités

`services/admin_ui.py` utilise encore `ADMIN_PASSWORD=mistral` si la variable
est absente. `config/admin-ui.service` charge un fichier `.env` facultatif et
n’exige pas cette variable. Le dépôt ne permet pas de connaître le mot de passe
réellement configuré sur le Pi. La valeur par défaut reste inchangée pendant la
réorganisation pour éviter de modifier les règles d’accès sans décision produit.

Correction proposée : exiger une valeur explicite non vide dans `.env`, refuser
les connexions si elle manque, et vérifier ce prérequis à l’installation.
Conserver l’authentification actuelle et les protections CSRF/session ; aucun
système de comptes supplémentaire n’est nécessaire.

## Validation physique encore nécessaire

Vérifier sur le Pi l’état des services, les permissions des journaux, nmcli,
Tailscale, les adresses réseau et les phases de déploiement. Tester un redémarrage
du terminal avec une session Minitel réelle. Mise à jour/rollback, redémarrage et
arrêt du Pi ne sont pas exécutés sur le poste de développement.

Une erreur de syntaxe déjà présente dans `develop` à `9f09808` (séquence littérale
`\n` dans `wifi_manager.main`) a été corrigée sans changer le provisionnement.

## Configuration IA

Les réglages de fournisseur/modèle/clé sont regroupés avant les personnalités.
Les réglages des fournisseurs non sélectionnés restent accessibles et conservent
leurs clés. Seules les options existantes sont exposées ; le schéma est inchangé.

Créer un preset ne l’active pas. L’éditeur, les connaissances et le test suivent
la personnalité sélectionnée, conservée dans l’URL. Enregistrer un preset inactif
ne redémarre pas le Minitel. Enregistrer le preset actif, activer un preset, changer
les réglages IA ou modifier les connaissances actives conserve le redémarrage
automatique existant. Un redémarrage refusé ne fait plus passer l’enregistrement
pour un échec de sauvegarde ni pour une application confirmée : le message indique
les deux résultats séparément. La personnalité « active » désigne la configuration,
pas une preuve de ce qui est actuellement chargé dans le processus du terminal.

Les modifications non enregistrées sont signalées. La navigation entre sections
les conserve ; changer de preset ou quitter la page déclenche un avertissement.
Les brouillons ne sont pas sauvegardés automatiquement. Les tests utilisent le
prompt édité et les réglages IA enregistrés, sans activer le preset. La génération
d’un prompt ne remplace plus un autre brouillon modifié pendant la requête.
Un prompt système vide conserve les consignes existantes, comme auparavant.

## Vérifications hors Pi (27 octobre 2026)

- `uv sync --locked --all-extras` : effectué ; aucun manifeste/verrou modifié.
- Suite ciblée : 117 tests et 22 sous-tests passent (navigation, administration,
  configuration/CSRF, fichiers de connaissance, bornes d’opérations, Gmail,
  réponses PRONOTE, journaux/stockage et déploiement simulé).
- `uv run --locked --all-extras ruff check .` : passe.
- `uv run --locked --all-extras python scripts/export_requirements.py --check` : passe.
- HTML rendu dans les quatre sections : balises équilibrées, identifiants uniques,
  aucun formulaire imbriqué. Tous les scripts intégrés passent `node --check`.
- Validation visuelle et interaction dans un navigateur : non effectuées. Chromium
  n’était pas installé ; la tentative de téléchargement du binaire a échoué.
- La suite Wi-Fi existante a quatre échecs hérités : trois tests attendent la classe
  `Monitor`, un attend `monitor_loop`, supprimés par le provisionnement au démarrage.
  Ces tests n’ont pas été modifiés et la surveillance périodique n’a pas été rétablie.

## Prochaine revue produit

Passer section par section avec les classifications KEEP / MODIFY / MOVE / REMOVE
et ADD. Examiner notamment le mot de passe par défaut, les diagnostics utiles,
la fiabilité/mise en évidence des mises à jour et les contrôles de personnalité.
Gmail, PRONOTE, Alexa, sauvegarde/restauration et accès distant attendent des décisions
explicites avant toute évolution. Aucun nouveau contrôle de puissance ou de Wi-Fi
n’a été ajouté. Les commandes cachées `mainframe` restent inchangées.
