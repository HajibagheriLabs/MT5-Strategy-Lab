"""MQL5 input parsing and .set file reading and writing.

Inputs are read from the source: `input` and `sinput` declarations, their trailing `//` comment
(which the tester shows as the input's name), `input group "..."` headings, and enums declared in
the same file or in a header shipped with it. Values are expressed the way the tester writes them
in a .set file: numbers as written, booleans as true/false, enums and colours as integers,
datetimes as seconds since 1970. The .set layout was taken from files the tester saved itself; see
DECISIONS.md.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from strategylab.config import decode_mt_text
from strategylab.mql5_constants import COLOR_VALUES, STANDARD_ENUMS

Kind = Literal["bool", "integer", "real", "string", "datetime", "color", "enum", "unknown"]

INTEGER_TYPES = {"char", "uchar", "short", "ushort", "int", "uint", "long", "ulong"}
REAL_TYPES = {"float", "double"}
SIMPLE_KINDS: dict[str, Kind] = {
    **dict.fromkeys(INTEGER_TYPES, "integer"),
    **dict.fromkeys(REAL_TYPES, "real"),
    "bool": "bool",
    "string": "string",
    "datetime": "datetime",
    "color": "color",
}

NO_SOURCE_NOTE = (
    "This strategy is a compiled .ex5 without its source, so its inputs cannot be read from it. "
    "Upload a .set file saved from the Strategy Tester's Inputs tab to choose their values; "
    "without one the EA runs with the defaults built into it."
)


class ParamsError(ValueError):
    pass


@dataclass(frozen=True)
class EnumMember:
    name: str
    value: int | None
    label: str | None = None


@dataclass(frozen=True)
class InputParam:
    name: str
    mql_type: str
    kind: Kind
    default: str
    """The default exactly as written in the source."""
    value: str | None
    """The default as the tester writes it in a .set file; None when it cannot be worked out."""
    label: str | None = None
    group: str | None = None
    static: bool = False
    """Declared with `sinput`: shown in the tester but never optimised."""
    members: tuple[EnumMember, ...] = ()
    line: int = 0


# --- source parsing ----------------------------------------------------------------------------

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_STRING = r'"(?:[^"\\\n]|\\.)*"'
_STATEMENT_START = re.compile(r"^[ \t]*(?P<keyword>sinput|input)[ \t]+", re.M)
_GROUP_HEAD = re.compile(r"group\b")
_GROUP = re.compile(rf"^group\s+(?P<title>{_STRING})")
_ENUM = re.compile(r"\benum\s+(?P<name>\w+)\s*\{(?P<body>.*?)\}\s*;", re.S)
_INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"(?P<path>[^"\r\n]+)"', re.M)
_DATETIME = re.compile(r"^D'(?P<text>[^']*)'$")
_COLOR_RGB = re.compile(r"^C'\s*(?P<r>\d+)\s*,\s*(?P<g>\d+)\s*,\s*(?P<b>\d+)\s*'$")
_INT_LITERAL = re.compile(r"^[-+]?(?:0[xX][0-9a-fA-F]+|\d+)$")
_REAL_LITERAL = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


def _strip_block_comments(source: str) -> str:
    # Keep line numbers stable by replacing each comment with its own newlines.
    return _BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), source)


def _trailing_label(trail: str) -> str | None:
    marker = trail.find("//")
    if marker < 0:
        return None
    label = trail[marker + 2 :].strip()
    return label or None


def _literal_end(text: str, index: int) -> int:
    """Index just past the string or character literal starting at text[index]."""
    quote = text[index]
    index += 1
    while index < len(text) and text[index] != quote and text[index] != "\n":
        index += 2 if text[index] == "\\" else 1
    return index + 1


def _split_top_level(text: str, separator: str = ",") -> list[str]:
    """Split on separators outside brackets and literals ("a,b", 'x', C'1,2,3', D'...')."""
    parts: list[str] = []
    depth, start, index = 0, 0, 0
    while index < len(text):
        ch = text[index]
        if ch in "\"'":
            index = _literal_end(text, index)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == separator and depth == 0:
            parts.append(text[start:index])
            start = index + 1
        index += 1
    parts.append(text[start:])
    return parts


@dataclass(frozen=True)
class _Statement:
    keyword: str
    body: str
    trail: str
    offset: int


def _statements(text: str) -> list[_Statement]:
    """Every `input`/`sinput` statement: a group heading, or a declaration up to its semicolon.

    Group headings take no semicolon. A declaration may run over several lines and may contain
    semicolons inside literals; a line comment inside it is skipped. Whatever follows the
    semicolon on the same line is kept, since it may hold the input's label comment.
    """
    found = []
    for match in _STATEMENT_START.finditer(text):
        begin = match.end()
        line_end = text.find("\n", begin)
        line_end = len(text) if line_end < 0 else line_end
        if _GROUP_HEAD.match(text, begin):
            found.append(_Statement("group", text[begin:line_end], "", match.start()))
            continue
        index, body = begin, []
        while index < len(text):
            ch = text[index]
            if ch in "\"'":
                end = _literal_end(text, index)
                body.append(text[index:end])
                index = end
                continue
            if text.startswith("//", index):
                newline = text.find("\n", index)
                index = len(text) if newline < 0 else newline
                continue
            if ch == ";":
                break
            if ch == "\n" and _STATEMENT_START.match(text, index + 1):
                index = len(text)
                break
            body.append(ch)
            index += 1
        if index >= len(text):
            continue
        trail_end = text.find("\n", index)
        trail_end = len(text) if trail_end < 0 else trail_end
        found.append(
            _Statement(match["keyword"], "".join(body), text[index + 1 : trail_end], match.start())
        )
    return found


def _int_literal(text: str) -> int | None:
    text = text.strip()
    if not _INT_LITERAL.match(text):
        return None
    sign = -1 if text.startswith("-") else 1
    digits = text.lstrip("+-")
    return sign * (int(digits, 16) if digits.lower().startswith("0x") else int(digits))


def parse_enums(source: str) -> dict[str, tuple[EnumMember, ...]]:
    """Enums declared in the source, with each member's value and its `//` label."""
    enums: dict[str, tuple[EnumMember, ...]] = {}
    for match in _ENUM.finditer(_strip_block_comments(source)):
        members: list[EnumMember] = []
        next_value: int | None = 0
        lines = match["body"].splitlines()
        # A member's label is the line comment on the line where the member is written.
        entries: list[tuple[str, str | None]] = []
        for raw_line in lines:
            code, _, comment = raw_line.partition("//")
            label = comment.strip() or None
            for piece in code.split(","):
                if piece.strip():
                    entries.append((piece.strip(), label))
        for text, label in entries:
            name, _, expression = text.partition("=")
            name = name.strip()
            value = next_value
            if expression.strip():
                literal = _int_literal(expression)
                known = {m.name: m.value for m in members}
                value = literal if literal is not None else known.get(expression.strip())
            members.append(EnumMember(name, value, label))
            next_value = value + 1 if value is not None else None
        enums[match["name"]] = tuple(members)
    return enums


