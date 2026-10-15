"""Small deterministic PRONOTE vocabulary, shared validated service underneath."""
import copy
import re
import textwrap
import unicodedata
from datetime import date, timedelta

try:
    from . import pronote
except ImportError:
    import pronote


COMMANDS = {
    "NOTES": "grades", "MOYENNES": "averages", "DEVOIRS": "homework",
    "COURS": "timetable", "CONTROLES": "tests", "ABSENCES": "absences",
    "RETARDS": "delays", "BULLETIN": "report", "COMPETENCES": "evaluations",
    "INFOS": "announcements", "CANTINE": "menus", "BILAN": "briefing",
    "SANCTIONS": "punishments",
}
ALIASES = {"NOTE": "NOTES", "MOYENNE": "MOYENNES", "DEVOIR": "DEVOIRS",
           "CONTROLE": "CONTROLES", "INFO": "INFOS", "MENU": "CANTINE",
           "HELP": "AIDE", "QUITTER": "RETOUR", "SORTIR": "RETOUR"}
DATED = {"homework", "timetable", "tests", "announcements", "menus", "briefing"}
LABELS = {"subject": "Matiere", "date": "Date", "start": "Debut", "end": "Fin",
          "grade": "Note", "out_of": "Sur", "class_average": "Classe", "student_average": "Eleve",
          "minimum": "Minimum", "maximum": "Maximum", "coefficient": "Coeff.", "comment": "Commentaire",
          "comments": "Appreciations", "teacher": "Prof.", "teachers": "Prof.", "classroom": "Salle",
          "description": "Description", "canceled": "Annule", "status": "Etat", "test": "Controle",
          "done": "Fait", "bonus": "Bonus", "optional": "Facultatif", "title": "Titre", "content": "Texte",
          "author": "Auteur", "category": "Categorie", "read": "Lu", "survey": "Sondage",
          "justified": "Justifie", "justification": "Justification", "reasons": "Motifs", "hours": "Heures",
          "days": "Jours", "minutes": "Minutes", "nature": "Nature", "schedule": "Programme",
          "circumstances": "Circonstances", "duration": "Duree", "homework": "Travail",
          "acquisitions": "Competences", "level": "Niveau", "domain": "Domaine", "name": "Nom",
          "abbreviation": "Code", "first_meal": "Entree", "main_meal": "Plat", "side_meal": "Garniture",
          "other_meal": "Autre", "cheese": "Fromage", "dessert": "Dessert", "lunch": "Dejeuner",
          "dinner": "Diner", "attachments": "Pieces jointes", "filename": "Fichier", "type": "Type",
          "content_error": "Texte indisponible"}


def normalize(text):
    return unicodedata.normalize("NFKD", text.strip()).encode("ascii", "ignore").decode().upper()


def narrow(text):
    return "\n".join(part for line in text.splitlines()
                     for part in (textwrap.wrap(line, width=39, break_on_hyphens=False) or [""]))


def details(item):
    lines = []
    for key, value in item.items():
        # False justification/read status matters; suppress only noisy flags.
        if value is None or value == "" or value == [] or (value is False and key not in ("justified", "read", "done")):
            continue
        label = LABELS.get(key, key)
        if isinstance(value, dict):
            lines.append(label + ":\n" + details(value))
        elif isinstance(value, list):
            lines.append(label + ":")
            lines.extend(details(v) if isinstance(v, dict) else str(v) for v in value)
        else:
            lines.append(label + ": " + ("oui" if value is True else "non" if value is False else str(value)))
    return "\n".join(lines)


