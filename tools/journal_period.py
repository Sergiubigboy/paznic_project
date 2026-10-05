"""
tools/journal_period.py — o intrare de jurnal pentru o perioadă întreagă
=========================================================================
N-ai scris o săptămână? Scrii o dată despre toată perioada și rămâne UN
singur bloc — nu se taie pe zile. Analiza e aceeași ca la o zi (rezumat,
feedback, ce a mers bine, pattern, taguri, cele 10 scoruri), doar privită
pe toată perioada: tendințe, nu momente.

În jurnal (chronos_data/logs/log_AAAA_LL.jsonl, luna ultimei zile):
    {"type": "period_entry",   "period_from", "period_to", "raw_text", ...}
    {"type": "period_summary", "period_from", "period_to", "analysis", ...}

CLI:
    python -m tools.journal_period add 2026-09-28 2026-10-04 "luni am mers la școală..."
    python -m tools.journal_period judge 2026-09-28 2026-10-04
"""

import glob
import json
import logging
import os
from datetime import date, datetime

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(BASE_DIR, "chronos_data", "logs")
TARGETS_FILE = os.path.join(BASE_DIR, "chronos_data", "targets.json")
MAX_DAYS = 62

_ZILE = ("luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică")
_LUNI = ("ian", "feb", "mar", "apr", "mai", "iun", "iul", "aug", "sep", "oct", "nov", "dec")


def label(d_from: date, d_to: date) -> str:
    a = f"{d_from.day} {_LUNI[d_from.month - 1]}"
    b = f"{d_to.day} {_LUNI[d_to.month - 1]}"
    return f"{a} – {b}"


def _log_file(d: date) -> str:
    return os.path.join(LOGS_DIR, f"log_{d.year}_{d.month:02d}.jsonl")


def _validate(d_from: date, d_to: date) -> str:
    if d_to < d_from:
        return "Intervalul e invers."
    if d_to > date.today():
        return "Perioada nu poate trece de azi."
    if (d_to - d_from).days + 1 > MAX_DAYS:
        return f"Maxim {MAX_DAYS} de zile într-o intrare."
    return ""


def add_period(d_from: date, d_to: date, text: str, source: str = "web") -> dict:
    text = (text or "").strip()
    if not text:
        return {"status": "error", "message": "Scrie ceva despre perioada asta."}
    err = _validate(d_from, d_to)
    if err:
        return {"status": "error", "message": err}
    entry = {
        "timestamp": datetime.now().isoformat(),
        "type": "period_entry",
        "logical_date": d_to.isoformat(),          # apare în jurnal la ultima zi
        "period_from": d_from.isoformat(),
        "period_to": d_to.isoformat(),
        "raw_text": text,
        "source": source,
    }
    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(_log_file(d_to), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"status": "ok", "label": label(d_from, d_to)}


def _collect_and_drop_old_summary(d_from: date, d_to: date) -> list:
    """Textele perioadei + scoate analiza veche (se regenerează)."""
    key = (d_from.isoformat(), d_to.isoformat())
    texts = []
    for path in glob.glob(os.path.join(LOGS_DIR, "*.jsonl")):
        keep, changed = [], False
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                except Exception:
                    keep.append(line)
                    continue
                same = (data.get("period_from"), data.get("period_to")) == key
                if same and data.get("type") == "period_entry":
                    texts.append(data.get("raw_text", ""))
                if same and data.get("type") == "period_summary":
                    changed = True
                    continue
                keep.append(line)
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(keep)
    return [t for t in texts if t]


def _targets_context() -> str:
    try:
        with open(TARGETS_FILE, "r", encoding="utf-8") as f:
            goals = json.load(f).get("goals", [])
        return "\n".join(f"- [{g.get('priority', '?')}] {g['title']} (progres: {g.get('progress', 0)}%)"
                         for g in goals) or "Niciun target activ."
    except Exception:
        return "Targeturile nu au putut fi încărcate."


