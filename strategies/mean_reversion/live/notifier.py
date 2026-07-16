from __future__ import annotations

import os
from pathlib import Path

import requests

from strategies.mean_reversion.live.signal_runner import DailyResult


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def money(value: float) -> str:
    return f"₹{value:,.0f}"


def format_daily_report(result: DailyResult) -> str:
    lines: list[str] = [f"📊 MR Daily Report — {result.as_of_date.isoformat()}", ""]
    if result.note:
        lines.extend([f"⚠️ {result.note}", ""])

    if result.exits:
        lines.append("🔴 EXIT TOMORROW AT OPEN")
        for row in result.exits:
            pnl = money(float(row["pnl_est"]))
            sign = "+" if float(row["pnl_est"]) >= 0 else ""
            lines.append(
                f"• {row['symbol']} — {row['reason']} | Est. exit: {money(float(row['exit_price_est']))} | "
                f"P&L est: {sign}{pnl}"
            )
    else:
        lines.append("🔴 EXIT TOMORROW AT OPEN")
        lines.append("• None")
    lines.append("")

    if result.entries:
        lines.append("🟢 ENTER TOMORROW AT OPEN")
        for row in result.entries:
            lines.append(
                f"• {row['symbol']} — RSI2={float(row['rsi2']):.1f} | "
                f"Entry est: {money(float(row['entry_price_est']))} | Stop: {money(float(row['stop']))} | "
                f"Size: {float(row['shares']):.0f} shares ({money(float(row['cost_basis']))}) | "
                f"Risk: {money(float(row['risk_amount']))}"
            )
    else:
        lines.append("🟢 ENTER TOMORROW AT OPEN")
        lines.append("• None")
    lines.append("")

    if not result.entries and not result.exits:
        lines.append("⚪ No action needed for other positions.")
        lines.append("")

    fill_text = ", ".join(
        f"{fill['symbol']} @ {money(float(fill['entry_price']))} ✓" for fill in result.paper_fills
    )
    if not fill_text:
        fill_text = "none"
    lines.extend(
        [
            "📋 PAPER PORTFOLIO",
            (
                f"Equity: {money(result.paper_equity)} | Positions: "
                f"{result.paper_positions}/5 | Cash: {money(result.paper_cash)}"
            ),
            f"Paper fills today: {fill_text}",
        ]
    )
    if result.paper_exits:
        exits = ", ".join(f"{row['symbol']} ({row['reason']})" for row in result.paper_exits)
        lines.append(f"Paper exits today: {exits}")
    if result.paper_entries:
        lines.append(f"Paper pending entries: {len(result.paper_entries)}")

    lines.extend(["", "⚠️ Place orders manually on Dhan at market open tomorrow."])
    return "\n".join(lines)


def send_daily_report(result: DailyResult, bot_token: str | None = None, chat_id: str | None = None) -> str:
    load_dotenv()
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    chat = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")

    message = format_daily_report(result)
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat, "text": message},
        timeout=20,
    )
    response.raise_for_status()
    return message