def _unquote(literal: str) -> str | None:
    literal = literal.strip()
    if len(literal) < 2 or literal[0] != '"' or literal[-1] != '"':
        return None
    body = literal[1:-1]
    escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'"}
    out, index = [], 0
    while index < len(body):
        ch = body[index]
        if ch == "\\" and index + 1 < len(body):
            out.append(escapes.get(body[index + 1], body[index + 1]))
            index += 2
        else:
            out.append(ch)
            index += 1
    return "".join(out)


def _format_real(value: float) -> str:
    return repr(float(value))


def _datetime_value(text: str) -> int | None:
    stripped = text.strip()
    for pattern in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d"):
        try:
            moment = datetime.strptime(stripped, pattern)
        except ValueError:
            continue
        # The tester writes datetime inputs as seconds since 1970 counted in the same clock as
        # the text, i.e. server time stored as if it were UTC.
        return int(moment.replace(tzinfo=UTC).timestamp())
    return None


def resolve_value(
    kind: Kind,
    default: str,
    members: tuple[EnumMember, ...] = (),
) -> str | None:
    """The tester's .set form of a default expression, or None if it is not a plain constant."""
    text = default.strip()
    if kind == "bool":
        lowered = text.lower()
        if lowered in ("true", "false"):
            return lowered
        number = _int_literal(text)
        return None if number is None else ("true" if number else "false")
    if kind == "integer":
        number = _int_literal(text)
        return None if number is None else str(number)
    if kind == "real":
        candidate = text.rstrip("fF") if "." in text or "e" in text.lower() else text
        if _REAL_LITERAL.match(candidate):
            return _format_real(float(candidate))
        return None
    if kind == "string":
        return _unquote(text)
    if kind == "datetime":
        match = _DATETIME.match(text)
        if match:
            seconds = _datetime_value(match["text"])
            return None if seconds is None else str(seconds)
        number = _int_literal(text)
        return None if number is None else str(number)
    if kind == "color":
        match = _COLOR_RGB.match(text)
        if match:
            red, green, blue = int(match["r"]), int(match["g"]), int(match["b"])
            return str(red | (green << 8) | (blue << 16))
        if text in COLOR_VALUES:
            return str(COLOR_VALUES[text])
        number = _int_literal(text)
        return None if number is None else str(number & 0xFFFFFFFF)
    if kind == "enum":
        for member in members:
            if member.name == text and member.value is not None:
                return str(member.value)
        number = _int_literal(text)
        return None if number is None else str(number)
    return None