def judge_period(d_from: date, d_to: date, memory=None) -> dict:
    """Analiza perioadei, în același format ca analiza unei zile."""
    texts = _collect_and_drop_old_summary(d_from, d_to)
    if not texts:
        return {"status": "error", "message": "Nicio intrare pentru perioada asta."}

    import ai_core
    import config
    from logger_specialist import JUDGMENT_SCHEMA
    ask = getattr(ai_core, "ask_llm_json", None) or getattr(ai_core, "ask_gemini_json")
    model = getattr(config, "LLM_MODEL_LOGGER", None) or getattr(config, "GEMINI_MODEL_LOGGER", None)

    zile = (d_to - d_from).days + 1
    combined = "\n\n".join(texts)
    prompt = f"""
    ROL: Psiholog sincer și empatic, cunoscut ca "Chronos". De data asta nu analizezi o zi,
    ci o PERIOADĂ: {zile} zile, {_ZILE[d_from.weekday()]} {d_from.isoformat()} – {_ZILE[d_to.weekday()]} {d_to.isoformat()}.
    Utilizatorul n-a scris zilnic și a povestit toată perioada dintr-o dată.

    Ești ca un prieten psiholog care se uită la imaginea de ansamblu:
    - ce a dominat perioada, cum a evoluat starea de la început la final;
    - pattern-uri pe mai multe zile, nu momente izolate;
    - laudă ce merită, semnalează cu empatie, conectează cu targeturile.

    TARGETURILE ACTIVE:
    {_targets_context()}

    CE A SCRIS DESPRE PERIOADĂ:
    {combined}

    INSTRUCȚIUNI STRICTE:
    1. short_summary: 2-3 propoziții cu ESENȚA și TONUL perioadei (nu o listă zi cu zi).
    2. psychologist_feedback: 3-5 propoziții despre perioadă ca întreg. Încheie cu o întrebare bună.
    3. what_went_well: 1-3 lucruri concrete pozitive din perioadă.
    4. pattern_alert: un pattern negativ din perioadă, dacă există; altfel "Niciun pattern negativ semnificativ."
    5. tags: 3-6 cuvinte cheie în română, relevante psihologic.
    6. RĂSPUNDE EXCLUSIV ÎN LIMBA ROMÂNĂ!

    SCORURI (1-10) = MEDIA REALISTĂ a perioadei, din ce a SPUS (nu ce ai vrea să crezi):
    energie, stres (10 = paralizat), dopamina (10 = activ/natural, 1 = scroll/vicii), disciplina,
    social, somn, claritate, progres_scopuri, dispozitie, corp.
    """
    analysis = ask(prompt, schema=JUDGMENT_SCHEMA, temperature=0.6, model=model)
    if not analysis:
        return {"status": "error", "message": "AI-ul n-a putut analiza perioada."}

    summary = {
        "timestamp": datetime.now().isoformat(),
        "type": "period_summary",
        "logical_date": d_to.isoformat(),
        "period_from": d_from.isoformat(),
        "period_to": d_to.isoformat(),
        "combined_text": combined,
        "analysis": analysis,
    }
    with open(_log_file(d_to), "a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")

    # Ca și zilele: intră în memoria lui Chronos, ca să-și amintească perioada.
    if memory is not None:
        try:
            memory.add_memory(f"mem_period_{d_from.isoformat()}_{d_to.isoformat()}",
                              analysis.get("psychologist_feedback", ""),
                              {"date": d_to.isoformat(), "period_from": d_from.isoformat(),
                               "summary": analysis.get("short_summary", ""),
                               "dispozitie": (analysis.get("scores") or {}).get("dispozitie", 5)})
        except Exception as e:
            logger.debug(f"[Jurnal] Memoria perioadei: {e}")
    logger.info(f"✅ [Jurnal] Perioada {label(d_from, d_to)} analizată.")
    return {"status": "ok", "summary": summary}


def main(argv=None) -> None:
    import argparse
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="python -m tools.journal_period")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("start"); a.add_argument("end"); a.add_argument("text")
    j = sub.add_parser("judge"); j.add_argument("start"); j.add_argument("end")
    args = p.parse_args(argv)
    d_from, d_to = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if args.cmd == "add":
        print(add_period(d_from, d_to, args.text))
    else:
        r = judge_period(d_from, d_to)
        if r["status"] != "ok":
            sys.exit(r["message"])
        an = r["summary"]["analysis"]
        print(an.get("short_summary"), "\n", an.get("scores"), "\n", an.get("tags"))


if __name__ == "__main__":
    main()
