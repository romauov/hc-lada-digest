import re
import logging

logger = logging.getLogger(__name__)

ALLOWED_TAGS = frozenset({'b', 'i', 'u', 's', 'code', 'pre', 'a'})


def _tag_name(tag: str) -> str:
    tl = tag.lower().strip('<>/ ')
    return tl.split()[0] if tl else ''


def sanitize_tg_html(text: str) -> str:
    if not text:
        return text

    text = re.sub(r'(?is)<li[^>]*>', '\n• ', text)
    text = re.sub(r'(?is)</li>', '', text)
    text = re.sub(r'(?is)<br\s*/?>', '\n', text)
    text = re.sub(r'(?is)</p\b[^>]*>', '\n', text)
    text = re.sub(r'(?is)<p\b[^>]*>', '', text)
    text = re.sub(r'(?is)</?ul[^>]*>', '', text)
    text = re.sub(r'(?is)</?ol[^>]*>', '', text)
    text = re.sub(r'(?is)</?div[^>]*>', '', text)

    def _replace_tag(m):
        tag = m.group(0)
        name = _tag_name(tag)
        if name in ALLOWED_TAGS:
            return tag
        return ''

    text = re.sub(r'</?[a-z]\w*(?:\s+[^>]*)?\s*/?>', _replace_tag, text, flags=re.IGNORECASE)

    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