def _kind_and_members(
    mql_type: str, enums: Mapping[str, tuple[EnumMember, ...]]
) -> tuple[Kind, tuple[EnumMember, ...]]:
    if mql_type in SIMPLE_KINDS:
        return SIMPLE_KINDS[mql_type], ()
    if mql_type in enums:
        return "enum", enums[mql_type]
    if mql_type in STANDARD_ENUMS:
        members = tuple(EnumMember(n, v) for n, v in STANDARD_ENUMS[mql_type].items())
        return "enum", members
    if mql_type.startswith("ENUM_"):
        return "enum", ()
    return "unknown", ()


def parse_inputs(
    source: str,
    enums: Mapping[str, tuple[EnumMember, ...]] | None = None,
    group: str | None = None,
    first_line: int = 1,
) -> tuple[list[InputParam], str | None]:
    """Inputs declared in one source text, in order, and the input group in force at its end."""
    known = dict(enums or {})
    known.update(parse_enums(source))
    text = _strip_block_comments(source)
    params: list[InputParam] = []
    for statement in _statements(text):
        body = " ".join(statement.body.split())
        line = text.count("\n", 0, statement.offset) + first_line
        if statement.keyword == "group":
            group_match = _GROUP.match(body)
            group = _unquote(group_match["title"]) if group_match else None
            continue
        static = statement.keyword == "sinput"
        tokens = body.split(None, 1)
        if len(tokens) < 2:
            continue
        mql_type, declarators = tokens
        if mql_type == "const":
            mql_type, _, declarators = declarators.partition(" ")
        kind, members = _kind_and_members(mql_type, known)
        label = _trailing_label(statement.trail)
        for declarator in _split_top_level(declarators):
            name, sep, default = declarator.partition("=")
            name = name.strip()
            if not re.match(r"^[A-Za-z_]\w*$", name):
                continue
            default = default.strip() if sep else ""
            params.append(
                InputParam(
                    name=name,
                    mql_type=mql_type,
                    kind=kind,
                    default=default,
                    value=resolve_value(kind, default, members) if default else None,
                    label=label,
                    group=group,
                    static=static,
                    members=members,
                    line=line,
                )
            )
    return params, group


def read_source(path: Path) -> str:
    return decode_mt_text(path.read_bytes())


