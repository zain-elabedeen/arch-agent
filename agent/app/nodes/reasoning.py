"""
Reasoning / explanation agent (terminal pipeline stage).

Produces ``explanation_report`` markdown from smells, recommendations, and
critiques. An optional LLM may **rephrase** the deterministic report only; if the
model invents or drops facts, output is rejected in favor of the template report.
"""

from __future__ import annotations

from typing import List

from agent.app.config import Settings
from agent.app.logging_utils import get_logger
from agent.app.state import Critique, GraphState, Recommendation

logger = get_logger("agent.nodes.reasoning")

def _smell_lines(smells: List[dict]) -> List[str]:
    """Format smell dicts as markdown bullet lines (or a single empty-state line)."""
    if not smells:
        return ["- No architecture smells were detected from the provided runtime signals."]
    lines: List[str] = []
    for s in smells:
        lines.append(
            f"- `{s.get('type', 'unknown')}` (severity: {s.get('severity', 'n/a')}, "
            f"confidence: {s.get('confidence', 'n/a')})"
        )
    return lines


def _recommendation_lines(recommendations: List[Recommendation]) -> List[str]:
    """Format recommendations as markdown bullets for the deterministic report."""
    if not recommendations:
        return ["- No architecture changes are currently recommended."]
    lines: List[str] = []
    for r in recommendations:
        reason = f" Reason: {r.reason}." if r.reason else ""
        lines.append(
            f"- `{r.pattern}`: {r.solution} (impact: {r.impact}, effort: {r.effort}).{reason}"
        )
    return lines


def _critique_lines(critiques: List[Critique]) -> List[str]:
    """Format critiques as markdown bullets."""
    if not critiques:
        return ["- No constraint warnings were triggered by current runtime context."]
    lines: List[str] = []
    for c in critiques:
        lines.append(f"- `{c.pattern_id}` [{c.level}]: {c.message}")
    return lines


def build_explanation_report(state: GraphState) -> str:
    """
    Explanation-only layer.
    This function must not detect smells or choose architecture decisions.
    It only summarizes deterministic outputs from prior agents.
    """
    smells = state.get("smells", [])
    recommendations = state.get("recommendations", [])
    critiques = state.get("critiques", [])

    report_parts = [
        "## Runtime Architecture Report",
        "",
        "### Detected Smells",
        *_smell_lines(smells),
        "",
        "### Recommended Architecture Moves",
        *_recommendation_lines(recommendations),
        "",
        "### Constraints and Warnings",
        *_critique_lines(critiques),
        "",
        "### Summary",
        (
            f"Detected {len(smells)} smell(s), produced {len(recommendations)} recommendation(s), "
            f"and raised {len(critiques)} critique warning(s)."
        ),
    ]
    return "\n".join(report_parts).strip()


def _build_llm_prompt(state: GraphState) -> str:
    """User message instructing the model to rewrite without adding or removing facts."""
    base_report = build_explanation_report(state)
    return (
        "You are an assistant that improves clarity of engineering reports.\n"
        "IMPORTANT RULES:\n"
        "- Do NOT add new information.\n"
        "- Do NOT remove information.\n"
        "- Do NOT reinterpret data.\n"
        "- Keep all original sections and factual claims.\n"
        "- Only improve clarity, flow, and readability.\n\n"
        "Rewrite the following report in clearer, more structured markdown:\n\n"
        f"{base_report}"
    )


def _is_llm_output_consistent(state: GraphState, llm_output: str) -> bool:
    """
    Reject LLM text that contradicts empty/non-empty sections (e.g. claims “no smells”
    when smells exist). Keeps explanation faithful to upstream agents.
    """
    smells = state.get("smells", [])
    recommendations = state.get("recommendations", [])
    critiques = state.get("critiques", [])
    report = llm_output.lower()

    if smells and "no smells detected" in report:
        return False
    if critiques and "no constraints or warnings provided" in report:
        return False
    if critiques and "no constraint warnings were triggered" in report:
        return False
    if recommendations and "no architecture changes are currently recommended" in report:
        return False
    return True


def _llm_report(state: GraphState, settings: Settings) -> str | None:
    """
    Call OpenAI-compatible chat completion to polish the report; return None on
    disabled config, missing SDK/key, or recoverable API errors (caller falls back).
    """
    if not settings.llm_reasoning_enabled:
        logger.info(
            "reasoning_agent llm disabled run_id=%s enabled=%s",
            state.get("run_id", "n/a"),
            settings.llm_reasoning_enabled,
        )
        return None
    try:
        from openai import OpenAI  # type: ignore[reportMissingImports]
    except Exception:
        logger.exception("reasoning_agent failed to import OpenAI SDK run_id=%s", state.get("run_id", "n/a"))
        return None

    try:
        logger.info(
            "reasoning_agent llm config run_id=%s provider=%s model=%s base_url=%s",
            state.get("run_id", "n/a"),
            settings.llm_provider,
            settings.llm_model,
            settings.ollama_base_url if settings.llm_provider == "ollama" else "default_openai",
        )

        if settings.llm_provider == "openai":
            if not settings.openai_api_key:
                logger.info(
                    "reasoning_agent llm disabled run_id=%s provider=openai api_key_set=%s",
                    state.get("run_id", "n/a"),
                    bool(settings.openai_api_key),
                )
                return None
            client = OpenAI(api_key=settings.openai_api_key)
        else:
            # Ollama exposes an OpenAI-compatible API at /v1.
            # A placeholder key is accepted for local usage.
            client = OpenAI(base_url=settings.ollama_base_url, api_key="ollama")

        resp = client.chat.completions.create(
            model=settings.llm_model,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": "You summarize existing architecture outputs only.",
                },
                {"role": "user", "content": _build_llm_prompt(state)},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        logger.info(
            "reasoning_agent llm success run_id=%s provider=%s model=%s report_chars=%d",
            state.get("run_id", "n/a"),
            settings.llm_provider,
            settings.llm_model,
            len(content),
        )
        return content or None
    except Exception as exc:
        # Avoid noisy tracebacks for expected API failures (quota, auth, rate limits).
        err_name = exc.__class__.__name__
        logger.warning(
            "reasoning_agent llm call failed run_id=%s error_type=%s message=%s",
            state.get("run_id", "n/a"),
            err_name,
            str(exc),
        )
        return None


def reasoning_node(state: GraphState, settings: Settings | None = None) -> GraphState:
    """Attach ``explanation_report``; prefer LLM polish when safe, else deterministic."""
    run_id = state.get("run_id", "n/a")
    logger.info(
        "reasoning_agent start run_id=%s smells=%d recommendations=%d critiques=%d",
        run_id,
        len(state.get("smells", [])),
        len(state.get("recommendations", [])),
        len(state.get("critiques", [])),
    )
    deterministic_report = build_explanation_report(state)
    if settings is not None:
        llm_output = _llm_report(state, settings)
        if llm_output:
            if _is_llm_output_consistent(state, llm_output):
                state["explanation_report"] = llm_output
                logger.info("reasoning_agent done run_id=%s source=llm", run_id)
                return state
            logger.warning(
                "reasoning_agent llm output inconsistent run_id=%s source=fallback_deterministic",
                run_id,
            )
    state["explanation_report"] = deterministic_report
    logger.info(
        "reasoning_agent done run_id=%s source=deterministic_fallback report_chars=%d",
        run_id,
        len(state.get("explanation_report", "")),
    )
    return state
