"""Private terminal panel for the Aizone HTTP API."""
import re
import threading
import time
from decimal import Decimal, InvalidOperation
from functools import partial
from queue import Queue

from . import airzone_client, config_store, home_config, rose_terminal

MODES = {1: "Arrêt", 2: "Climatisation", 3: "Chauffage", 4: "Ventilation",
         5: "Déshumidification", 7: "Auto"}
MESSAGES = {
    "confirmed": "Réglage confirmé par Airzone.",
    "sent": "Consigne envoyée. Relecture en attente.",
    "rejected": "Commande rejetée par Airzone.",
    "not_sent": "Commande non envoyée.",
    "unconfirmed": "Commande envoyée. Relecture en attente.",
    "unknown": "Résultat inconnu. Vérifiez l'état.",
    "skipped": "Non envoyée (séquence interrompue).",
}


def label(value, fallback=""):
    if not isinstance(value, str):
        return fallback
    return " ".join("".join(char for char in value[:160] if char.isprintable()).split()) or fallback


def room_name(zone):
    return label(zone.get("name"), f"Zone {zone['id']}")


def number(value):
    return type(value) in (int, float) and -1e6 < value < 1e6


def display(value, unit=""):
    return f"{value:g}".replace(".", ",") + unit if number(value) else "--"


def power(zone):
    value = zone.get("on")
    if type(value) is not bool:
        return "--"
    return "Marche" if value else "Arrêt"


def mode_name(value):
    return MODES.get(value, f"Mode {value}") if type(value) is int else "--"


def current_mode(state):
    return next((z.get("mode") for z in state.get("zones", [])
                 if z["id"] == state.get("master_zone_id") and z.get("available") is True), None)


def supported_modes(state):
    modes = state.get("supported_modes")
    return [mode for mode in modes if type(mode) is int and 1 <= mode <= 255] if isinstance(modes, list) else []


def render(state, zone, choosing_mode, notice, *, all_zones=False):
    lines = ["AIRZONE / " + (room_name(zone) if zone else "MAISON")]
    if state.get("demo") is True:
        lines.append("SIMULATION / lecture seule")
    if not airzone_client.fresh(state):
        lines.append("Hors ligne ou relevé non actuel")
    seconds = airzone_client.age(state)
    if seconds is not None:
        lines.append(f"Âge du relevé : {max(0, int(seconds))} s")
    if notice:
        lines.extend(("", notice))
    lines.append("")
    if choosing_mode:
        lines.append("Mode actuel : " + mode_name(current_mode(state)))
        lines.extend(f"{mode}  {mode_name(mode)}" for mode in supported_modes(state))
        lines.extend(("", "Numéro du mode + ENVOI"))
    elif all_zones:
        lines.extend(("TOUTES LES ZONES", "C:20,5 / MARCHE / ARRET",
                      "Une confirmation pour toute la maison."))
    elif zone:
        if zone.get("available") is not True:
            lines.append("Zone indisponible.")
        else:
            lines.extend((
                "Température : " + display(zone.get("temperature"), " °C"),
                "Consigne    : " + display(zone.get("setpoint"), " °C"),
                "État        : " + power(zone),
                "Humidité    : " + display(zone.get("humidity"), " %"),
                "Demande air : " + display(zone.get("air_demand")),
                "Mode        : " + mode_name(zone.get("mode")),
            ))
            warning = label(zone.get("device_warning"))
            if warning:
                lines.append(warning)
            if number(zone.get("pending_setpoint")):
                lines.append("Consigne en attente : " + display(zone["pending_setpoint"], " °C"))
        lines.extend(("", "C:20,5 / MARCHE / ARRET"))
    else:
        for item in state.get("zones", []):
            lines.append(f"{item['id']}  {room_name(item)[:23]}")
            if item.get("available") is True:
                lines.append(f"   {display(item.get('temperature'), ' °C')} / "
                             f"{display(item.get('setpoint'), ' °C')} / {power(item)}")
            else:
                lines.append("   Indisponible")
        lines.extend(("", "0 ou TOUS : toutes les zones", "Numéro de zone / MODE"))
    if zone:
        lines.append("SUITE/RETOUR : zone suivante/précédente")
    lines.append("MAJ : relire / taper RETOUR : sortir")
    return "\n".join(lines)