def compact(item, operation, index):
    """Short screen view; TEXTE/LIRE and printing retain the original fields."""
    title = f"{index}. " + (item.get("subject") or item.get("title") or "")
    if operation in ("timetable", "tests"):
        start, end = item.get("start") or "", item.get("end") or ""
        lines = [title, start[:10] + " " + start[11:16] + "-" + end[11:16]]
        if item.get("teacher") or item.get("classroom"):
            lines.append(" / ".join(str(item[k]) for k in ("classroom", "teacher") if item.get(k)))
        lines.extend(label for key, label in (("canceled", "ANNULE"), ("test", "CONTROLE")) if item.get(key))
        if item.get("status"):
            lines.append(item["status"])
    elif operation == "grades":
        lines = [title, (item.get("date") or "")[:10], f"{item.get('grade') or '?'} / {item.get('out_of') or '?'}"]
        for key in ("coefficient", "class_average", "minimum", "maximum"):
            if item.get(key) is not None:
                lines.append(LABELS[key] + ": " + str(item[key]))
        if item.get("bonus"):
            lines.append("Bonus")
        if item.get("optional"):
            lines.append("Facultatif")
        if item.get("comment"):
            comment = item["comment"]
            lines.append(comment[:120] + ("... (TEXTE)" if len(comment) > 120 else ""))
    elif operation == "homework":
        text = item.get("description") or ""
        lines = [title, (item.get("date") or "")[:10] + (" / fait" if item.get("done") else ""),
                 text[:240] + ("... (TEXTE)" if len(text) > 240 else "")]
        if item.get("attachments"):
            lines.append("Pieces jointes: " + str(len(item["attachments"])))
    elif operation == "announcements":
        lines = [f"{index}. " + (item.get("title") or "Information"), (item.get("date") or "")[:10],
                 "Lu" if item.get("read") else "Non lu"]
    else:
        return details(item)
    return "\n".join(lines)


def render(result, operation, full=False):
    if result.get("error"):
        text = "PRONOTE: " + result["error"]
        if result.get("periods"):
            text += "\nPeriodes: " + " / ".join(result["periods"])
        return narrow(text)
    lines = ["PRONOTE / " + result.get("child", ""), result.get("period", "")]
    items = result.get("items", [])
    if not items:
        lines.append("Aucun enregistrement publie.")
    weekly = operation == "timetable" and result.get("from") != result.get("to") and not full
    if weekly:
        days = {}
        for item in items:
            days.setdefault(item.get("start", "")[:10], []).append(item)
        for day, lessons in sorted(days.items()):
            lines.append(f"{day}: {len(lessons)} cours")
            for lesson in lessons:
                if lesson.get("canceled") or lesson.get("test") or lesson.get("status"):
                    lines.append("  " + (lesson.get("subject") or "Cours") + ": "
                                 + ("annule" if lesson.get("canceled") else "controle" if lesson.get("test") else lesson["status"]))
        lines.append("Date ISO pour le detail d'un jour.")
    for index, item in enumerate([] if weekly else items, 1):
        if operation == "briefing":
            lines.append("\n" + item["component"].upper())
            if item.get("error"):
                lines.append("Indisponible: " + item["error"])
            else:
                lines.extend((details(record) if full else compact(record, item["component"], number)) + "\n"
                             for number, record in enumerate(item.get("items", []), 1))
            if item.get("truncated"):
                lines.append("Resultats tronques.")
            if item.get("warning"):
                lines.append(item["warning"])
        elif operation in ("announcements", "report", "evaluations") and not full:
            lines.append(f"{index}. " + " / ".join(str(item[k]) for k in ("date", "subject", "title", "student_average") if item.get(k)))
        else:
            lines.append("\n" + (f"{index}. " + details(item) if full else compact(item, operation, index)))
    if result.get("comments"):
        comment = " / ".join(result["comments"])
        lines.append("Appreciation generale: " + (comment if full else comment[:160] + ("... (TEXTE)" if len(comment) > 160 else "")))
    for key in ("warning", "retrieved_at"):
        if result.get(key):
            lines.append(("Consulte: " if key == "retrieved_at" else "") + result[key])
    if result.get("stale"):
        lines.append("CACHE POSSIBLEMENT PERIME")
    elif result.get("cached"):
        lines.append("Cache local")
    if result.get("truncated"):
        lines.append("Resultats tronques.")
    if result.get("partial"):
        lines.append("Resultat partiellement indisponible.")
    if not full:
        if operation in ("announcements", "report", "evaluations") and items:
            lines.append("LIRE numero / TEXTE / IMPRIMER")
        elif operation in ("timetable", "homework", "tests", "menus"):
            lines.append("DEMAIN / SEMAINE / IMPRIMER")
            lines.append("LIRE numero / TEXTE")
        elif operation == "grades":
            lines.append("Matiere / TEXTE / IMPRIMER")
        elif operation == "briefing":
            lines.append("TEXTE / IMPRIMER / INFOS")
    return narrow("\n".join(lines))


