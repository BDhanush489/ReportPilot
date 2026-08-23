"""
Everything that runs a report on a schedule without a human clicking a
button, and reacts once it has: scheduler.py (when to run), alerts.py
(what's worth flagging in what it produced), delivery.py (how the result
gets to someone). Grouped here as one package because they're one pipeline
-- scheduler.run_schedule calls into alerts.check_alerts and delivery
directly, in that order.
"""
