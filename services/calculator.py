"""Restricted local arithmetic; one optional model call for the zero-division scene."""

import ast
import json
import logging
import random
import re
import textwrap
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import requests

CONTENT = Path(__file__).resolve().parents[1] / "config" / "calculator.json"
PROMPT = CONTENT.with_name("calculator_prompt.txt")
MAX_INPUT = 256
MAX_NODES = 160
MAX_BITS = 1024
SCENE_TIMEOUT = 12
log = logging.getLogger(__name__)


class ArithmeticProblem(Exception):
    pass


def content(language):
    return json.loads(CONTENT.read_text(encoding="utf-8"))[language]


def candidate(text):
    """Conservative automatic trigger; bare numbers retain existing routing."""
    return bool(
        text.strip()
        and re.fullmatch(r"[0-9.,+*/()\s-]+", text)
        and re.search(r"[+*/()-]", text)
    )


def evaluate(expression):
    if (
        not isinstance(expression, str)
        or len(expression) > MAX_INPUT
        or "\n" in expression
        or "\r" in expression
        or not re.fullmatch(r"[0-9.,+*/() \t-]+", expression)
    ):
        raise ArithmeticProblem("input")
    expression = expression.strip().replace(",", ".")
    nesting = 0
    for char in expression:
        nesting += (char == "(") - (char == ")")
        if nesting > 16 or nesting < 0:
            raise ArithmeticProblem("complexity")
    if any(
        len(number.replace(".", "")) > 80
        for number in re.findall(r"[0-9.]+", expression)
    ):
        raise ArithmeticProblem("number")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        raise ArithmeticProblem("input") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise ArithmeticProblem("complexity")
    allowed = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.UAdd,
        ast.USub,
    )
    # Validate the whole tree BEFORE evaluation (including before a zero divisor).
    for node in nodes:
        if not isinstance(node, allowed):
            raise ArithmeticProblem("input")
        if isinstance(node, ast.Constant) and type(node.value) not in (int, float):
            raise ArithmeticProblem("input")

    def bounded(value):
        if (
            value.numerator.bit_length() > MAX_BITS
            or value.denominator.bit_length() > MAX_BITS
        ):
            raise ArithmeticProblem("number")
        return value

    def visit(node):
        if isinstance(node, ast.Constant):
            return bounded(Fraction(ast.get_source_segment(expression, node)))
        if isinstance(node, ast.UnaryOp):
            value = visit(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        left, right = visit(node.left), visit(node.right)
        if isinstance(node.op, ast.Add):
            value = left + right
        elif isinstance(node.op, ast.Sub):
            value = left - right
        elif isinstance(node.op, ast.Mult):
            value = left * right
        else:
            value = left / right  # ZeroDivisionError only from actual evaluation.
        return bounded(value)

    try:
        return visit(tree.body)
    except (ValueError, RecursionError):
        raise ArithmeticProblem("input") from None


def format_result(value):
    with localcontext() as context:
        context.prec = 6
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        approximate = Fraction(decimal) != value
        exponent = decimal.adjusted() if decimal else 0
        if decimal and (exponent >= 6 or exponent < -4):
            mantissa = format(decimal.scaleb(-exponent), "f")
            if "." in mantissa:
                mantissa = mantissa.rstrip("0").rstrip(".")
            text = mantissa + "e" + str(exponent)
        else:
            text = format(decimal, "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
    return ("~" if approximate else "") + text


def validate_scene(text):
    if not isinstance(text, str) or len(text) > 6500:
        raise ValueError("scene size")
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {
        "escalation",
        "maintenance",
        "recovery",
    }:
        raise ValueError("scene structure")
    total = 0
    for name, minimum, maximum in (
        ("escalation", 8, 22),
        ("maintenance", 4, 9),
        ("recovery", 2, 4),
    ):
        steps = data[name]
        if not isinstance(steps, list) or not minimum <= len(steps) <= maximum:
            raise ValueError("scene stages")
        for step in steps:
            if (
                not isinstance(step, str)
                or not 5 <= len(step) <= 450
                or not all(c.isprintable() for c in step)
                or re.search(
                    r"```|https?://|\b(?:ENVoyer|ENVOYER|CALC|SOMMAIRE)\b", step
                )
            ):
                raise ValueError("scene text")
            letters = "".join(c for c in step if c.isalpha())
            if len(letters) > 12 and letters.isupper():
                raise ValueError("scene case")
            total += len(step)
    if (
        total > 4200
        or len(list(frames([s for steps in data.values() for s in steps]))) > 150
    ):
        raise ValueError("scene size")
    narrative = " ".join(s for steps in data.values() for s in steps).casefold()
    if any(
        anchor not in narrative
        for anchor in ("k7", "k2f", "alcatel", "x.25", "1984", "vacant")
    ):
        raise ValueError("scene arc")
    return data


def generate_scene(expression, language, key, model):
    """One request, no tools/history/memory/account data. Errors use the reference."""
    wording = content(language)
    fallback = wording["zero_scene"]
    if not key or key == "...":
        log.info("calculator scene=fallback reason=missing_key")
        return fallback
    try:
        response = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            timeout=SCENE_TIMEOUT,
            json={
                "model": model,
                "temperature": 0.85,
                "max_tokens": 2400,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": PROMPT.read_text(encoding="utf-8")},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "expression": expression,
                                "language": "English" if language == "en" else "French",
                                "reference": fallback,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            },
        )
        response.raise_for_status()
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("incomplete scene")
        scene = validate_scene(choice["message"]["content"])
        log.info("calculator scene=generated stages=%d", sum(map(len, scene.values())))
        return scene
    except (
        requests.RequestException,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
    ) as error:
        log.warning("calculator scene=fallback reason=%s", type(error).__name__)
        return fallback


def frames(steps):
    for step in steps:
        yield from textwrap.wrap(
            step, width=39, replace_whitespace=False, break_on_hyphens=False
        )
        yield ""


class Session:
    def __init__(self):
        self.mode = False

    def explicit(self, question):
        return self.mode or bool(
            re.match(r"^CALC(?:\s|$)", question.strip(), re.IGNORECASE)
        )

    def handle(self, question, language="fr", generate=None):
        wording = content(language)
        stripped = question.strip()
        if stripped.upper() == "CALC":
            self.mode = True
            return {"display": wording["open"], "turn": None, "calculator": True}
        if self.mode and stripped.upper() in {"RETOUR", "QUITTER", "EXIT"}:
            self.mode = False
            return {"display": wording["close"], "turn": None, "calculator": True}
        explicit = self.explicit(stripped)
        expression = re.sub(r"^CALC\s+", "", stripped, count=1, flags=re.IGNORECASE)
        if not explicit and not candidate(expression):
            return None
        try:
            value = evaluate(expression)
            fact = expression + " = " + format_result(value)
            display = (
                random.choice(wording["ordinary"]) + "\n\n" + wording["result"] + fact
            )
            return {
                "display": display,
                "turn": {"role": "assistant", "content": fact},
                "calculator": True,
                "factual": fact,
            }
        except ZeroDivisionError:
            scene = (
                generate(expression, language) if generate else wording["zero_scene"]
            )
            outcome = wording["undefined"]
            steps = (
                [wording["requested"] + expression]
                + scene["escalation"]
                + [outcome]
                + scene["maintenance"]
                + scene["recovery"]
            )
            fact = expression + ": " + outcome
            return {
                "display": "\n\n".join(steps),
                "scene": steps,
                "calculator": True,
                "factual": fact,
                "turn": {"role": "assistant", "content": fact},
            }
        except ArithmeticProblem as error:
            return {
                "display": wording["errors"][str(error)],
                "turn": None,
                "calculator": True,
            }