class Session:
    def __init__(self, backend=None):
        self.backend = backend or pronote.backend()
        self.mode = False
        self.child = None
        self.operation = None
        self.last = None
        self.selecting = False
        self.scope = None
        self.grade_view = False
        self.grade_items = []
        self.grade_index = 0

    def reset(self):
        self.__init__(self.backend)

    def clear_grades(self):
        self.grade_view = False
        self.grade_items = []
        self.grade_index = 0

    def open_grades(self, items):
        self.grade_items = items
        self.grade_index = 0
        self.grade_view = bool(items)
        return self.grade_page() if items else render(self.last, "grades")

    def grade_page(self):
        text = render({**self.last, "items": [self.grade_items[self.grade_index]]}, "grades")
        text = text.replace("\n1. ", f"\n{self.grade_index + 1}. ", 1)
        text = text.replace("Matiere / TEXTE / IMPRIMER", "")
        state = "CACHE POSSIBLEMENT PERIME\n" if self.last.get("stale") else ""
        if self.last.get("truncated"):
            state += "Liste tronquee\n"
        return narrow(f"Note {self.grade_index + 1}/{len(self.grade_items)}\n" + state + text.rstrip()
                      + "\nSUITE:suiv / RETOUR:prec / QUITTER")

    def help(self):
        return narrow("PRONOTE" + (" / " + self.child if self.child else "") + "\n"
                      + "NOTES / MOYENNES / DEVOIRS\nCOURS / CONTROLES / BULLETIN\n"
                      "COMPETENCES / ABSENCES / RETARDS\nINFOS / CANTINE / BILAN / SANCTIONS\n"
                      "ENFANT / AIDE / RETOUR")

    def children(self, aliases):
        if not aliases:
            return "PRONOTE: aucun enfant autorise.\nConfigurer les alias dans l'admin."
        return narrow("Choisir un enfant:\n" + "\n".join(f"{i}. {a}" for i, a in enumerate(aliases, 1)))

    def fallback(self, text):
        context = {"operation": self.operation}
        if self.last:
            context.update({key: self.last[key] for key in ("from", "to", "period") if key in self.last})
        return {"fallback": text, "child": self.child, "context": context}

    def prepare(self, text):
        """Return text, a validated-service request, or one scoped LLM fallback.

        No network or authentication here; terminal waiting remains cancellable.
        """
        word = normalize(text)
        if not self.mode and word != "PRONOTE":
            return None
        # Leaving a local mode must never depend on a school/configuration read.
        if word in ("RETOUR", "SORTIR", "QUITTER"):
            self.reset()
            return {"display": "Retour a la conversation."}
        try:
            config = self.backend.config()
        except pronote.PronoteError as error:
            self.clear_grades()
            return {"display": "PRONOTE: " + str(error)}
        if not config.get("enabled"):
            self.reset()
            return {"display": "PRONOTE: disabled"}
        aliases = list(config["children"])
        # Reconfiguration invalidates selected-child data, including print/detail.
        if self.scope != config:
            self.last = None
            self.child = None
            self.clear_grades()
            self.scope = copy.deepcopy(config)
        if self.child not in aliases:
            self.child = aliases[0] if len(aliases) == 1 else None
            self.last = None
            self.clear_grades()
        self.mode = True
        if word in ("SUIVANT", "SUITE", "PRECEDENT"):
            if not self.grade_view:
                return {"display": "Aucune note affichee. Tapez NOTES."}
            if word == "PRECEDENT" and self.grade_index == 0:
                self.clear_grades()
                return {"display": self.help()}
            step = -1 if word == "PRECEDENT" else 1
            self.grade_index = min(len(self.grade_items) - 1, self.grade_index + step)
            return {"display": self.grade_page()}
        selected_grades = self.grade_items if self.grade_view else None
        if word != "IMPRIMER":
            self.clear_grades()
        if word in ("PRONOTE", "AIDE", "HELP"):
            return {"display": self.help() + ("\n" + self.children(aliases) if not self.child else "")}
        if word == "ENFANT":
            self.selecting = True
            self.last = None
            return {"display": self.children(aliases)}
        matched = [a for a in aliases if normalize(a) == word]
        if self.selecting or self.child is None:
            if word.isdigit() and 1 <= int(word) <= len(aliases):
                matched = [aliases[int(word) - 1]]
            if len(matched) == 1:
                self.child = matched[0]
                self.selecting = False
                self.last = None
                self.operation = None
                return {"display": self.help()}
            return {"display": self.children(aliases)}
        if word in ("TEXTE", "IMPRIMER") or re.fullmatch(r"LIRE \d+", word):
            if not self.last:
                return {"display": "Aucun resultat selectionne."}
            if word.startswith("LIRE "):
                index = int(word.split()[1]) - 1
                items = selected_grades if selected_grades is not None else self.last.get("items", [])
                if not 0 <= index < len(items):
                    return {"display": "Numero indisponible."}
                return {"display": narrow(details(items[index]))}
            result = {**self.last, "items": selected_grades} if selected_grades is not None else self.last
            return {"display": render(result, self.operation, full=True), "print": word == "IMPRIMER"}
        command, _, suffix = word.partition(" ")
        command = ALIASES.get(command, command)
        operation = COMMANDS.get(command)
        if operation:
            if operation != self.operation:
                self.last = None
            self.operation = operation
        else:
            operation = self.operation
            suffix = word
        if not operation:
            return self.fallback(text)
        args = {"child": self.child}
        if operation == "report" and not suffix:
            args["period"] = "published"
        if operation in DATED:
            day = pronote.today()
            end = None
            weekdays = {name: i for i, name in enumerate(("LUNDI", "MARDI", "MERCREDI", "JEUDI", "VENDREDI", "SAMEDI", "DIMANCHE"))}
            if suffix in weekdays:
                day += timedelta(days=(weekdays[suffix] - day.weekday()) % 7)
            elif suffix in ("DEMAIN",):
                day += timedelta(days=1)
            elif suffix in ("HIER",):
                day -= timedelta(days=1)
            elif suffix == "SEMAINE":
                day -= timedelta(days=day.weekday())
                end = day + timedelta(days=6)
            elif suffix in ("AUJOURD'HUI", "AUJOURDHUI", "AUJOURD HUI"):
                pass
            elif suffix:
                try:
                    day = date.fromisoformat(suffix)
                except ValueError:
                    return self.fallback(text)
            else:
                # Use backend defaults for homework, tests and recent news.
                return {"tool": "pronote_get_" + operation, "arguments": args, "operation": operation}
            args.update({"from": day.isoformat(), "to": (end or day).isoformat()})
        elif suffix and (command in COMMANDS or suffix.startswith("PERIODE ")):
            # Preserve original case/accent for an exact period name.
            selector = text.strip().partition(" ")[2]
            known = self.last.get("periods", []) if self.last else []
            args["period"] = next((p for p in known if normalize(p) == normalize(selector)), selector)
        elif suffix:
            if self.last and self.last.get("items") and operation == "grades":
                subject_filter = "MATH" if suffix == "MATHS" else suffix
                filtered = [i for i in self.last["items"] if subject_filter in normalize(i.get("subject") or "")]
                if filtered:
                    return {"display": self.open_grades(filtered)}
            return self.fallback(text)
        return {"tool": "pronote_get_" + operation, "arguments": args, "operation": operation}

    def receive(self, result, operation):
        self.operation = operation
        self.last = copy.deepcopy(result)
        self.clear_grades()
        if operation == "grades" and not result.get("error"):
            return self.open_grades(self.last.get("items", []))
        return render(result, operation)


class ScopedBackend:
    """Restrict a direct-mode LLM fallback to the selected authorized alias."""
    def __init__(self, backend, child):
        self.backend, self.child = backend, child

    def config(self):
        config = self.backend.config()
        children = config.get("children", {})
        return {**config, "children": {self.child: children[self.child]} if self.child in children else {}}

    def tools(self):
        if not self.config()["children"]:
            return []
        tools = copy.deepcopy(self.backend.tools())
        for tool in tools:
            properties = tool["function"]["parameters"]["properties"]
            if "child" in properties:
                properties["child"]["enum"] = [self.child]
        return tools

    def execute(self, name, arguments, deadline=None):
        if isinstance(arguments, str):
            import json
            try:
                arguments = json.loads(arguments)
            except ValueError:
                return {"error": "invalid_arguments"}
        if name == "pronote_list_children" and arguments == {}:
            return {"children": list(self.config()["children"])}
        if not isinstance(arguments, dict) or arguments.get("child") != self.child:
            return {"error": "unknown_child"}
        return self.backend.execute(name, arguments, deadline)
