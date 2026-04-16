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

    IMPORTANT: This plugin must be FIRST in the plugins list so that
    on_user_message_callback always runs (even when later plugins block).
    It returns None (never blocks), so it doesn't interfere with other
    plugins.  after_model_callback updates the entry if the LLM responds.
    """

    def __init__(self, alert_block_rate: float = 0.5):
        super().__init__(name="audit_log")
        self.logs: list[dict] = []
        self.alert_block_rate = alert_block_rate
        self.alerts: list[str] = []
        # Simple ref to last entry created by on_user_message_callback
        self._last_entry: dict | None = None

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
        """Record incoming user message and immediately append to log.

        The entry defaults to blocked=True / response="(blocked before LLM)".
        If the LLM actually responds, after_model_callback updates the entry
        in-place with the real response and latency.
        """
        text = self._extract_text(user_message)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_input": text,
            "response": "(blocked before LLM)",
            "blocked": True,
            "latency_ms": 0,
            "_start": time.time(),
        }
        self.logs.append(entry)
        self._last_entry = entry          # simple ref for after_model_callback
        return None                       # never block — always pass through

    async def after_model_callback(self, *, callback_context, llm_response):
        """Update the most recent log entry with actual LLM response."""
        response_text = self._extract_text(
            llm_response.content if hasattr(llm_response, "content") else llm_response
        )

        BLOCK_INDICATORS = ["⚠️", "request blocked", "cannot provide", "i'm sorry"]
        blocked = any(ind in response_text.lower() for ind in BLOCK_INDICATORS)

        # Update the entry that was created in on_user_message_callback
        entry = self._last_entry
        if entry is not None and "_start" in entry:
            entry["response"] = response_text[:500]
            entry["blocked"] = blocked
            entry["latency_ms"] = round((time.time() - entry.pop("_start")) * 1000, 1)

        # --- Monitoring: check block rate and fire alert if needed ---
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

        return llm_response               # never modify

    # ---- Export & reporting ----
    def export_json(self, filepath: str = "audit_log.json"):
        """Write collected logs to a JSON file."""
        clean = [{k: v for k, v in e.items() if not k.startswith("_")}
                 for e in self.logs]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False, default=str)
        print(f"Audit log exported: {filepath} ({len(clean)} entries)")

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
        if total:
            print(f"  Blocked:             {blocked}  ({blocked/total:.0%})")
        else:
            print(f"  Blocked:             0")
        print(f"  Avg latency:         {avg_latency:.0f} ms")
        print(f"  Alerts fired:        {len(self.alerts)}")
        for a in self.alerts:
            print(f"    {a}")
        print(f"{'='*50}")
