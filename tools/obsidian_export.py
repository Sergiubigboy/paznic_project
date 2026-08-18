"""
tools/obsidian_export.py — Export automat chronos_data → vault Obsidian
========================================================================
Transformă datele JSON în note Markdown legate între ele, pe care le poți
răsfoi, edita și căuta în Obsidian.

DE CE EXPORT ȘI NU MIGRARE:
    JSON-ul rămâne sursa de adevăr — dashboard-ul web scrie/citește direct în
    el, iar formatul structurat e mai precis pentru AI decât markdown-ul.
    Vault-ul e o OGLINDĂ pentru ochi umani, regenerabilă oricând.
    → Nu edita notele exportate așteptând să se întoarcă în aplicație;
      modificările se fac în dashboard, apoi rulezi exportul din nou.

FUNCȚIONEAZĂ PE ORICE FOLDER DE DATE:
    export_vault(source_dir=..., vault_dir=...) — deci merge identic pe
    backup-ul mai mare de pe Raspberry Pi, fără să schimbi nimic în cod:

        python -m tools.obsidian_export --source /mnt/pi/chronos_data \\
                                        --vault  /mnt/pi/ObsidianVault

Rulare pe datele locale, cu setările din .env:
        python -m tools.obsidian_export
"""

import json
import logging
import os
from datetime import datetime, date
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SOURCE = os.path.join(BASE_DIR, "chronos_data")


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.warning(f"⚠️ [Obsidian] Nu pot citi {os.path.basename(path)}: {e}")
        return default


