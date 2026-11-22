"""Curated display-only mainframe theatre; never represents real telemetry."""
import random

TASKS = (
    "Tri des paradoxes sur bande...",
    "Rebobinage du raisonnement...",
    "Alignement des tores memoire...",
    "Degivrage du Cray de secours...",
    "Tri des cartes perforees...",
    "Dedouanement des octets...",
    "Synchronisation des bouliers...",
    "Vidange du cache a mercure...",
    "Ponderation des evidences...",
    "Recherche du bit de secours...",
    "Compactage des contradictions...",
    "Consultation du tambour central...",
    "Mise en phase des tubes a vide...",
    "Depannage du bus metaphysique...",
    "Classement des idees egarees...",
    "Relance du lecteur de ruban...",
    "Calcul de la retenue morale...",
    "Arbitrage des relais indecis...",
    "Transfert vers le PDP-11...",
    "Controle du registre du doute...",
    "Depoussierage des hypotheses...",
    "Demelage des fils logiques...",
    "Etalonnage du doute raisonnable...",
    "Allocation des cycles de pensee...",
)
INTERVAL = 2.0


def sequence():
    """Shuffle bags, without repeating at a bag boundary."""
    previous = None
    while True:
        bag = list(TASKS)
        random.shuffle(bag)
        if bag[0] == previous:
            bag[0], bag[1] = bag[1], bag[0]
        for task in bag:
            previous = task
            yield task