def reason(value, fallback):
    return label(value[0], fallback) if isinstance(value, list) and value else fallback


def proposal(state, zone, choosing_mode, command):
    if state.get("demo") is not False or not airzone_client.fresh(state):
        raise airzone_client.AirzoneError("Relevé réel récent requis. Tapez MAJ.")
    if state.get("commands_enabled") is not True:
        raise airzone_client.AirzoneError("Commandes désactivées.")
    if choosing_mode:
        if not re.fullmatch(r"[0-9]{1,3}", command) or int(command) not in supported_modes(state):
            raise airzone_client.AirzoneError("Choisissez un mode proposé.")
        if state.get("mode_enabled") is not True:
            raise airzone_client.AirzoneError(reason(state.get("mode_reasons"), "Mode partagé indisponible."))
        value = int(command)
        return None, "mode", value, f"Toute la maison :\n{mode_name(current_mode(state))} > {mode_name(value)}"
    if command in {"MARCHE", "ARRÊT", "ARRET"}:
        field, value = "on", command == "MARCHE"
        summary = f"{power(zone)} > {'Marche' if value else 'Arrêt'}"
    else:
        match = re.fullmatch(r"C:\s*([+-]?[0-9]{1,3}(?:[.,][0-9]{1,3})?)", command)
        if not match:
            raise airzone_client.AirzoneError("Utilisez C:20,5, MARCHE ou ARRET.")
        field = "setpoint"
        target = Decimal(match[1].replace(",", "."))
        limits = [zone.get(key) for key in ("min", "max", "step")]
        if not all(number(limit) for limit in limits):
            raise airzone_client.AirzoneError("Limites de consigne indisponibles.")
        low, high, step = (Decimal(str(limit)) for limit in limits)
        try:
            valid = low <= target <= high and step > 0 and (target - low) % step == 0
        except InvalidOperation:
            valid = False
        if not valid:
            raise airzone_client.AirzoneError(f"Consigne : {low} à {high} °C, pas {step}.")
        value = float(target)
        summary = f"{display(zone.get('setpoint'))} > {display(value, ' °C')}"
    controls = zone.get("controls")
    if zone.get("available") is not True or not isinstance(controls, dict) or controls.get(field) is not True:
        reasons = zone.get("control_reasons")
        why = reasons.get(field) if isinstance(reasons, dict) else None
        raise airzone_client.AirzoneError(reason(why, "Commande indisponible pour cette zone."))
    return zone["id"], field, value, f"{room_name(zone)} :\n{summary}"


def group_proposal(state, command):
    zones = state.get("zones", [])
    if not zones:
        raise airzone_client.AirzoneError("Aucune zone disponible.")
    targets = []
    for zone in zones:
        try:
            zone_id, field, value, _ = proposal(state, zone, False, command)
        except airzone_client.AirzoneError as error:
            raise airzone_client.AirzoneError(f"{room_name(zone)} : {error}") from error
        targets.append((zone_id, field, value))
    value = targets[0][2]
    if targets[0][1] == "setpoint":
        change = display(value, " °C")
    else:
        change = "Marche" if value else "Arrêt"
    names = "\n".join(room_name(zone) for zone in zones)
    return targets, f"TOUTES LES ZONES ({len(zones)})\n{names}\n\nAppliquer : {change}"


def mode_notice(state, target, outcome):
    requested = f"Mode demandé : {mode_name(target)}"
    observed = f"Mode relu : {mode_name(current_mode(state))}"
    return "\n".join((MESSAGES[outcome], requested, observed))


