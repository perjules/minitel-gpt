"""Explicit, bounded projections of PronotePy 2.15.7 objects.

No object dictionaries, URLs, identifiers, downloads or write methods cross this
boundary. Imports of pronote are lazy to support the standalone systemd script.
"""
from datetime import date, datetime, timedelta

PERIOD_OPERATIONS = {
    "averages": "Subject averages supplied by PRONOTE; never calculated locally.",
    "report": "Published report, subject comments and general comments. Use period published for the latest published report (up to eight started periods).",
    "evaluations": "Competency evaluations and acquisition levels, distinct from numeric grades.",
    "absences": "Period absences, duration, reasons and justification status.",
    "delays": "Period lateness, minutes, reasons and justification status.",
    "punishments": "Read-only period disciplinary records and detention schedules.",
}
DATE_OPERATIONS = {
    "tests": "Upcoming timetable test flags; no assessment description is provided by this source. Max 7 days.",
    "announcements": "Announcement and survey metadata and read-only content. Max 31 days. Never mark read or answer surveys.",
    "menus": "Published canteen meals for inclusive ISO dates. Max 31 days.",
    "briefing": "Daily briefing: lessons, changes, upcoming tests, homework and recent announcements; partial failures retained. Pass one day.",
}


def module():
    try:
        from . import pronote
    except ImportError:
        import pronote
    return pronote


def scalar(value, limit=500):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, (bool, int, float)):
        return value
    if not isinstance(value, str):
        raise module().PronoteError("malformed_response")
    return module().clean(value, limit)


def fields(obj, names):
    return {output: scalar(getattr(obj, source, None)) for output, source in names.items()}


def strings(values):
    return [scalar(value) for value in (values or [])[:30]]


def attachments(values):
    # Link names can themselves be credential-bearing URLs. Do not return them.
    if not isinstance(values, (list, tuple)):
        return []
    return [{"filename": scalar(a.name, 160), "type": "file"}
            for a in (values or [])[:10] if a.type == 1]


def subject(obj):
    return scalar(getattr(getattr(obj, "subject", None), "name", None), 120)


def lesson(obj):
    return {**fields(obj, {"start": "start", "end": "end", "teacher": "teacher_name",
                          "classroom": "classroom", "canceled": "canceled",
                          "status": "status", "test": "test"}), "subject": subject(obj)}


def grade_extra(obj):
    return fields(obj, {"minimum": "min", "maximum": "max", "bonus": "is_bonus",
                        "optional": "is_optionnal"})


def select_period(client, selector):
    pn = module()
    periods = client.periods
    if selector == "current":
        selected = pn.latest_grade_period(periods)
    else:
        choices = [p for p in periods if p.name == selector]
        selected = choices[0] if len(choices) == 1 else None
    return selected, [pn.clean(p.name, 100) for p in periods]


