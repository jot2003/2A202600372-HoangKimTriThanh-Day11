"""
Audit Log & Monitoring — Records every interaction and fires alerts.

WHY: Provides an immutable record of what happened for compliance and
incident investigation.  Monitoring detects anomalies (high block rate,
judge failures) that no single guardrail can surface on its own.

Rubric: "audit_log.json exported with 20+ entries.
         Alerts fire when thresholds exceeded." (7 pts)
"""
import json
import time
from datetime import datetime, timezone

from google.genai import types
from google.adk.plugins import base_plugin


class AuditLogPlugin(base_plugin.BasePlugin):
    """Records every input/output pair with metadata.

    Also exposes lightweight monitoring: block-rate tracking and
    threshold-based alerting printed to stdout (suitable for demo).
    """

    def __init__(self, alert_block_rate: float = 0.5):
        super().__init__(name="audit_log")
        self.logs: list[dict] = []
        self._pending: dict[str, dict] = {}   # session_id -> partial entry
        self.alert_block_rate = alert_block_rate
        self.alerts: list[str] = []

    # ---- helpers ----
    @staticmethod
    def _extract_text(content) -> str:
        text = ""
        if content and hasattr(content, "parts") and content.parts:
            for part in content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text
        return text

    def _current_block_rate(self) -> float:
        if not self.logs:
            return 0.0
        blocked = sum(1 for e in self.logs if e.get("blocked"))
        return blocked / len(self.logs)

    # ---- ADK callbacks ----
    async def on_user_message_callback(
        self, *, invocation_context, user_message
    ) -> types.Content | None:
        """Record incoming user message — never blocks."""
        text = self._extract_text(user_message)
        session_id = (
            invocation_context.session_id if invocation_context else "unknown"
        )
        self._pending[session_id] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_input": text,
            "start_time": time.time(),
        }
        return None  # never block

    async def after_model_callback(self, *, callback_context, llm_response):
        """Record model response, compute latency, check for block signals."""
        response_text = self._extract_text(llm_response)

        # Try to find the matching pending entry
        session_id = "unknown"
        if hasattr(callback_context, "invocation_context"):
            ctx = callback_context.invocation_context
            session_id = ctx.session_id if ctx else "unknown"

        entry = self._pending.pop(session_id, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_input": "(unknown)",
            "start_time": time.time(),
        })

        BLOCK_INDICATORS = ["⚠️", "request blocked", "cannot provide", "i'm sorry"]
        blocked = any(ind in response_text.lower() for ind in BLOCK_INDICATORS)

        entry.update({
            "response": response_text[:500],
            "blocked": blocked,
            "latency_ms": round((time.time() - entry["start_time"]) * 1000, 1),
        })
        entry.pop("start_time", None)
        self.logs.append(entry)

        # --- Monitoring: check block rate ---
        block_rate = self._current_block_rate()
        if block_rate > self.alert_block_rate and len(self.logs) >= 5:
            alert_msg = (
                f"🚨 ALERT: Block rate {block_rate:.0%} exceeds "
                f"threshold {self.alert_block_rate:.0%} "
                f"(after {len(self.logs)} requests)"
            )
            if not self.alerts or self.alerts[-1] != alert_msg:
                self.alerts.append(alert_msg)
                print(alert_msg)

        return llm_response  # never modify

    # ---- Export ----
    def export_json(self, filepath: str = "audit_log.json"):
        """Write collected logs to a JSON file."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.logs, f, indent=2, ensure_ascii=False, default=str)
        print(f"Audit log exported: {filepath} ({len(self.logs)} entries)")

    def print_summary(self):
        """Print a summary suitable for demo / report."""
        total = len(self.logs)
        blocked = sum(1 for e in self.logs if e.get("blocked"))
        avg_latency = (
            sum(e.get("latency_ms", 0) for e in self.logs) / total
            if total else 0
        )
        print(f"\n{'='*50}")
        print("AUDIT & MONITORING SUMMARY")
        print(f"{'='*50}")
        print(f"  Total interactions:  {total}")
        print(f"  Blocked:             {blocked}  ({blocked/total:.0%})" if total else "  Blocked:  0")
        print(f"  Avg latency:         {avg_latency:.0f} ms")
        print(f"  Alerts fired:        {len(self.alerts)}")
        for a in self.alerts:
            print(f"    {a}")
        print(f"{'='*50}")