def group_notice(state, receipt):
    zones = {zone["id"]: zone for zone in state.get("zones", [])}
    lines = ["BILAN TOUTES LES ZONES"]
    for result in receipt["results"]:
        zone = zones.get(result["id"], {"id": result["id"]})
        lines.append(room_name(zone) + " :")
        lines.append(MESSAGES.get(result["outcome"], MESSAGES["unknown"]))
    return "\n".join(lines)


def choose_controller(term, language):
    chat = rose_terminal.api()
    try:
        choices = home_config.targets()
    except (config_store.InvalidConfig, config_store.StorageFailure) as error:
        chat.show_response(term, str(error), language)
        return None, "done"
    if not choices:
        chat.show_response(term, "Climatisation désactivée. Configurez Maison connectée dans l'administration.", language)
        return None, "done"
    if len(choices) == 1:
        return choices[0], "done"
    lines = ["AIRZONE / CHOISIR UNE MAISON"]
    lines.extend(f"{i}  {item['home']} / {item['name']}" for i, item in enumerate(choices, 1))
    lines.append("Numéro + ENVOI / RETOUR : quitter")
    chat.show_response(term, "\n".join(lines), language)
    while True:
        answer, action = chat.read_question(term, calculator_mode=True)
        if action != "envoi":
            return None, action
        answer = (answer or "").strip().upper()
        if answer in {"RETOUR", "NON"}:
            return None, "done"
        if answer in {"SOMMAIRE", "LOGOUT"}:
            return None, answer.lower()
        if re.fullmatch(r"[0-9]{1,3}", answer) and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1], "done"


