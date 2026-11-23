"""Reduce RE/MAX listing descriptions to a small, attribute-free HTML subset.

RE/MAX descriptions are pasted from word processors and other portals, so they carry inline
styles, tracking classes, wrapper spans and occasionally embedded media. The site's CSP forbids
inline styles and the page must not leak third-party phone numbers, so we rebuild the markup
from an allowlist instead of trying to clean the original in place.
"""
import html
import re
from html.parser import HTMLParser

INLINE_FORMAT_TAGS = {"strong", "b", "em", "i"}
LIST_TAGS = {"ul", "ol"}
# Content inside these is executable or embedded media and must disappear with its text.
DROPPED_WITH_CONTENT_TAGS = {"script", "style", "iframe", "noscript", "object", "embed", "svg", "template"}
# Unknown block-level wrappers end the current paragraph so their text does not run together.
PARAGRAPH_BOUNDARY_TAGS = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "blockquote",
    "table", "tr", "header", "footer", "pre", "hr",
}
# Turkish numbers: optional +90 / 0090 / 0 trunk prefix, 3-digit area code (2xx-5xx, optionally
# in parentheses), then 3-2-2 digits with any mix of space, dot or dash separators.
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:(?:\+|00)\s*90|0)?[\s.\-]*\(?\s*[2-5]\d{2}\s*\)?[\s.\-]*\d{3}[\s.\-]*\d{2}[\s.\-]*\d{2}(?!\d)"
)
WHITESPACE_RUN = re.compile(r"[\s ]+")
EMPTY_FORMAT_PAIR = re.compile(r"<(strong|b|em|i)>\s*</\1>")


class _Flow:
    """A container whose text is split into lines: the document root or one list item."""

    def __init__(self, kind):
        self.kind = kind
        self.children = []  # root: ("p", lines) / ("list", _List); item: ("lines", lines) / ("list", _List)
        self.lines = []
        self.current_line = []


class _List:
    def __init__(self, kind, implicit):
        self.kind = kind
        self.items = []
        # A bare <li> outside <ul>/<ol> gets an implied list, which ends at the next block boundary.
        self.implicit = implicit


