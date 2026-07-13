"""Small, dependency-free dimensional quantities and safe parameter expressions.

The v2 YAML format deliberately avoids arbitrary SIMetrix expressions.  Values are
evaluated before script generation using only quantities, named parameters, and
arithmetic operators.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Mapping

from .errors import ValidationError


Dimension = tuple[int, int, int]  # voltage, current, time exponents
DIMENSIONLESS: Dimension = (0, 0, 0)

DIMENSIONS: dict[str, Dimension] = {
    "dimensionless": DIMENSIONLESS,
    "none": DIMENSIONLESS,
    "voltage": (1, 0, 0),
    "current": (0, 1, 0),
    "time": (0, 0, 1),
    "frequency": (0, 0, -1),
    "resistance": (1, -1, 0),
    "capacitance": (-1, 1, 1),
    "inductance": (1, -1, 1),
}

UNIT_DIMENSIONS: dict[str, Dimension] = {
    "": DIMENSIONLESS,
    "V": DIMENSIONS["voltage"],
    "A": DIMENSIONS["current"],
    "s": DIMENSIONS["time"],
    "Hz": DIMENSIONS["frequency"],
    "ohm": DIMENSIONS["resistance"],
    "Ω": DIMENSIONS["resistance"],
    "F": DIMENSIONS["capacitance"],
    "H": DIMENSIONS["inductance"],
}

PREFIXES: dict[str, float] = {
    "": 1.0,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "M": 1e6,
    "G": 1e9,
}

_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_QUANTITY_RE = re.compile(rf"(?P<number>{_NUMBER})(?P<suffix>[A-Za-zΩ]*)$")
_PARAM_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")


def dimension_name(dimension: Dimension) -> str:
    for name, candidate in DIMENSIONS.items():
        if candidate == dimension and name not in {"none"}:
            return name
    return f"V^{dimension[0]} A^{dimension[1]} s^{dimension[2]}"


def parse_dimension(value: str | Dimension | None) -> Dimension:
    if value is None:
        return DIMENSIONLESS
    if isinstance(value, tuple):
        return value
    try:
        return DIMENSIONS[str(value).lower()]
    except KeyError as exc:
        raise ValidationError("Unknown parameter dimension", dimension=value, known=sorted(DIMENSIONS)) from exc


@dataclass(frozen=True)
class Quantity:
    value: float
    dimension: Dimension = DIMENSIONLESS

    def _require_same_dimension(self, other: "Quantity", operator: str) -> None:
        if self.dimension != other.dimension:
            raise ValidationError(
                "Dimension mismatch",
                operator=operator,
                left=dimension_name(self.dimension),
                right=dimension_name(other.dimension),
            )

    def __add__(self, other: "Quantity") -> "Quantity":
        self._require_same_dimension(other, "+")
        return Quantity(self.value + other.value, self.dimension)

    def __sub__(self, other: "Quantity") -> "Quantity":
        self._require_same_dimension(other, "-")
        return Quantity(self.value - other.value, self.dimension)

    def __mul__(self, other: "Quantity") -> "Quantity":
        return Quantity(self.value * other.value, tuple(a + b for a, b in zip(self.dimension, other.dimension)))

    def __truediv__(self, other: "Quantity") -> "Quantity":
        if other.value == 0:
            raise ValidationError("Division by zero in parameter expression")
        return Quantity(self.value / other.value, tuple(a - b for a, b in zip(self.dimension, other.dimension)))

    def __neg__(self) -> "Quantity":
        return Quantity(-self.value, self.dimension)


def _split_suffix(suffix: str) -> tuple[float, Dimension]:
    if suffix.casefold() == "ohm":
        suffix = "ohm"
    elif len(suffix) > 1 and suffix[0] in PREFIXES and suffix[1:].casefold() == "ohm":
        suffix = suffix[0] + "ohm"
    if suffix in UNIT_DIMENSIONS:
        return 1.0, UNIT_DIMENSIONS[suffix]
    if not suffix:
        return 1.0, DIMENSIONLESS
    prefix = suffix[:1]
    unit = suffix[1:]
    if prefix not in PREFIXES or unit not in UNIT_DIMENSIONS:
        raise ValidationError("Unsupported unit suffix", suffix=suffix)
    return PREFIXES[prefix], UNIT_DIMENSIONS[unit]


def parse_quantity(value: str | int | float | Quantity, expected_dimension: str | Dimension | None = None) -> Quantity:
    """Parse a single literal such as ``2.2uH`` or ``12 V``.

    A unitless literal is dimensionless here.  ``evaluate_expression`` can apply an
    expected dimension to a final unitless result, making simple property values
    such as ``VALUE: 10`` convenient while preserving dimensional checks.
    """

    expected = parse_dimension(expected_dimension) if expected_dimension is not None else None
    if isinstance(value, Quantity):
        result = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        result = Quantity(float(value))
    elif isinstance(value, str):
        compact = re.sub(r"\s+", "", value)
        match = _QUANTITY_RE.fullmatch(compact)
        if not match:
            raise ValidationError("Invalid quantity literal", value=value)
        scale, dimension = _split_suffix(match.group("suffix"))
        result = Quantity(float(match.group("number")) * scale, dimension)
    else:
        raise ValidationError("Quantity must be a number or string", value=repr(value))
    if expected is not None and result.dimension == DIMENSIONLESS and expected != DIMENSIONLESS:
        result = Quantity(result.value, expected)
    if expected is not None and result.dimension != expected:
        raise ValidationError(
            "Quantity has the wrong dimension",
            value=str(value),
            expected=dimension_name(expected),
            actual=dimension_name(result.dimension),
        )
    if not math.isfinite(result.value):
        raise ValidationError("Quantity must be finite", value=str(value))
    return result


def format_simplis(value: Quantity) -> str:
    """Render a normalized numeric value suitable for a SIMPLIS scalar property."""

    if value.value == 0:
        return "0"
    return format(value.value, ".12g")


class _ExpressionParser:
    def __init__(self, text: str, parameters: Mapping[str, Quantity]) -> None:
        self.text = text
        self.parameters = parameters
        self.tokens = self._tokenize(text)
        self.index = 0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens: list[str] = []
        index = 0
        while index < len(text):
            if text[index].isspace():
                index += 1
                continue
            if text.startswith("${", index):
                match = _PARAM_RE.match(text, index)
                if not match:
                    raise ValidationError("Invalid parameter reference", expression=text, position=index)
                tokens.append(match.group(0))
                index = match.end()
                continue
            if text[index] in "+-*/()":
                tokens.append(text[index])
                index += 1
                continue
            match = re.match(rf"{_NUMBER}(?:\s*[A-Za-zΩ]+)?", text[index:])
            if match:
                token = re.sub(r"\s+", "", match.group(0))
                tokens.append(token)
                index += len(token)
                continue
            raise ValidationError("Unsupported token in parameter expression", expression=text, position=index)
        return tokens

    def _peek(self) -> str | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self) -> str:
        token = self._peek()
        if token is None:
            raise ValidationError("Unexpected end of parameter expression", expression=self.text)
        self.index += 1
        return token

    def parse(self) -> Quantity:
        result = self._parse_sum()
        if self._peek() is not None:
            raise ValidationError("Unexpected token in parameter expression", expression=self.text, token=self._peek())
        return result

    def _parse_sum(self) -> Quantity:
        value = self._parse_product()
        while self._peek() in {"+", "-"}:
            operator = self._take()
            right = self._parse_product()
            value = value + right if operator == "+" else value - right
        return value

    def _parse_product(self) -> Quantity:
        value = self._parse_unary()
        while self._peek() in {"*", "/"}:
            operator = self._take()
            right = self._parse_unary()
            value = value * right if operator == "*" else value / right
        return value

    def _parse_unary(self) -> Quantity:
        token = self._peek()
        if token == "+":
            self._take()
            return self._parse_unary()
        if token == "-":
            self._take()
            return -self._parse_unary()
        if token == "(":
            self._take()
            value = self._parse_sum()
            if self._take() != ")":
                raise ValidationError("Unbalanced parentheses", expression=self.text)
            return value
        token = self._take()
        if token.startswith("${"):
            name = token[2:-1]
            try:
                return self.parameters[name]
            except KeyError as exc:
                raise ValidationError("Unknown parameter reference", parameter=name, expression=self.text) from exc
        return parse_quantity(token)


def evaluate_expression(
    value: str | int | float | Quantity,
    parameters: Mapping[str, Quantity] | None = None,
    expected_dimension: str | Dimension | None = None,
) -> Quantity:
    """Evaluate an allowed parameter expression and validate its final dimension."""

    expected = parse_dimension(expected_dimension) if expected_dimension is not None else None
    if isinstance(value, str) and ("${" in value or any(character in value for character in "+-*/()")):
        result = _ExpressionParser(value, parameters or {}).parse()
    else:
        result = parse_quantity(value)
    if expected is not None and result.dimension == DIMENSIONLESS and expected != DIMENSIONLESS:
        result = Quantity(result.value, expected)
    if expected is not None and result.dimension != expected:
        raise ValidationError(
            "Parameter expression has the wrong dimension",
            expression=str(value),
            expected=dimension_name(expected),
            actual=dimension_name(result.dimension),
        )
    return result