def run(term, client, language, *, initial=None, access=None, location=""):
    """A chat proposal finishes after one action; explicit entry keeps the panel open."""
    chat = rose_terminal.api()
    pending = None

    def request(operation, timeout):
        nonlocal pending
        output = Queue(maxsize=1)

        def work():
            try:
                output.put((operation(), None))
            except airzone_client.AirzoneError as error:
                output.put((str(error), None))
            except Exception as error:  # noqa: BLE001 - Relay worker failures to the UART owner.
                output.put((None, error))

        worker = threading.Thread(target=work, daemon=True)
        pending = term._pending = (worker, output, time.monotonic())
        term.waiting("Airzone : veuillez patienter")
        worker.start()
        return chat.wait_answer(term, pending, timeout)

    def finish(text):
        chat.show_response(term, text, language)
        return "done", pending

    state, selected, choosing_mode, notice = {}, None, False, ""
    queued_command = None
    if initial is not None:
        selected, choosing_mode = initial["selected"], initial["mode"]
        queued_command = initial["command"]
    refresh = True
    pending_mode = None
    while True:
        if refresh:
            try:
                if access is not None:
                    access()
            except (airzone_client.AirzoneError, config_store.InvalidConfig, config_store.StorageFailure) as error:
                return finish(str(error))
            reply, action = request(client.state, airzone_client.REQUEST_TIMEOUT + 1)
            if action in {"sommaire", "guide"}:
                return action, pending
            state = reply if isinstance(reply, dict) else {}
            if not state:
                notice = "\n".join(filter(None, (notice, reply or "Lecture Aizone indisponible.")))
            if action == "deadline" or initial is not None and not state:
                return finish(notice)
            if pending_mode is not None and state:
                target, baseline, master, pending_outcome = pending_mode
                observed = airzone_client.observation(state)
                confirmed = (state.get("demo") is False and airzone_client.fresh(state)
                             and baseline is not None and observed is not None and observed > baseline
                             and state.get("master_zone_id") == master and current_mode(state) == target)
                notice = mode_notice(state, target, "confirmed" if confirmed else pending_outcome)
                if confirmed:
                    pending_mode = None
            refresh = False
        zone = next((item for item in state.get("zones", []) if item["id"] == selected), None)
        if initial is not None and selected not in (None, 0) and zone is None:
            return finish("Zone indisponible. Aucune commande envoyée.")
        if initial is None:
            chat.show_response(term, location + "\n" + render(state, zone, choosing_mode, notice, all_zones=selected == 0), language)
        if queued_command is not None:
            answer, action = queued_command, "envoi"
            queued_command = None
        else:
            answer, action = chat.read_question(term, navigation_mode="zones" if zone else False)
        if action != "envoi":
            return action, pending
        command = (answer or "").strip().upper()
        if command in {"SOMMAIRE", "LOGOUT"}:
            return command.lower(), pending
        notice = ""
        if zone and command in {"SUIVANT", "PRECEDENT"}:
            ids = [item["id"] for item in state["zones"]]
            selected = ids[(ids.index(selected) + (1 if command == "SUIVANT" else -1)) % len(ids)]
            continue
        if command in {"TOUS", "0"}:
            selected, choosing_mode = 0, False
            continue
        if command == "MAJ":
            refresh = True
            continue
        if command == "RETOUR":
            if selected is None and not choosing_mode:
                return "done", pending
            selected, choosing_mode = None, False
            continue
        if not zone and not choosing_mode and selected != 0:
            if command == "MODE":
                choosing_mode = True
            elif re.fullmatch(r"[0-9]{1,2}", command) and any(
                    item["id"] == int(command) for item in state.get("zones", [])):
                selected = int(command)
            else:
                notice = "Choisissez un numéro de zone ou MODE."
            continue
        try:
            if selected == 0:
                targets, summary = group_proposal(state, command)
                operation = partial(client.apply_all, targets)
                timeout = 49
                field, value = targets[0][1:]
            else:
                zone_id, field, value, summary = proposal(state, zone, choosing_mode, command)
                operation = partial(client.apply, zone_id, field, value)
                timeout = airzone_client.REQUEST_TIMEOUT + 8
        except airzone_client.AirzoneError as error:
            notice = str(error)
            if initial is not None:
                return finish(notice)
            continue
        summary = (location + "\n" if location else "") + summary
        chat.show_response(term, summary + "\n\nConfirmer ? OUI / NON", language)
        answer, action = chat.read_question(term, calculator_mode=True)
        if action != "envoi":
            return action, pending
        answer = (answer or "").strip().upper()
        if answer in {"SOMMAIRE", "LOGOUT"}:
            return answer.lower(), pending
        if answer != "OUI":
            notice = "Commande annulée."
            if initial is not None:
                return finish(notice)
            continue
        try:
            if access is not None:
                access()
        except (airzone_client.AirzoneError, config_store.InvalidConfig, config_store.StorageFailure) as error:
            return finish(str(error))
        if not airzone_client.fresh(state):
            notice = "Relevé expiré. Aucune commande envoyée."
            if initial is not None:
                return finish(notice)
            refresh = True
            continue
        if field == "mode":
            pending_mode = (value, airzone_client.observation(state), state.get("master_zone_id"), "unknown")
        receipt, action = request(operation, timeout)
        if action in {"sommaire", "guide"}:
            return action, pending
        if action == "deadline":
            chat.show_response(term, MESSAGES["unknown"], language)
            return "done", pending
        receipt = receipt if isinstance(receipt, dict) else {}
        outcome = receipt.get("outcome", "unknown")
        if outcome == "group":
            notice = group_notice(state, receipt)
        elif field == "mode":
            notice = mode_notice(receipt, value, outcome if outcome in MESSAGES else "unknown")
            if outcome in {"confirmed", "not_sent", "rejected"}:
                pending_mode = None
            else:
                pending_mode = (*pending_mode[:3], outcome if outcome in MESSAGES else "unknown")
        else:
            notice = MESSAGES.get(outcome, MESSAGES["unknown"])
        if outcome in {"not_sent", "rejected"}:
            detail = label(receipt.get("message"))
            if detail:
                notice += "\n" + detail
        if initial is not None:
            return finish(summary + "\n\n" + notice)
        refresh = True
