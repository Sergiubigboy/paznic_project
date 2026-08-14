"""
tools/context_tools.py — Citire Contextuală a Datelor Personale (chronos_data)
================================================================================
Tool-uri de CITIRE prin care Chronos își consultă propriile date despre Sergiu
înainte să răspundă: finanțe, targeturi, remindere, proiecte, sport, obiceiuri.

Design:
    - Fiecare categorie are un formatter care întoarce TEXT compact, nu JSON brut
      (mai puțini tokeni + LLM-ul raționează mai bine pe text structurat).
    - Logica financiară replică EXACT calculele din web_dashboard.py
      (_calc_balance / _calc_inv_summary) ca cifrele rostite de Chronos să
      coincidă cu cele afișate în dashboard.
    - Log-urile de jurnal sunt EXCLUSE intenționat (prea lungi pentru voce);
      pentru ele există memoria vectorială din logger_agent.

Categorii disponibile: vezi CATEGORIES / available_categories().
"""

import json
import logging
import os
from datetime import datetime, date
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "chronos_data")
FINANCE_DIR = os.path.join(DATA_DIR, "finance")
GYM_DIR = os.path.join(DATA_DIR, "gym")


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _load_json(path: str, default: Any) -> Any:
    """Citire tolerantă la fișiere lipsă/corupte — nicio categorie nu trebuie
    să arunce excepție, altfel pică tot răspunsul vocal."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.warning(f"⚠️ [Context Tools] Nu pot citi {os.path.basename(path)}: {e}")
        return default


def _fin(path: str) -> list:
    data = _load_json(os.path.join(FINANCE_DIR, path), [])
    return data if isinstance(data, list) else []


def _money(val: float) -> str:
    """Formatare sumă fără zecimale inutile (pentru rostire naturală)."""
    val = round(float(val), 2)
    return f"{int(val)}" if val == int(val) else f"{val:.2f}"


def _days_since(date_str: str) -> Optional[int]:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return (date.today() - datetime.strptime(date_str[:26], fmt).date()).days
        except (ValueError, TypeError):
            continue
    return None


# ─────────────────────────────────────────────────────────────
# FINANȚE — replică logica din web_dashboard.py
# ─────────────────────────────────────────────────────────────

def _calc_balance(account_id: str, transactions: list) -> float:
    total = 0.0
    for tx in transactions:
        if tx.get("account_id") == account_id:
            amount = float(tx.get("amount", 0))
            total += amount if tx.get("type") == "in" else -amount
    return round(total, 2)


def _calc_inv_summary(inventory: list) -> dict:
    active = [i for i in inventory if i.get("status") == "active"]
    sold = [i for i in inventory if i.get("status") == "sold"]

    active_cost = sum(float(i.get("cost_basis", 0)) for i in active)
    active_value = sum(float(i.get("estimated_value", 0)) for i in active)
    active_profit = active_value - active_cost

    total_invested = sum(float(i.get("cost_basis", 0)) for i in inventory)
    total_recovered = sum(float(i.get("sold_amount", 0)) for i in sold)
    realized_profit = total_recovered - sum(float(i.get("cost_basis", 0)) for i in sold)

    return {
        "active_cost": active_cost,
        "active_value": active_value,
        "active_profit": active_profit,
        "active_count": len(active),
        "sold_count": len(sold),
        "total_invested": total_invested,
        "total_recovered": total_recovered,
        "realized_profit": realized_profit,
        "current_risk": total_invested - total_recovered,
    }


def _fmt_finante() -> str:
    accounts = _fin("accounts.json")
    transactions = _fin("transactions.json")
    debts = _fin("debts.json")
    inventory = _fin("inventory_active.json") + _fin("inventory_sold.json")

    lines = ["=== FINANȚE ==="]

    # Conturi + total lichid
    total_cash = 0.0
    if accounts:
        lines.append("Conturi (bani lichizi):")
        for acc in accounts:
            bal = _calc_balance(acc.get("id", ""), transactions)
            total_cash += bal
            lines.append(f"  - {acc.get('name', '?')}: {_money(bal)} RON")
    lines.append(f"TOTAL LICHID (doar conturi): {_money(total_cash)} RON")

    # Investiții
    inv = _calc_inv_summary(inventory)
    if inventory:
        lines.append("")
        lines.append("Investiții (flipping carduri Pokemon):")
        lines.append(
            f"  - Stoc activ: {inv['active_count']} articole, "
            f"cost {_money(inv['active_cost'])} RON, "
            f"valoare estimată {_money(inv['active_value'])} RON "
            f"(profit potențial {_money(inv['active_profit'])} RON)"
        )
        lines.append(f"  - Bani blocați acum în stoc (risc): {_money(inv['current_risk'])} RON")
        # Ce ai vândut până acum → categoria separată 'vanzari' (la cerere explicită)

        active_items = [i for i in inventory if i.get("status") == "active"]
        if active_items:
            lines.append("  - Articole în stoc:")
            for it in active_items:
                lines.append(
                    f"      {it.get('name', '?')}: cost {_money(it.get('cost_basis', 0))}, "
                    f"estimat {_money(it.get('estimated_value', 0))} RON"
                )

    # Datorii
    owed_to_me = [d for d in debts if d.get("direction") == "owed_to_me" and not d.get("settled")]
    i_owe = [d for d in debts if d.get("direction") != "owed_to_me" and not d.get("settled")]
    total_owed_to_me = sum(float(d.get("amount", 0)) for d in owed_to_me)
    total_i_owe = sum(float(d.get("amount", 0)) for d in i_owe)

    if owed_to_me or i_owe:
        lines.append("")
        lines.append("Datorii nesoldate:")
        for d in owed_to_me:
            lines.append(f"  - {d.get('name', '?')} ÎI DATOREAZĂ lui Sergiu {_money(d.get('amount', 0))} RON")
        for d in i_owe:
            lines.append(f"  - Sergiu ÎI DATOREAZĂ lui {d.get('name', '?')} {_money(d.get('amount', 0))} RON")

    # ── TOTALUL PE CARE ÎL CERE DE OBICEI ──
    avere_totala = total_cash + inv["active_value"] + total_owed_to_me - total_i_owe
    lines.append("")
    lines.append("AVERE TOTALĂ (lichizi + valoarea stocului + de primit − de dat):")
    lines.append(
        f"  {_money(total_cash)} + {_money(inv['active_value'])} + "
        f"{_money(total_owed_to_me)} − {_money(total_i_owe)} = "
        f"{_money(avere_totala)} RON"
    )

    # Jurnalul de tranzacții NU e inclus aici intenționat — e zgomot (multe
    # intrări, nu neapărat exacte) pentru o întrebare tipică de tip "cât am".
    # Disponibil separat, la cerere explicită, în categoria 'tranzactii'.

    return "\n".join(lines)


def _fmt_tranzactii(n: int = 15) -> str:
    """Jurnalul de tranzacții — DOAR la cerere explicită ('arată-mi tranzacțiile',
    'ce mișcări am avut'), nu face parte din răspunsul standard de finanțe."""
    transactions = _fin("transactions.json")
    if not transactions:
        return "=== TRANZACȚII ===\nNicio tranzacție înregistrată."

    accounts = {a.get("id"): a.get("name", "?") for a in _fin("accounts.json")}
    recent = sorted(transactions, key=lambda t: t.get("date", ""), reverse=True)[:n]

    lines = [f"=== ULTIMELE {len(recent)} TRANZACȚII ==="]
    for tx in recent:
        sign = "+" if tx.get("type") == "in" else "-"
        acc = accounts.get(tx.get("account_id"), "?")
        note = f" ({tx['note']})" if tx.get("note") else ""
        lines.append(f"  {tx.get('date', '?')} [{acc}]: {sign}{_money(tx.get('amount', 0))} RON{note}")
    return "\n".join(lines)


def _fmt_vanzari() -> str:
    """Ce a vândut Sergiu până acum — DOAR la cerere explicită
    ('ce am vândut', 'cum au mers vânzările'), nu face parte din finanțe standard."""
    sold = [i for i in _fin("inventory_sold.json") if i.get("status") == "sold"]
    if not sold:
        return "=== VÂNZĂRI ===\nNimic vândut încă."

    total_recovered = sum(float(i.get("sold_amount", 0)) for i in sold)
    total_cost = sum(float(i.get("cost_basis", 0)) for i in sold)

    lines = [
        "=== CE AI VÂNDUT PÂNĂ ACUM ===",
        f"Total: {len(sold)} articole, recuperat {_money(total_recovered)} RON, "
        f"profit realizat {_money(total_recovered - total_cost)} RON",
    ]
    for it in sorted(sold, key=lambda i: i.get("sold_at", ""), reverse=True):
        lines.append(
            f"  - {it.get('name', '?')}: cumpărat cu {_money(it.get('cost_basis', 0))}, "
            f"vândut cu {_money(it.get('sold_amount', 0))} RON pe {it.get('sold_at', '?')}"
        )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# TARGETURI / REMINDERE / PROIECTE
# ─────────────────────────────────────────────────────────────

def _fmt_targeturi() -> str:
    goals = _load_json(os.path.join(DATA_DIR, "targets.json"), {}).get("goals", [])
    if not goals:
        return "=== TARGETURI ===\nNiciun target activ."

    lines = ["=== TARGETURI (obiective personale) ==="]
    for g in goals:
        deadline = g.get("deadline") or "fără termen"
        lines.append(
            f"  - [{g.get('priority', '?')}] {g.get('title', '?')} "
            f"— progres {g.get('progress', 0)}%, termen {deadline}, "
            f"categorie {g.get('category', '?')}"
        )
        if g.get("description"):
            lines.append(f"      scop: {g['description']}")
    return "\n".join(lines)


def _fmt_remindere() -> str:
    lines = ["=== REMINDERE & MENTENANȚĂ ==="]

    reminders = _load_json(os.path.join(DATA_DIR, "reminders.json"), {}).get("reminders", [])
    pending = [r for r in reminders if not r.get("checked")]
    if pending:
        lines.append("De făcut:")
        for r in pending:
            lines.append(f"  - [{r.get('priority', '?')}] {r.get('title', '?')}")
            if r.get("description"):
                lines.append(f"      {r['description']}")
    else:
        lines.append("Niciun reminder activ.")

    # Mentenanță — marcăm ce a depășit intervalul
    items = _load_json(os.path.join(DATA_DIR, "maintenance.json"), {}).get("items", [])
    due_lines = []
    for item in items:
        for task in item.get("tasks", []):
            days = _days_since(task.get("last_done", ""))
            interval = int(task.get("interval_days", 0) or 0)
            if days is None:
                due_lines.append(f"  - {item.get('name', '?')} / {task.get('name', '?')}: niciodată făcut")
            elif interval and days >= interval:
                due_lines.append(
                    f"  - {item.get('name', '?')} / {task.get('name', '?')}: "
                    f"SCADENT (ultima dată acum {days} zile, interval {interval})"
                )
    if due_lines:
        lines.append("Mentenanță scadentă:")
        lines.extend(due_lines)

    return "\n".join(lines)


def _collect_pending_steps(steps: list, depth: int = 0) -> list:
    """Extrage recursiv pașii nefinalizați dintr-un plan de proiect."""
    out = []
    for step in steps or []:
        if step.get("status") != "done":
            out.append(f"{'    ' * (depth + 1)}- [{step.get('priority', '?')}] {step.get('title', '?')}")
        out.extend(_collect_pending_steps(step.get("children", []), depth + 1))
    return out


def _fmt_proiecte() -> str:
    data = _load_json(os.path.join(DATA_DIR, "electronics_data.json"), {})
    projects = data.get("projects", [])
    if not projects:
        return "=== PROIECTE ===\nNiciun proiect înregistrat."

    lines = ["=== PROIECTE (electronică / robotică) ==="]
    for p in projects:
        lines.append(f"  Proiect '{p.get('name', '?')}' — status: {p.get('status', '?')}")
        if p.get("description"):
            lines.append(f"      {p['description']}")

        pending = _collect_pending_steps(p.get("plan", []))
        if pending:
            lines.append("      Pași rămași de făcut:")
            lines.extend(f"    {s}" for s in pending)
        else:
            lines.append("      (toți pașii din plan sunt bifați)")

        devlog = p.get("devlog", [])
        if devlog:
            last = devlog[-1]
            lines.append(f"      Ultimul devlog ({last.get('date', '?')}): {last.get('title', '?')}")

    wishlist = data.get("wishlist", [])
    if wishlist:
        lines.append("  Wishlist componente:")
        for w in wishlist:
            lines.append(f"      - {w.get('name', w) if isinstance(w, dict) else w}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# SPORT & OBICEIURI
# ─────────────────────────────────────────────────────────────

def _fmt_sport() -> str:
    lines = ["=== SPORT / CORP ==="]

    profile = _load_json(os.path.join(GYM_DIR, "profile.json"), {})
    phase = _load_json(os.path.join(GYM_DIR, "phase.json"), {})
    if profile:
        lines.append(
            f"Înălțime {profile.get('height', '?')} cm, "
            f"greutate țintă {profile.get('goal_weight', '?')} kg."
        )
    if phase:
        lines.append(f"Fază curentă: {phase.get('current', '?')}.")

    weights = _load_json(os.path.join(GYM_DIR, "weight_log.json"), [])
    if isinstance(weights, list) and weights:
        last = weights[-1]
        lines.append(f"Ultima cântărire: {last.get('weight', '?')} kg pe {last.get('date', '?')}.")
        if len(weights) > 1:
            delta = float(last.get("weight", 0)) - float(weights[0].get("weight", 0))
            trend = "crescut" if delta > 0 else "scăzut" if delta < 0 else "stagnat"
            lines.append(f"Evoluție totală: a {trend} {abs(round(delta, 1))} kg de la prima cântărire.")

    measurements = _load_json(os.path.join(GYM_DIR, "measurements.json"), {}).get("entries", [])
    if measurements:
        m = measurements[0]
        lines.append(
            f"Ultimele măsurători ({m.get('date', '?')}): "
            f"braț încordat {m.get('brat_incordat', '?')}, piept {m.get('piept', '?')}, "
            f"talie {m.get('talie', '?')}, coapsă {m.get('coapsa', '?')} cm."
        )

    checks = _load_json(os.path.join(GYM_DIR, "daily_checks.json"), {}).get("checks", [])
    if checks:
        last = checks[-1]
        lines.append(f"Ultim check alimentar: {last.get('level', '?')} pe {last.get('date', '?')}.")

    return "\n".join(lines) if len(lines) > 1 else "=== SPORT / CORP ===\nNicio dată."


def _fmt_obiceiuri() -> str:
    lines = ["=== OBICEIURI ZILNICE ==="]

    data = _load_json(os.path.join(DATA_DIR, "daily_tasks.json"), {})
    tasks = data.get("tasks", [])
    checks = data.get("checks", {})
    today = date.today().isoformat()
    done_today = set(checks.get(today, []))

    if tasks:
        for t in tasks:
            mark = "BIFAT azi" if t.get("id") in done_today else "NEbifat azi"
            lines.append(f"  - {t.get('name', '?')}: {mark}")
    else:
        lines.append("  Niciun obicei definit.")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# REGISTRU PUBLIC
# ─────────────────────────────────────────────────────────────

CATEGORIES = {
    "finante":    _fmt_finante,
    "tranzactii": _fmt_tranzactii,   # DOAR la cerere explicită — nu în întrebări generale de bani
    "vanzari":    _fmt_vanzari,      # DOAR la cerere explicită — "ce am vândut"
    "targeturi":  _fmt_targeturi,
    "remindere":  _fmt_remindere,
    "proiecte":   _fmt_proiecte,
    "sport":      _fmt_sport,
    "obiceiuri":  _fmt_obiceiuri,
}


def available_categories() -> list:
    return list(CATEGORIES.keys())


def read_context(categories) -> str:
    """
    Întoarce contextul pentru categoriile cerute, ca text compact.

    Args:
        categories: listă de nume de categorii (sau un singur string).
                    Necunoscutele sunt ignorate cu un avertisment în output.
    """
    if isinstance(categories, str):
        categories = [categories]
    if not categories:
        categories = list(CATEGORIES.keys())

    blocks, unknown = [], []
    for cat in categories:
        key = str(cat).strip().lower()
        fn = CATEGORIES.get(key)
        if not fn:
            unknown.append(key)
            continue
        try:
            blocks.append(fn())
        except Exception as e:
            logger.error(f"❌ [Context Tools] Eroare la categoria '{key}': {e}", exc_info=True)
            blocks.append(f"=== {key.upper()} ===\n(eroare la citire: {e})")

    if unknown:
        blocks.append(
            f"(categorii necunoscute ignorate: {', '.join(unknown)}. "
            f"Disponibile: {', '.join(CATEGORIES)})"
        )

    return "\n\n".join(blocks) if blocks else "Nicio dată disponibilă."