class _DescriptionCollector(HTMLParser):
    """Builds root -> paragraphs/lists -> items -> lines -> tokens, keeping source order and nesting."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Flow("root")
        self.stack = [self.root]
        self.format_stack = []
        self.skip_depth = 0

    # --- line / frame bookkeeping -------------------------------------------------------
    def _top_flow(self):
        """Return the innermost flow, opening an implied item when text lands directly in a list."""
        top = self.stack[-1]
        if isinstance(top, _List):
            item = _Flow("li")
            top.items.append(item)
            self.stack.append(item)
            return item
        return top

    def _open_line_if_needed(self, flow):
        if not flow.current_line:
            flow.current_line = [("open", tag) for tag in self.format_stack]

    def _end_line(self, flow):
        if not flow.current_line:
            return
        flow.lines.append(flow.current_line + [("close", tag) for tag in reversed(self.format_stack)])
        flow.current_line = []

    def _flush_lines(self, flow):
        self._end_line(flow)
        if flow.lines:
            flow.children.append(("p" if flow.kind == "root" else "lines", flow.lines))
            flow.lines = []

    def _pop(self):
        frame = self.stack.pop()
        if isinstance(frame, _Flow):
            self._flush_lines(frame)

    def _pop_until(self, frame_type, kind=None):
        if not any(isinstance(frame, frame_type) and (kind is None or frame.kind == kind) for frame in self.stack[1:]):
            return
        while True:
            frame = self.stack[-1]
            self._pop()
            if isinstance(frame, frame_type) and (kind is None or frame.kind == kind):
                return

    def _block_boundary(self):
        top = self.stack[-1]
        if isinstance(top, _List):
            if not top.implicit:
                return
            self.stack.pop()
        flow = self.stack[-1]
        if flow.kind == "root":
            self._flush_lines(flow)
        else:
            self._end_line(flow)

    # --- parser callbacks ---------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag in DROPPED_WITH_CONTENT_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "br":
            if isinstance(self.stack[-1], _Flow):
                self._end_line(self.stack[-1])
        elif tag in LIST_TAGS:
            flow = self._top_flow()
            self._flush_lines(flow)
            new_list = _List(tag, implicit=False)
            flow.children.append(("list", new_list))
            self.stack.append(new_list)
        elif tag == "li":
            top = self.stack[-1]
            if isinstance(top, _Flow) and top.kind == "li":
                self._pop()  # an unclosed sibling <li> ends where the next one starts
                top = self.stack[-1]
            if isinstance(top, _Flow):
                self._flush_lines(top)
                implied_list = _List("ul", implicit=True)
                top.children.append(("list", implied_list))
                self.stack.append(implied_list)
            self._top_flow()
        elif tag in INLINE_FORMAT_TAGS:
            flow = self._top_flow()
            self._open_line_if_needed(flow)
            flow.current_line.append(("open", tag))
            self.format_stack.append(tag)
        elif tag in PARAGRAPH_BOUNDARY_TAGS:
            self._block_boundary()

    def handle_startendtag(self, tag, attrs):
        if tag in DROPPED_WITH_CONTENT_TAGS:
            return
        self.handle_starttag(tag, attrs)
        if tag in INLINE_FORMAT_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in DROPPED_WITH_CONTENT_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag == "li":
            self._pop_until(_Flow, "li")
        elif tag in LIST_TAGS:
            self._pop_until(_List)
        elif tag in INLINE_FORMAT_TAGS and tag in self.format_stack:
            top = self.stack[-1]
            current_line = top.current_line if isinstance(top, _Flow) else []
            while self.format_stack:
                open_tag = self.format_stack.pop()
                # An empty line has not re-opened the tag yet, so there is nothing to close.
                if current_line:
                    current_line.append(("close", open_tag))
                if open_tag == tag:
                    break
        elif tag in PARAGRAPH_BOUNDARY_TAGS:
            self._block_boundary()

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = WHITESPACE_RUN.sub(" ", data)
        if not text or (isinstance(self.stack[-1], _List) and not text.strip()):
            return
        flow = self._top_flow()
        self._open_line_if_needed(flow)
        flow.current_line.append(("text", text))

    def finish(self):
        self.close()
        while len(self.stack) > 1:
            self._pop()
        self._flush_lines(self.root)
        return self.root


def _line_text(line):
    return "".join(value for kind, value in line if kind == "text").strip()


def _render_line(line):
    parts = []
    for kind, value in line:
        if kind == "text":
            parts.append(html.escape(value, quote=True))
        elif kind == "open":
            parts.append(f"<{value}>")
        else:
            parts.append(f"</{value}>")
    rendered = "".join(parts)
    previous = None
    while previous != rendered:
        previous, rendered = rendered, EMPTY_FORMAT_PAIR.sub("", rendered)
    return rendered.strip()


def _keep_lines(lines, counter):
    kept = []
    for line in lines:
        text = _line_text(line)
        if not text:
            continue
        if PHONE_PATTERN.search(text):
            counter[0] += 1
            continue
        rendered = _render_line(line)
        if rendered:
            kept.append(rendered)
    return kept


def _render_list(list_block, counter):
    items = []
    for item in list_block.items:
        parts = []
        for kind, content in item.children:
            if kind == "lines":
                lines = _keep_lines(content, counter)
                if lines:
                    parts.append("<br>".join(lines))
            else:
                nested = _render_list(content, counter)
                if nested:
                    parts.append(nested)
        if parts:
            items.append("<li>" + "".join(parts) + "</li>")
    return f"<{list_block.kind}>" + "".join(items) + f"</{list_block.kind}>" if items else ""


def sanitize_description(raw_html: str):
    """Return (clean_html, phone_lines_removed) for a RE/MAX description fragment."""
    collector = _DescriptionCollector()
    collector.feed(raw_html or "")
    root = collector.finish()
    phone_lines_removed = [0]
    rendered_blocks = []
    for kind, content in root.children:
        if kind == "p":
            lines = _keep_lines(content, phone_lines_removed)
            if lines:
                rendered_blocks.append("<p>" + "<br>".join(lines) + "</p>")
        else:
            rendered_list = _render_list(content, phone_lines_removed)
            if rendered_list:
                rendered_blocks.append(rendered_list)
    return "\n".join(rendered_blocks), phone_lines_removed[0]
