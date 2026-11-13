"""moodle_parse — HTML extractors for the Moodle connector (standard library).

Shapes verified against learning.monash.edu (Moodle 4.5, 2026-11-12):
- resource wrapper pages contain exactly one pluginfile anchor;
- folder pages list pluginfile anchors;
- forum view pages link discussions via discuss.php?d=<id>;
- discussion pages render posts as <article data-post-id>, with replies nested
  inside div.indent[data-region=replies-container] within the parent article.
"""
import html as html_mod
import re
from html.parser import HTMLParser

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<script\b.*?</script\s*>|<!--.*?-->", re.S | re.I)
_STYLE_RE = re.compile(r"<style\b.*?</style\s*>", re.S | re.I)
_ON_ATTR_RE = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_TOKEN_PARAM_RE = re.compile(r"([?&])(token|sesskey)=[^&\"'<>\s]*&?", re.I)


def strip_tags(fragment: str) -> str:
    text = _TAG_RE.sub(" ", fragment or "")
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def sanitize_html(fragment: str) -> str:
    """Remove active content and credential-bearing URLs from archived HTML."""
    out = _SCRIPT_RE.sub("", fragment or "")
    out = _STYLE_RE.sub("", out)
    out = _ON_ATTR_RE.sub("", out)
    out = re.sub(r"javascript:", "", out, flags=re.I)
    out = _TOKEN_PARAM_RE.sub(r"\1", out)
    out = re.sub(r"\?&", "?", out)
    return out.strip()


def extract_pluginfile_links(fragment: str, base: str = "") -> list[dict]:
    """(url, name) pairs for pluginfile anchors and embedded media, deduped."""
    from urllib.parse import urljoin, urlsplit

    links, seen = [], set()

    def add(url: str, name: str | None):
        url = urljoin(base, html_mod.unescape(url))
        if url in seen:
            return
        seen.add(url)
        fallback = urlsplit(url).path.rsplit("/", 1)[-1] or "file"
        links.append({"url": url, "name": (name or "").strip() or fallback})

    for m in re.finditer(r'<a\b[^>]*href="([^"]*pluginfile\.php[^"]*)"[^>]*>(.*?)</a>',
                         fragment or "", re.S | re.I):
        add(m.group(1), strip_tags(m.group(2)))

    for m in re.finditer(r'<(img|iframe|embed|source|object|video|audio)\b[^>]*?'
                         r'\b(?:src|data)="([^"]*pluginfile\.php[^"]*)"[^>]*>',
                         fragment or "", re.S | re.I):
        tag_html = m.group(0)
        label = re.search(r'(?:alt|title)="([^"]*)"', tag_html, re.I)
        add(m.group(2), html_mod.unescape(label.group(1)) if label else None)
    return links


def extract_first_pluginfile(fragment: str, base: str = "") -> dict | None:
    links = extract_pluginfile_links(fragment, base)
    return links[0] if links else None


# ------------------------------------------------------------------ forum view
def parse_forum_discussions(page_html: str, base: str) -> list[dict]:
    """Discussion list from /mod/forum/view.php (ordered, deduped)."""
    from urllib.parse import urljoin

    out, seen = [], set()
    for m in re.finditer(r'<a\b[^>]*href="([^"]*discuss\.php\?d=(\d+)[^"]*)"[^>]*>(.*?)</a>', page_html or "", re.S | re.I):
        did = int(m.group(2))
        if did in seen:
            continue
        seen.add(did)
        subject = strip_tags(m.group(3))
        row = _enclosing_row(page_html, m.start())
        replies = _replies_from_row(row)
        out.append({
            "discussion_id": did,
            "subject": subject,
            "url": urljoin(base, html_mod.unescape(m.group(1))),
            "replies": replies,
        })
    return out


def _enclosing_row(page_html: str, pos: int) -> str:
    start = page_html.rfind("<tr", 0, pos)
    end = page_html.find("</tr>", pos)
    if start >= 0 and end > start:
        return page_html[start:end]
    return ""


