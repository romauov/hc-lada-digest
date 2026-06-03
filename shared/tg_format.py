import re
import logging

logger = logging.getLogger(__name__)

ALLOWED_TAGS = frozenset({'b', 'i', 'u', 's', 'code', 'pre', 'a'})


def _tag_name(tag: str) -> str:
    tl = tag.lower().strip('<>/ ')
    return tl.split()[0] if tl else ''


def format_graph_summary(graph) -> str:
    by_type = {}
    for e in graph.entities.values():
        by_type.setdefault(e.type, []).append(e.name)
    lines = [
        f"📊 <b>Граф знаний</b>",
        f"Версия: {graph.version} | {graph.last_updated}",
        "",
    ]
    for t, names in sorted(by_type.items(), key=lambda x: -len(x[1])):
        icon = {"hockey_club": "🏒", "coach": "👤", "player": "👤", "sponsor": "🏢",
                "arena": "🏟", "farm_club": "🏒", "official": "👔", "league": "🏆",
                "other": "📌"}.get(t, "📌")
        lines.append(f"{icon} {t}: {len(names)}")
    lines.append("")
    lines.append(f"Связей: {len(graph.relations)}")
    top = sorted(graph.entities.values(), key=lambda e: e.priority_score, reverse=True)[:5]
    lines.append("")
    lines.append(f"<b>Топ по приоритету:</b>")
    for e in top:
        mentioned = e.last_mentioned or "никогда"
        lines.append(f"• {e.name} ({e.priority_score}) — {mentioned}")
    return "\n".join(lines)


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
    text = re.sub(r'(?is)<![^>]*>', '', text)

    def _replace_tag(m):
        tag = m.group(0)
        name = _tag_name(tag)
        if name in ALLOWED_TAGS:
            return tag
        return ''

    text = re.sub(r'</?[a-z]\w*(?:\s+[^>]*)?\s*/?>', _replace_tag, text, flags=re.IGNORECASE)

    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_message(text: str) -> str:
    if not text.startswith("{"):
        return text
    try:
        import json
        data = json.loads(text)
        for key in ("message", "content", "text"):
            if key in data:
                return str(data[key])
    except json.JSONDecodeError:
        pass
    return text


def md_to_tg(text: str) -> str:
    text = re.sub(r'```[\w]*\n?', '', text)
    text = re.sub(r'\n?```', '', text)
    text = re.sub(r'<[^>]*>', '', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__([^_]+)__', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!_)_([^_]+)_(?!_)', r'<i>\1</i>', text)
    return text.strip()