def discover_inputs(entry: Path, root: Path | None = None) -> list[InputParam]:
    """Inputs of an EA, following `#include "..."` into headers inside `root` (the upload)."""
    root = root or entry.parent
    enums: dict[str, tuple[EnumMember, ...]] = {}
    params: list[InputParam] = []
    visited: set[Path] = set()

    def visit(path: Path, group: str | None) -> str | None:
        resolved = path.resolve()
        if resolved in visited or not resolved.is_file():
            return group
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            return group
        visited.add(resolved)
        source = read_source(resolved)
        enums.update(parse_enums(source))
        # Headers are parsed where they are included, so their inputs keep the source order.
        position = 0
        for include in _INCLUDE.finditer(source):
            segment = source[position : include.start()]
            line = source.count("\n", 0, position) + 1
            found, group = parse_inputs(segment, enums, group, line)
            params.extend(found)
            target = PurePosixPath(include["path"].replace("\\", "/"))
            group = visit(resolved.parent.joinpath(*target.parts), group)
            position = include.end()
        line = source.count("\n", 0, position) + 1
        found, group = parse_inputs(source[position:], enums, group, line)
        params.extend(found)
        return group

    visit(entry, None)
    return params


# --- .set files --------------------------------------------------------------------------------


@dataclass
class SetEntry:
    name: str
    value: str
    start: str | None = None
    step: str | None = None
    stop: str | None = None
    optimize: bool | None = None

    def render(self) -> str:
        if self.start is None:
            return f"{self.name}={self.value}"
        flag = "Y" if self.optimize else "N"
        return f"{self.name}={self.value}||{self.start}||{self.step}||{self.stop}||{flag}"


@dataclass
class SetFile:
    """A .set file as lines: comments are kept verbatim so a file survives a round trip."""

    lines: list[str | SetEntry] = field(default_factory=list)

    @property
    def entries(self) -> list[SetEntry]:
        return [line for line in self.lines if isinstance(line, SetEntry)]

    def values(self) -> dict[str, str]:
        return {entry.name: entry.value for entry in self.entries}

    def set_value(self, name: str, value: str) -> None:
        for entry in self.entries:
            if entry.name == name:
                entry.value = value
                return
        self.lines.append(SetEntry(name, value))

    def to_text(self) -> str:
        rendered = [line.render() if isinstance(line, SetEntry) else line for line in self.lines]
        return "".join(f"{line}\r\n" for line in rendered)

    def to_bytes(self) -> bytes:
        """UTF-16LE with a BOM and CRLF line ends, as the tester writes them."""
        return codecs.BOM_UTF16_LE + self.to_text().encode("utf-16-le")


def parse_set_text(text: str) -> SetFile:
    lines: list[str | SetEntry] = []
    for raw in text.lstrip("﻿").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(";") or "=" not in stripped:
            lines.append(raw)
            continue
        name, _, rest = raw.partition("=")
        parts = rest.split("||")
        if len(parts) == 5:
            value, start, step, stop, flag = parts
            entry = SetEntry(name.strip(), value, start, step, stop, flag.strip().upper() == "Y")
        else:
            entry = SetEntry(name.strip(), rest)
        lines.append(entry)
    return SetFile(lines)


def read_set(path: Path) -> SetFile:
    try:
        return parse_set_text(decode_mt_text(path.read_bytes()))
    except OSError as exc:
        raise ParamsError(f"Cannot read {path}: {exc}") from exc


