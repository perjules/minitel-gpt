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
