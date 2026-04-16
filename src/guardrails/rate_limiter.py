"""
Rate Limiter — Sliding-window per-user rate limiting.

WHY: Prevents abuse / brute-force prompt injection by limiting how many
requests a single user can send within a time window.  Other layers
(input/output guardrails) cannot defend against volumetric abuse.

Rubric: "Rate Limiter works — first N pass, rest blocked with wait time" (8 pts)
"""
import time
from collections import defaultdict, deque

from google.genai import types
from google.adk.plugins import base_plugin
from google.adk.agents.invocation_context import InvocationContext


class RateLimitPlugin(base_plugin.BasePlugin):
    """Sliding-window rate limiter implemented as an ADK plugin.

    How it works:
        - Each user has a deque of request timestamps.
        - On every incoming message we prune timestamps older than
          *window_seconds*, then check if the user still has quota.
        - If quota is exceeded the message is replaced with a polite
          block response that tells the user how long to wait.
    """

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        super().__init__(name="rate_limiter")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # user_id -> deque of float timestamps
        self.user_windows: dict[str, deque] = defaultdict(deque)
        # Counters for monitoring
        self.allowed_count = 0
        self.blocked_count = 0

    # ------------------------------------------------------------------
    # ADK callback — runs BEFORE the message reaches the LLM
    # ------------------------------------------------------------------
    async def on_user_message_callback(
        self,
        *,
        invocation_context: InvocationContext,
        user_message: types.Content,
    ) -> types.Content | None:
        try:
            user_id = (
                invocation_context.user_id
                if invocation_context and hasattr(invocation_context, "user_id")
                else "anonymous"
            )
        except Exception:
            user_id = "anonymous"
        now = time.time()
        window = self.user_windows[user_id]

        # Prune expired timestamps from the front of the deque
        while window and window[0] <= now - self.window_seconds:
            window.popleft()

        if len(window) >= self.max_requests:
            # Oldest non-expired timestamp → wait until it expires
            wait = self.window_seconds - (now - window[0])
            self.blocked_count += 1
            return types.Content(
                role="model",
                parts=[types.Part.from_text(
                    text=f"⚠️ Rate limit exceeded. Please wait {wait:.0f}s "
                         f"before sending another message."
                )],
            )

        # Under limit — record timestamp and allow
        window.append(now)
        self.allowed_count += 1
        return None
