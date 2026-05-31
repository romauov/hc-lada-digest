from shared.fact_checker.models import Contradiction


def format_fact_check_block(contradictions: list[Contradiction]) -> str:
    if not contradictions:
        return ""

    high = [c for c in contradictions if c.severity == "high"]
    medium = [c for c in contradictions if c.severity == "medium"]
    other = [c for c in contradictions if c.severity == "low"]

    lines = ["⚠️ <b>Требует проверки</b>"]
    for c in high + medium + other:
        icon = "🔴" if c.severity == "high" else ("🟡" if c.severity == "medium" else "🔵")
        confirmed = " ✓ подтверждено" if c.is_confirmed else ""
        lines.append(
            f'{icon} <b>{c.entity_name}</b>: {c.new_claim}'
            f'{confirmed} — <a href="{c.source_url}">{c.source_name}</a>\n'
            f'   <i>Известно: {c.known_fact}</i>'
        )
    return "\n".join(lines)
