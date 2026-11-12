"""Render chat message content: escaped HTML plus a small safe markup subset.

Supports GFM-style tables, **bold**, and `code`. HTML in the input is always
escaped first, so this is safe for both user and assistant messages. Anything
that is not a well-formed table stays plain text (the chat bubble keeps
``white-space: pre-wrap``, so newlines still break).
"""
import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`\n]+)`")
_DELIM_CELL = re.compile(r":?-{1,}:?")


def _split_row(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_delimiter(line):
    cells = _split_row(line)
    return bool(cells) and all(_DELIM_CELL.fullmatch(cell) for cell in cells)


def _align_of(cell):
    left, right = cell.startswith(":"), cell.endswith(":")
    if left and right:
        return "center"
    if right:
        return "right"
    return "left"


def _inline(text):
    text = _CODE.sub(r"<code>\1</code>", text)
    return _BOLD.sub(r"<strong>\1</strong>", text)


def _render_table(header, aligns, rows):
    width = len(header)

    def cell(tag, content, align):
        style = f' style="text-align:{align}"' if align != "left" else ""
        return f"<{tag}{style}>{_inline(content)}</{tag}>"

    head = "".join(cell("th", h, a) for h, a in zip(header, aligns))
    body = "".join(
        "<tr>"
        + "".join(cell("td", c, a) for c, a in zip((row + [""] * width)[:width], aligns))
        + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


@register.filter
def chat_markdown(value):
    lines = escape(str(value or "")).split("\n")
    out = []
    i = 0
    while i < len(lines):
        if "|" in lines[i] and i + 1 < len(lines) and _is_delimiter(lines[i + 1]):
            header = _split_row(lines[i])
            aligns = [_align_of(c) for c in _split_row(lines[i + 1])]
            aligns += ["left"] * (len(header) - len(aligns))
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            out.append(_render_table(header, aligns, rows))
        else:
            out.append(_inline(lines[i]))
            i += 1
    return mark_safe("\n".join(out))
