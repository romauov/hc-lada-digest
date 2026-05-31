from shared.fact_checker.models import Contradiction, VerificationResult, CONFIRMATION_SOURCES_NEEDED
from shared.fact_checker.prompts import VERIFY_SYSTEM, CONFIRM_SYSTEM
from shared.fact_checker.verifier import verify_news_item, try_confirm_contradiction, verify_all_news, _build_facts_block, FACT_THRESHOLD, VERIFY_MIN_IMPORTANCE
from shared.fact_checker.applier import apply_contradictions_to_graph
from shared.fact_checker.formatter import format_fact_check_block

__all__ = [
    "Contradiction", "VerificationResult",
    "VERIFY_SYSTEM", "CONFIRM_SYSTEM",
    "verify_news_item", "try_confirm_contradiction", "verify_all_news", "_build_facts_block",
    "apply_contradictions_to_graph",
    "format_fact_check_block",
    "FACT_THRESHOLD", "VERIFY_MIN_IMPORTANCE", "CONFIRMATION_SOURCES_NEEDED",
]