def _num(val, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _money(val) -> str:
    v = round(_num(val), 2)
    return f"{int(v)}" if v == int(v) else f"{v:.2f}"


def _write(vault: str, relpath: str, content: str) -> str:
    full = os.path.join(vault, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return full


def _frontmatter(**kv) -> str:
    """Proprietăți Obsidian — devin filtrabile în Dataview/Bases."""
    lines = ["---"]
    for k, v in kv.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            lines.extend(f"  - {item}" for item in v)
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# GENERATOARE DE NOTE
# ─────────────────────────────────────────────────────────────

def _note_finante(src: str) -> str:
    fin = lambda n: _load(os.path.join(src, "finance", n), [])
    accounts, txs = fin("accounts.json"), fin("transactions.json")
    debts = fin("debts.json")
    inv = fin("inventory_active.json") + fin("inventory_sold.json")

    def balance(acc_id):
        t = 0.0
        for tx in txs:
            if tx.get("account_id") == acc_id:
                a = _num(tx.get("amount"))
                t += a if tx.get("type") == "in" else -a
        return round(t, 2)

    total_cash = sum(balance(a.get("id")) for a in accounts)
    active = [i for i in inv if i.get("status") == "active"]
    sold = [i for i in inv if i.get("status") == "sold"]
    val_activ = sum(_num(i.get("estimated_value")) for i in active)
    cost_activ = sum(_num(i.get("cost_basis")) for i in active)
    de_primit = sum(_num(d.get("amount")) for d in debts
                    if d.get("direction") == "owed_to_me" and not d.get("settled"))
    de_dat = sum(_num(d.get("amount")) for d in debts
                 if d.get("direction") != "owed_to_me" and not d.get("settled"))
    avere = total_cash + val_activ + de_primit - de_dat

    out = [
        _frontmatter(tip="finante", actualizat=datetime.now().strftime("%Y-%m-%d %H:%M"),
                     avere_totala=round(avere, 2), lichizi=round(total_cash, 2)),
        "", "# 💰 Finanțe", "",
        f"> **Avere totală: {_money(avere)} RON**",
        f"> lichizi {_money(total_cash)} + stoc {_money(val_activ)} "
        f"+ de primit {_money(de_primit)} − de dat {_money(de_dat)}",
        "", "## Conturi", "", "| Cont | Sold |", "|---|---:|",
    ]
    for a in accounts:
        out.append(f"| {a.get('name','?')} | {_money(balance(a.get('id')))} RON |")

    if active:
        out += ["", "## Stoc investiții", "",
                "| Articol | Cost | Estimat | Diferență |", "|---|---:|---:|---:|"]
        for i in active:
            c, e = _num(i.get("cost_basis")), _num(i.get("estimated_value"))
            out.append(f"| {i.get('name','?')} | {_money(c)} | {_money(e)} | {_money(e-c)} |")
        out.append(f"\n*Cost total {_money(cost_activ)} → valoare estimată "
                   f"{_money(val_activ)} RON.*")

    if sold:
        out += ["", "## Vândute", "", "| Articol | Cumpărat | Vândut | Data |", "|---|---:|---:|---|"]
        for i in sold:
            out.append(f"| {i.get('name','?')} | {_money(i.get('cost_basis'))} | "
                       f"{_money(i.get('sold_amount'))} | {i.get('sold_at') or '—'} |")

    nesoldate = [d for d in debts if not d.get("settled")]
    if nesoldate:
        out += ["", "## Datorii", ""]
        for d in nesoldate:
            directie = "îmi datorează" if d.get("direction") == "owed_to_me" else "îi datorez"
            out.append(f"- **{d.get('name','?')}** {directie} {_money(d.get('amount'))} RON")

    if txs:
        recente = sorted(txs, key=lambda t: t.get("date", ""), reverse=True)[:20]
        nume = {a.get("id"): a.get("name") for a in accounts}
        out += ["", "## Ultimele mișcări", "", "| Data | Cont | Sumă | Notă |", "|---|---|---:|---|"]
        for t in recente:
            semn = "+" if t.get("type") == "in" else "−"
            out.append(f"| {t.get('date','?')} | {nume.get(t.get('account_id'),'?')} | "
                       f"{semn}{_money(t.get('amount'))} | {t.get('note','')} |")
    return "\n".join(out) + "\n"


def _note_targeturi(src: str) -> str:
    goals = _load(os.path.join(src, "targets.json"), {}).get("goals", [])
    out = [_frontmatter(tip="targeturi", actualizat=datetime.now().strftime("%Y-%m-%d %H:%M"),
                        total=len(goals)),
           "", "# 🎯 Targeturi", ""]
    if not goals:
        return "\n".join(out + ["*Niciun target activ.*", ""])

    for g in goals:
        prog = int(_num(g.get("progress")))
        bara = "█" * (prog // 10) + "░" * (10 - prog // 10)
        out.append(f"## {g.get('title','?')}")
        out.append(f"`{bara}` **{prog}%** · prioritate {g.get('priority','?')} "
                   f"· categorie {g.get('category','?')}")
        if g.get("deadline"):
            try:
                zile = (datetime.strptime(g["deadline"], "%Y-%m-%d").date() - date.today()).days
                stare = f"⚠️ depășit cu {abs(zile)} zile" if zile < 0 else f"mai are {zile} zile"
            except (ValueError, TypeError):
                stare = ""
            out.append(f"Termen: {g['deadline']} — {stare}")
        if g.get("description"):
            out.append(f"\n{g['description']}")
        out.append("")
    return "\n".join(out) + "\n"


def _note_proiecte(src: str) -> list:
    """Câte o notă per proiect — se leagă între ele în vault."""
    data = _load(os.path.join(src, "electronics_data.json"), {})
    note = []

    def pasi(steps, depth=0):
        linii = []
        for s in steps or []:
            bif = "x" if s.get("status") == "done" else " "
            linii.append(f"{'  ' * depth}- [{bif}] {s.get('title','?')}")
            linii += pasi(s.get("children", []), depth + 1)
        return linii

    for p in data.get("projects", []):
        toti = []
        def numara(steps):
            for s in steps or []:
                toti.append(s.get("status") == "done")
                numara(s.get("children", []))
        numara(p.get("plan", []))
        gata = sum(1 for t in toti if t)

        out = [_frontmatter(tip="proiect", nume=p.get("name", "?"),
                            status=p.get("status", "?"),
                            pasi_gata=f"{gata}/{len(toti)}"),
               "", f"# ⚡ {p.get('name','?')}", ""]
        if p.get("description"):
            out += [p["description"], ""]
        if p.get("plan"):
            out += ["## Plan", ""] + pasi(p["plan"]) + [""]
        if p.get("devlog"):
            out += ["## Devlog", ""]
            for d in sorted(p["devlog"], key=lambda x: x.get("date", ""), reverse=True):
                out.append(f"### {d.get('date','?')} — {d.get('title','?')}")
                if d.get("text"):
                    out.append(d["text"])
                out.append("")
        note.append((f"Proiecte/{p.get('name','proiect')}.md", "\n".join(out) + "\n"))
    return note


def _note_sport(src: str) -> str:
    g = lambda n: _load(os.path.join(src, "gym", n), {})
    profile, phase = g("profile.json"), g("phase.json")
    weights = _load(os.path.join(src, "gym", "weight_log.json"), [])
    meas = g("measurements.json").get("entries", [])

    ultima = weights[-1] if isinstance(weights, list) and weights else {}
    out = [_frontmatter(tip="sport", faza=phase.get("current", "?"),
                        greutate=_num(ultima.get("weight")),
                        tinta=_num(profile.get("goal_weight"))),
           "", "# 💪 Sport", "",
           f"- Înălțime: **{profile.get('height','?')} cm**",
           f"- Greutate actuală: **{ultima.get('weight','?')} kg** ({ultima.get('date','?')})",
           f"- Țintă: **{profile.get('goal_weight','?')} kg**",
           f"- Fază: **{phase.get('current','?')}**", ""]

    if isinstance(weights, list) and len(weights) > 1:
        out += ["## Evoluție greutate", "", "| Data | kg |", "|---|---:|"]
        for w in weights[-15:]:
            out.append(f"| {w.get('date','?')} | {w.get('weight','?')} |")
        out.append("")

    if meas:
        m = meas[0]
        out += ["## Ultimele măsurători", "", f"*{m.get('date','?')}*", "",
                "| Zonă | cm |", "|---|---:|"]
        for k, v in m.items():
            if k not in ("date", "notes", "recorded_at") and v:
                out.append(f"| {k.replace('_',' ')} | {v} |")
    return "\n".join(out) + "\n"


def _note_azi(src: str) -> str:
    rem = _load(os.path.join(src, "reminders.json"), {}).get("reminders", [])
    mnt = _load(os.path.join(src, "maintenance.json"), {}).get("items", [])
    tasks = _load(os.path.join(src, "daily_tasks.json"), {})
    today = date.today().isoformat()

    out = [_frontmatter(tip="azi", data=today), "", f"# 📅 {today}", ""]

    activ = [r for r in rem if not r.get("checked")]
    out += ["## De făcut", ""]
    out += [f"- [ ] {r.get('title','?')} *({r.get('priority','?')})*" for r in activ] \
        or ["*Nimic activ.*"]

    scadente = []
    for item in mnt:
        for t in item.get("tasks", []):
            ld, interval = t.get("last_done"), int(_num(t.get("interval_days")))
            if not ld or not interval:
                continue
            try:
                zile = (date.today() - datetime.strptime(ld, "%Y-%m-%d").date()).days
            except (ValueError, TypeError):
                continue
            if zile >= interval:
                scadente.append(f"- [ ] **{t.get('name')}** la {item.get('name')} "
                                f"— acum {zile} zile (interval {interval})")
    if scadente:
        out += ["", "## Mentenanță scadentă", ""] + scadente

    checks = tasks.get("checks", {})
    if tasks.get("tasks"):
        out += ["", "## Obiceiuri", ""]
        for t in tasks["tasks"]:
            bif = "x" if t.get("id") in checks.get(today, []) else " "
            out.append(f"- [{bif}] {t.get('name','?')}")
    return "\n".join(out) + "\n"


def _note_index(src: str, generate: list) -> str:
    return "\n".join([
        _frontmatter(tip="index", generat=datetime.now().strftime("%Y-%m-%d %H:%M")),
        "", "# 🧠 Chronos", "",
        "> Oglindă generată automat din `chronos_data`.",
        "> **Nu edita aici** — modificările se fac în dashboard, apoi re-rulezi exportul.",
        "", "## Note", "",
    ] + [f"- [[{os.path.splitext(os.path.basename(p))[0]}]]" for p in generate] + [""])


# ─────────────────────────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────────────────────────

def export_vault(source_dir: Optional[str] = None,
                 vault_dir: Optional[str] = None) -> dict:
    """
    Generează vault-ul Obsidian din datele JSON.

    Args:
        source_dir: folderul cu datele (implicit chronos_data local; poate fi
                    și backup-ul de pe Pi).
        vault_dir:  unde se scrie vault-ul (implicit OBSIDIAN_VAULT_PATH din .env).
    """
    src = source_dir or DEFAULT_SOURCE
    if not vault_dir:
        try:
            from config import OBSIDIAN_VAULT_PATH
            vault_dir = OBSIDIAN_VAULT_PATH
        except ImportError:
            vault_dir = ""
    if not vault_dir:
        return {"status": "error",
                "message": "Nu știu unde e vault-ul. Setează OBSIDIAN_VAULT_PATH în .env "
                           "sau dă vault_dir explicit."}
    if not os.path.isdir(src):
        return {"status": "error", "message": f"Folderul sursă nu există: {src}"}

    scrise = []
    try:
        for rel, gen in (("Finanțe.md", _note_finante),
                         ("Targeturi.md", _note_targeturi),
                         ("Sport.md", _note_sport),
                         ("Azi.md", _note_azi)):
            _write(vault_dir, rel, gen(src))
            scrise.append(rel)

        for rel, content in _note_proiecte(src):
            _write(vault_dir, rel, content)
            scrise.append(rel)

        _write(vault_dir, "Chronos.md", _note_index(src, scrise))
        scrise.append("Chronos.md")
    except Exception as e:
        logger.error(f"❌ [Obsidian] Export eșuat: {e}", exc_info=True)
        return {"status": "error", "message": str(e), "scrise": scrise}

    logger.info(f"📓 [Obsidian] {len(scrise)} note scrise în {vault_dir}")
    return {"status": "ok", "vault": vault_dir, "note": scrise,
            "message": f"Am exportat {len(scrise)} note în Obsidian."}


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Export chronos_data → vault Obsidian")
    ap.add_argument("--source", help="Folderul cu date (implicit: chronos_data local)")
    ap.add_argument("--vault", help="Folderul vault-ului (implicit: OBSIDIAN_VAULT_PATH)")
    a = ap.parse_args()

    res = export_vault(a.source, a.vault)
    print(res["message"])
    for n in res.get("note", []):
        print("  •", n)