def collect(client, operation, args):
    """Return normalized items and metadata inside an already scoped session."""
    pn = module()
    extra = {}
    if operation in PERIOD_OPERATIONS:
        if operation == "report" and args["period"] == "published":
            periods = [p for p in client.periods if p.start.date() <= pn.today()]
            ordered = sorted(periods, key=lambda p: (p.start.date(), -(p.end - p.start).days, p.name.casefold()), reverse=True)
            for period in ordered[:8]:
                result, metadata = collect(client, "report", {**args, "period": period.name})
                if metadata.get("error") != "report_unavailable":
                    return result, metadata
            return [], {"error": "report_unavailable", "periods": [pn.clean(p.name, 100) for p in ordered]}
        period, names = select_period(client, args["period"])
        if period is None:
            return [], {"error": "select_period", "periods": names}
        extra["period"] = pn.clean(period.name, 100)
        if operation == "report":
            report = period.report
            if report is None:
                return [], {**extra, "error": "report_unavailable"}
            items = [{**fields(s, {"subject": "name", "student_average": "student_average",
                                   "class_average": "class_average", "minimum": "min_average",
                                   "maximum": "max_average", "coefficient": "coefficient"}),
                      "comments": strings(s.comments), "teachers": strings(s.teachers)}
                     for s in report.subjects]
            extra["comments"] = strings(report.comments)
        else:
            values = getattr(period, operation)
            items = []
            for obj in values[:pn.MAX_ITEMS + 1]:
                if operation == "averages":
                    item = {**fields(obj, {"student_average": "student", "class_average": "class_average",
                                          "minimum": "min", "maximum": "max", "out_of": "out_of"}),
                            "subject": subject(obj)}
                elif operation == "evaluations":
                    item = {**fields(obj, {"title": "name", "date": "date", "teacher": "teacher",
                                          "description": "description", "coefficient": "coefficient"}),
                            "subject": subject(obj),
                            "acquisitions": [fields(a, {"name": "name", "domain": "domain", "level": "level",
                                                        "abbreviation": "abbreviation"})
                                             for a in obj.acquisitions[:30]]}
                elif operation == "absences":
                    item = {**fields(obj, {"start": "from_date", "end": "to_date", "hours": "hours",
                                          "days": "days", "justified": "justified"}), "reasons": strings(obj.reasons)}
                elif operation == "delays":
                    item = {**fields(obj, {"date": "date", "minutes": "minutes", "justified": "justified",
                                          "justification": "justification"}), "reasons": strings(obj.reasons)}
                else:
                    item = {**fields(obj, {"date": "given", "nature": "nature", "circumstances": "circumstances",
                                          "homework": "homework", "duration": "duration"}),
                            "reasons": strings(obj.reasons),
                            "schedule": [fields(s, {"start": "start", "duration": "duration"}) for s in obj.schedule[:20]]}
                items.append(item)
        return items, extra

    start, end = date.fromisoformat(args["from"]), date.fromisoformat(args["to"])
    if operation == "tests":
        items = [lesson(l) for l in client.lessons(start, end) if l.test and not l.canceled]
        extra["warning"] = "Timetable flags only; assessment descriptions unavailable."
    elif operation == "menus":
        items = [{**fields(m, {"date": "date", "name": "name", "lunch": "is_lunch", "dinner": "is_dinner"}),
                  **{course: [scalar(f.name, 160) for f in (getattr(m, course, None) or [])[:20]]
                     for course in ("first_meal", "main_meal", "side_meal", "other_meal", "cheese", "dessert")}}
                 for m in client.menus(start, end)]
    elif operation == "announcements":
        values = client.information_and_surveys()
        values = sorted([v for v in values if start <= (v.start_date or v.creation_date).date() <= end],
                        key=lambda v: v.creation_date, reverse=True)
        items = []
        # Detail calls are bounded separately from list size (Pi / slow network).
        for index, v in enumerate(values[:pn.MAX_ITEMS + 1]):
            item = fields(v, {"title": "title", "author": "author", "date": "creation_date",
                              "category": "category", "read": "read", "survey": "survey"})
            if index < 5:
                try:
                    item["content"] = pn.clean(v.content(), 2000)
                    item["attachments"] = attachments(v.attachments())
                except Exception as error:  # noqa: BLE001 -- secret-free partial result boundary
                    code = error_code(error)
                    if code == "authentication_required":
                        raise pn.PronoteError(code) from None
                    item["content_error"] = code
            else:
                item["content_error"] = "detail_limit"
            items.append(item)
        extra["partial"] = any(i.get("content_error") for i in items)
        if len(values) > 5:
            extra["warning"] = "Content limited to the five newest announcements; narrow the date range for older content."
    elif operation == "briefing":
        items = []
        lessons = None
        lesson_error = None
        try:
            lessons = client.lessons(start, start + timedelta(days=6))
        except Exception as error:  # noqa: BLE001 -- same bounded partial result boundary
            lesson_error = error
        # One login / selected child; no nested execute() or lock acquisition.
        for component in ("timetable", "tests", "homework", "announcements"):
            try:
                until = start if component == "timetable" else start + timedelta(days=6)
                since = start - timedelta(days=7) if component == "announcements" else start
                if component == "announcements":
                    until = start
                scope = {"from": since.isoformat(), "to": until.isoformat()}
                if component == "timetable":
                    if lesson_error:
                        raise lesson_error
                    records = [lesson(l) for l in lessons if l.start.date() == start]
                    metadata = {}
                elif component == "tests":
                    if lesson_error:
                        raise lesson_error
                    records = [lesson(l) for l in lessons if l.test and not l.canceled]
                    metadata = {"warning": "Timetable flags only; assessment descriptions unavailable."}
                elif component == "homework":
                    records = [{**fields(h, {"date": "date", "description": "description", "done": "done"}),
                                "subject": subject(h), "attachments": attachments(getattr(h, "files", []))}
                               for h in client.homework(start, until)]
                    metadata = {}
                else:
                    records, metadata = collect(client, component, scope)
                items.append({"component": component, "items": records[:20],
                              "truncated": len(records) > 20, **metadata})
            except Exception as error:  # noqa: BLE001 -- secret-free partial result boundary
                code = error_code(error)
                if code == "authentication_required":
                    raise pn.PronoteError(code) from None
                items.append({"component": component, "error": code})
        extra["partial"] = any(i.get("error") or any(r.get("content_error") for r in i.get("items", [])) for i in items)
    else:
        raise pn.PronoteError("unsupported_tool")
    return items, extra


def error_code(error):
    if isinstance(error, module().PronoteError):
        return str(error)
    name = type(error).__name__
    if getattr(error, "pronote_error_code", None) in (10, 22):
        return "authentication_required"
    if getattr(getattr(error, "response", None), "status_code", None) == 403:
        return "permission_denied"
    if name == "UnsupportedOperation":
        return "feature_unavailable"
    if name in ("CryptoError", "ExpiredObject", "MFAError"):
        return "authentication_required"
    if name in ("ParsingError", "DataError", "KeyError", "AttributeError", "TypeError", "ValueError"):
        return "malformed_response"
    if name in ("Timeout", "ReadTimeout", "ConnectTimeout"):
        return "timeout"
    return "unavailable"