def entry_for(param: InputParam, value: str) -> SetEntry:
    """A .set line for an input, with the default optimisation range the tester itself writes.

    Strings, colours and sinput inputs are written as plain name=value. Other kinds get
    value||start||step||stop||N. The range is derived from the compiled default, not from the
    value being set (the tester writes MovingPeriod=24||12||1||120 for a default of 12): for
    numbers and datetimes start is the default, step 1 (a tenth of the default for reals) and
    stop ten times the default; booleans get false..true; enums their lowest..highest member
    with step 0.
    """
    if param.static or param.kind in ("string", "color", "unknown"):
        return SetEntry(param.name, value)
    if param.kind == "bool":
        return SetEntry(param.name, value, "false", "0", "true", False)
    if param.kind == "enum":
        values = [m.value for m in param.members if m.value is not None]
        if not values:
            return SetEntry(param.name, value)
        return SetEntry(param.name, value, str(min(values)), "0", str(max(values)), False)
    base = param.value if param.value is not None else value
    if param.kind == "real":
        number = float(base)
        return SetEntry(param.name, value, base, f"{number / 10:.6f}", f"{number * 10:.6f}", False)
    return SetEntry(param.name, value, base, "1", str(int(base) * 10), False)


def build_set(
    params: Iterable[InputParam],
    overrides: Mapping[str, str] | None = None,
    title: str | None = None,
) -> SetFile:
    """A complete .set file for these inputs; inputs whose value is unknown are left out, so the
    tester uses the default compiled into the EA for them."""
    overrides = dict(overrides or {})
    names = set()
    lines: list[str | SetEntry] = []
    if title:
        lines += [f"; {title}", ";"]
    group: str | None = None
    for param in params:
        names.add(param.name)
        if param.group != group and param.group is not None:
            lines.append(f"; {param.group}")
        group = param.group
        value = overrides.pop(param.name, param.value)
        if value is None:
            continue
        lines.append(entry_for(param, value))
    if overrides:
        unknown = ", ".join(sorted(overrides))
        raise ParamsError(f"The strategy has no inputs named {unknown}.")
    return SetFile(lines)


def validate_value(param: InputParam, value: str) -> str:
    """Check a value given for an input and return it in the tester's form."""
    text = value.strip()
    if param.kind == "integer" and _int_literal(text) is None:
        raise ParamsError(f"{param.name} needs a whole number, got '{value}'.")
    if param.kind == "real":
        try:
            return _format_real(float(text))
        except ValueError as exc:
            raise ParamsError(f"{param.name} needs a number, got '{value}'.") from exc
    if param.kind == "bool":
        if text.lower() not in ("true", "false"):
            raise ParamsError(f"{param.name} needs true or false, got '{value}'.")
        return text.lower()
    if param.kind == "enum" and param.members:
        by_name = {m.name: m.value for m in param.members}
        if text in by_name and by_name[text] is not None:
            return str(by_name[text])
        allowed = {str(m.value) for m in param.members if m.value is not None}
        if text not in allowed:
            names = ", ".join(m.name for m in param.members)
            raise ParamsError(f"{param.name} must be one of {names}, got '{value}'.")
    if param.kind in ("datetime", "color"):
        resolved = resolve_value(param.kind, text, param.members)
        if resolved is None:
            raise ParamsError(f"{param.name} could not be read from '{value}'.")
        return resolved
    if any(ch in text for ch in "\r\n"):
        raise ParamsError(f"{param.name} cannot contain a line break.")
    return text if param.kind != "string" else value


@dataclass(frozen=True)
class ParameterDiscovery:
    params: list[InputParam]
    source: Literal["source", "set_file", "none"]
    note: str | None = None


def params_from_set(set_file: SetFile) -> list[InputParam]:
    """Inputs as far as a .set file tells: names and values, but no types or labels."""
    return [
        InputParam(name=e.name, mql_type="", kind="unknown", default=e.value, value=e.value)
        for e in set_file.entries
    ]


def discover(entry: Path, root: Path, set_file: Path | None = None) -> ParameterDiscovery:
    """Inputs of a staged strategy: from its source, or for a bare .ex5 from a .set file."""
    if entry.suffix.lower() == ".mq5":
        return ParameterDiscovery(discover_inputs(entry, root), "source")
    if set_file is not None:
        note = (
            "Inputs come from the uploaded .set file. Their types and labels are not known "
            "without the source, so values are passed to the tester exactly as written."
        )
        return ParameterDiscovery(params_from_set(read_set(set_file)), "set_file", note)
    return ParameterDiscovery([], "none", NO_SOURCE_NOTE)
