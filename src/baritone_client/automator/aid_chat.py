"""Announce genuine need in server chat, sparsely and without repeating.

Chat is shared with a human. That makes it the one channel where volume is a
real cost: a bot that repeats itself is worse than a bot that says nothing,
because it trains the reader to ignore the channel entirely. Six bots
refreshing a standing problem every thirty seconds would emit ~700 identical
lines an hour and bury the one line that mattered.

Three rules follow, in priority order:

1. **Only new need speaks.** A request that is merely refreshed is silent. A
   bot starving for an hour announces once, not one hundred and twenty times.
2. **Never the same line twice in a row.** Phrasing is chosen per request and
   the recent history is remembered, so a fleet in trouble does not chant.
3. **The number always survives the joke.** Every line carries its metric --
   `food 0/14`, `2.0 HP`, `8 hostiles`. If the humour ever costs the reader
   the fact, the line is wrong.

Resolution and expiry are deliberately NOT announced. They are dashboard
facts; only a fresh problem is news.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional, Sequence

#: Quiet period per bot regardless of kind. A bot flapping between different
#: needs must not be able to talk continuously.
CHAT_COOLDOWN_SECONDS = 240.0
#: How many recent phrasings to remember per bot before allowing reuse.
RECENT_MEMORY = 4

_LEDGER = "aid_chat"

#: Each phrasing must render its metric. Humour is dry on purpose: this is a
#: distress call, and a line that reads as a joke first gets skimmed.
_PHRASINGS: Mapping[str, Sequence[str]] = {
    "food": (
        "running on fumes down here, food {food}/14",
        "food {food}/14 and my stomach has filed a formal complaint",
        "food {food}/14. I have, regrettably, become the hunger",
        "down to food {food}/14 -- accepting bread, cows, or vague promises",
        "food {food}/14, currently rationing a single crumb",
    ),
    "rescue": (
        "{health} HP and reconsidering several recent decisions",
        "{health} HP left. Not to alarm anyone. Deeply alarmed",
        "on {health} HP -- this is fine. This is demonstrably not fine",
        "{health} HP. Sending this while I still can",
    ),
    "clear_hostiles": (
        "{hostiles} hostiles have formed a committee around me",
        "besieged by {hostiles}. They brought friends. The friends brought friends",
        "{hostiles} hostiles nearby, so no work is getting done, only staring",
        "cannot work: {hostiles} hostiles and none of them will leave",
    ),
    "supply": (
        "no progress on {detail} -- send help or send ideas",
        "{detail}, and it has stopped being funny",
        "still blocked: {detail}. Open to suggestions",
    ),
}
_FALLBACK = "needs help: {detail}"


def _ledger(state: Any) -> dict:
    custom = getattr(state, "custom_data", None)
    if not isinstance(custom, dict):
        custom = {}
        state.custom_data = custom
    ledger = custom.setdefault(_LEDGER, {})
    if not isinstance(ledger, dict):
        ledger = {}
        custom[_LEDGER] = ledger
    return ledger


def _metrics(request: Mapping[str, Any], signals: Any) -> dict:
    """Values a phrasing may interpolate. Missing values must never crash."""
    detail = str(request.get("detail", "") or "unspecified trouble")
    streak = 0
    for token in detail.replace("<", " ").split():
        if token.isdigit():
            streak = int(token)
            break
    return {
        "detail": detail,
        "food": int(getattr(signals, "food", 0) or 0),
        "health": f"{float(getattr(signals, 'health', 0.0) or 0.0):.1f}",
        "hostiles": int(getattr(signals, "nearby_hostiles", 0) or 0),
        "streak": streak or int(request.get("urgency", 0) or 0),
    }


def choose_template(request: Mapping[str, Any], recent: Sequence[str]) -> str:
    """Pick a phrasing this bot has not used recently.

    One chooser only. An earlier version selected the template twice by
    different rules, so the anti-repeat memory recorded a line the bot had not
    actually said and the fleet drifted back into repeating itself.
    """
    options = list(_PHRASINGS.get(str(request.get("kind", "")), ()))
    if not options:
        return _FALLBACK
    fresh = [text for text in options if text not in recent] or options
    # Stable per request so refreshing a need never re-rolls the phrasing,
    # while successive needs rotate through the pool.
    digest = sum(ord(char) for char in str(request.get("request_id", "")))
    return fresh[digest % len(fresh)]


def compose(request: Mapping[str, Any], signals: Any, recent: Sequence[str]) -> str:
    """Render the chosen phrasing with its metric."""
    template = choose_template(request, recent)
    try:
        return template.format(**_metrics(request, signals))
    except (KeyError, IndexError, ValueError):
        return _FALLBACK.format(**_metrics(request, signals))


def should_announce(
    state: Any, request: Mapping[str, Any], *, now: Optional[float] = None
) -> bool:
    """Only a genuinely new need, and only after the quiet period."""
    if not request.get("is_new"):
        return False
    moment = time.time() if now is None else float(now)
    recorded = _ledger(state).get("last_at")
    if recorded is None:
        return True  # never spoken: the first call for help must get through
    try:
        last = float(recorded)
    except (TypeError, ValueError):
        return True
    return moment - last >= CHAT_COOLDOWN_SECONDS


def announce(
    client: Any,
    state: Any,
    request: Mapping[str, Any],
    signals: Any,
    *,
    now: Optional[float] = None,
) -> Optional[str]:
    """Say one line in server chat. Returns the text, or None if it stayed quiet."""
    if not should_announce(state, request, now=now):
        return None
    ledger = _ledger(state)
    recent = list(ledger.get("recent", []))
    template = choose_template(request, recent)
    message = compose(request, signals, recent)
    try:
        client.transport.dispatch("chat", {"message": message})
    except Exception:
        # Chat is decoration. Losing it must never disturb scheduling.
        return None
    ledger["last_at"] = time.time() if now is None else float(now)
    recent.append(template)
    ledger["recent"] = recent[-RECENT_MEMORY:]
    return message


__all__ = [
    "CHAT_COOLDOWN_SECONDS",
    "RECENT_MEMORY",
    "announce",
    "choose_template",
    "compose",
    "should_announce",
]
