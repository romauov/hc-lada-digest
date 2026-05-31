from dataclasses import dataclass, field

CONFIRMATION_SOURCES_NEEDED = 2


@dataclass
class Contradiction:
    entity_name:   str
    relation_type: str
    known_fact:    str
    new_claim:     str
    severity:      str
    source_url:    str = ""
    source_name:   str = ""
    confirmed_by:  list[str] = field(default_factory=list)

    @property
    def is_confirmed(self) -> bool:
        return len(self.confirmed_by) >= CONFIRMATION_SOURCES_NEEDED

    def to_dict(self) -> dict:
        return {
            "entity_name":   self.entity_name,
            "relation_type": self.relation_type,
            "known_fact":    self.known_fact,
            "new_claim":     self.new_claim,
            "severity":      self.severity,
            "source_url":    self.source_url,
            "source_name":   self.source_name,
            "confirmed_by":  self.confirmed_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Contradiction":
        return cls(**d)


@dataclass
class VerificationResult:
    news_item:      object
    contradictions: list[Contradiction] = field(default_factory=list)
    is_clean:       bool = True
    skip_reason:    str = ""

    @property
    def has_high_severity(self) -> bool:
        return any(c.severity == "high" for c in self.contradictions)

    def to_digest_block(self) -> str:
        if self.is_clean or not self.contradictions:
            return ""
        lines = []
        for c in self.contradictions:
            icon = "🔴" if c.severity == "high" else ("🟡" if c.severity == "medium" else "🔵")
            lines.append(
                f'{icon} <b>{c.entity_name}</b>: {c.new_claim} '
                f'<i>(известно: {c.known_fact})</i> — '
                f'<a href="{self.news_item.url}">источник</a>'
            )
        return "\n".join(lines)