def _replies_from_row(row: str) -> int | None:
    for cell in re.findall(r"<td[^>]*>(.*?)</td>", row or "", re.S):
        text = strip_tags(cell)
        if re.fullmatch(r"\d{1,5}", text):
            return int(text)
    return None


# ------------------------------------------------------------------ discussion
class _PostParser(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.posts: list[dict] = []
        self._stack: list[str] = []          # open tags
        self._article_open = 0               # nesting depth of <article>
        self._articles: list[dict] = []      # open article records
        self._body_depth = 0                 # <div> depth inside a post-content div
        self._current: dict | None = None

    # --- helpers
    def _depth(self, tag: str) -> int:
        return self._stack.count(tag)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self._stack.append(tag)
        if tag == "article":
            self._article_open += 1
            parent = self._articles[-1] if self._articles else None
            post = {
                "post_id": int(a.get("data-post-id") or (a.get("id") or "p0").lstrip("p")),
                "parent_post_id": parent["post_id"] if parent else None,
                "subject": None, "author": None, "created_at": None,
                "body_html": "", "attachments": [],
            }
            self._articles.append(post)
            self.posts.append(post)
        elif tag == "h3" and a.get("data-region-content") == "forum-post-core-subject":
            self._capturing = "subject"
        elif tag == "a" and "user/view.php" in (a.get("href") or "") and self._articles:
            self._capturing = "author"
        elif tag == "time" and a.get("datetime") and self._articles:
            self._articles[-1]["created_at"] = a["datetime"]
        elif tag == "div" and a.get("id", "").startswith("post-content-") and self._articles:
            self._body_depth = 1
            self._body_start = self.getpos()
        elif tag == "div" and self._body_depth:
            self._body_depth += 1

    def handle_endtag(self, tag):
        if tag == "article" and self._articles:
            post = self._articles.pop()
            if self._body_depth and post.get("body_html") == "":
                post["body_html"] = self._slice_body()
            self._article_open = max(0, self._article_open - 1)
        elif tag == "div" and self._body_depth:
            self._body_depth -= 1
            if self._body_depth == 0 and self._articles:
                self._articles[-1]["body_html"] = self._slice_body()
        elif tag in ("h3", "a") and getattr(self, "_capturing", None):
            self._capturing = None
        if self._stack and tag in self._stack:
            # pop to the last occurrence
            while self._stack:
                t = self._stack.pop()
                if t == tag:
                    break

    def handle_data(self, data):
        mode = getattr(self, "_capturing", None)
        if not mode or not self._articles:
            return
        if mode == "subject" and self._articles[-1]["subject"] is None:
            self._articles[-1]["subject"] = data.strip()
        elif mode == "author" and self._articles[-1]["author"] is None:
            self._articles[-1]["author"] = data.strip()

    # body slicing is only needed once; nested div handling is approximate but
    # verified against the Monash template (post-content-container div).
    def _slice_body(self) -> str:
        return ""

    def finalize(self, source: str):
        # Re-extract bodies by id range (robust against nested divs)
        for post in self.posts:
            pid = post["post_id"]
            m = re.search(rf'<div[^>]+id="post-content-{pid}"[^>]*>', source)
            if not m:
                continue
            start = m.end()
            end = _matching_div_end(source, start)
            fragment = source[start:end]
            post["body_html"] = sanitize_html(fragment)
            post["attachments"] = extract_pluginfile_links(fragment)
        return self.posts


def _matching_div_end(source: str, start: int) -> int:
    depth = 1
    pos = start
    for m in re.finditer(r"<div\b|</div\s*>", source[start:], re.I):
        if m.group(0).lower().startswith("</"):
            depth -= 1
            if depth == 0:
                return start + m.start()
        else:
            depth += 1
        pos = start + m.end()
    return len(source)


def parse_discussion(page_html: str) -> list[dict]:
    parser = _PostParser(page_html or "")
    parser.feed(page_html or "")
    return parser.finalize(page_html or "")


# ------------------------------------------------------------------ labels / summaries
def parse_labels(page_html: str) -> list[dict]:
    """Label ('Text and media area') blocks from a course page."""
    out = []
    for m in re.finditer(r'<li[^>]+class="[^"]*activity[^"]*label[^"]*"[^>]*>(.*?)</li>', page_html or "", re.S | re.I):
        block = m.group(1)
        name_m = re.search(r'class="[^"]*instancename[^"]*"[^>]*>(.*?)</span>', block, re.S)
        body_m = re.search(r'class="[^"]*(?:no-overflow|content)[^"]*"[^>]*>(.*)', block, re.S)
        out.append({
            "name": strip_tags(name_m.group(1)) if name_m else None,
            "html": sanitize_html(body_m.group(1)) if body_m else "",
        })
    return out


def extract_page_content(page_html: str) -> str | None:
    """Content HTML of a mod_page activity (verified: div#page-content)."""
    source = page_html or ""
    m = re.search(r'<div[^>]+id="page-content"[^>]*>', source, re.I)
    if not m:
        m = re.search(r'<div[^>]+class="[^"]*(?:resourcecontent|no-overflow)[^"]*"[^>]*>', source, re.I)
    if not m:
        return None
    end = _matching_div_end(source, m.end())
    return source[m.end():end]


def _div_by_class_tokens(source: str, tokens: set[str]):
    for m in re.finditer(r'<div\b[^>]*class="([^"]*)"[^>]*>', source or "", re.I):
        if tokens.intersection(m.group(1).split()):
            return m
    return None


def parse_section_page(page_html: str) -> dict:
    """Section summary + label blocks from course/view.php?id=..&section=N.

    Verified Monash markup:
      li.activity.label[id=module-<cmid>][data-activityname=...]
        div.activity-altcontent > div.no-overflow  (label body)
      Standard Moodle summary: div.summary or [data-for=section-summary]
      Monash assessment widget: div.assessmentsummary — **course-level
        "Assessment structure" table, NOT a section summary** (same content on
        every section; recorded separately as evidence, never as summary).
    """
    source = page_html or ""

    summary_html, summary_kind = None, None
    m = re.search(r'<div[^>]+data-for="section-summary"[^>]*>', source, re.I)
    if m is None:
        m = _div_by_class_tokens(source, {"summary"})
    if m is not None:
        end = _matching_div_end(source, m.end())
        fragment = source[m.end():end]
        if strip_tags(fragment):
            summary_html, summary_kind = fragment, "standard"

    widget_html = None
    wm = _div_by_class_tokens(source, {"assessmentsummary"})
    if wm is not None:
        end = _matching_div_end(source, wm.end())
        widget_html = source[wm.end():end]

    labels = []
    for m in re.finditer(r'<li\b[^>]*class="[^"]*(?:\blabel\b|modtype_label)[^"]*"[^>]*>', source, re.I):
        tag = m.group(0)
        cm_m = re.search(r'data-id="(\d+)"', tag) or re.search(r'id="module-(\d+)"', tag)
        cmid = int(cm_m.group(1)) if cm_m else None
        body = None
        bm = re.search(r'<div[^>]+class="[^"]*activity-altcontent[^"]*"[^>]*>', source[m.start():], re.I)
        if bm:
            body_start = m.start() + bm.end()
            body_end = _matching_div_end(source, body_start)
            body = source[body_start:body_end]
        if body is None:
            nm = re.search(r'<div[^>]+class="[^"]*no-overflow[^"]*"[^>]*>', source[m.start():], re.I)
            if nm:
                body_start = m.start() + nm.end()
                body = source[body_start:_matching_div_end(source, body_start)]
        name_m = re.search(r'data-activityname="([^"]*)"', tag)
        labels.append({
            "cmid": cmid,
            "name": html_mod.unescape(name_m.group(1)) if name_m else None,
            "html": sanitize_html(body) if body else "",
        })
    return {"summary_html": sanitize_html(summary_html) if summary_html else None,
            "summary_kind": summary_kind,
            "assessment_widget_html": sanitize_html(widget_html) if widget_html else None,
            "labels": [l for l in labels if l["cmid"]]}


def has_edit_marker(fragment: str) -> bool:
    text = strip_tags(fragment or "").lower()
    return "edited by" in text or "last modified" in text
