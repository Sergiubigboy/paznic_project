"""
tools/journal_split.py — un text, mai multe zile de jurnal
===========================================================
N-ai scris o săptămână? Scrii o singură dată, liber („luni am fost la sală,
marți și miercuri am stat acasă bolnav, joi...") și modelul îl împarte pe
zile, la persoana I, ca și cum ai fi scris în fiecare seară.

Reguli date modelului:
    - nu inventează nimic: o zi despre care n-ai zis nimic rămâne goală;
    - ce spui despre o perioadă („toată săptămâna am dormit prost") ajunge în
      fiecare zi la care se referă;
    - păstrează cuvintele tale, doar le așază pe zile.

Rezultatul e o PROPUNERE: pagina ți-o arată zi cu zi, o corectezi, abia apoi
se salvează.

CLI:
    python -m tools.journal_split "luni sala, marti bolnav" --from 2026-09-28 --to 2026-10-04
"""

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

MAX_DAYS = 31
_ZILE = ("luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică")
_LUNI = ("ian", "feb", "mar", "apr", "mai", "iun", "iul", "aug", "sep", "oct", "nov", "dec")


def day_label(d: date) -> str:
    return f"{_ZILE[d.weekday()]} {d.day} {_LUNI[d.month - 1]}"


def days_between(start: date, end: date) -> list:
    """Zilele din interval, cel mult până azi (nu scrii în jurnal pentru mâine)."""
    if end < start:
        start, end = end, start
    end = min(end, date.today())
    n = min((end - start).days + 1, MAX_DAYS)
    return [start + timedelta(days=i) for i in range(n)]


def _ask():
    import ai_core
    # Pe Pi rulează ai_core cu ask_gemini_json; local poate fi ask_llm_json.
    return getattr(ai_core, "ask_llm_json", None) or getattr(ai_core, "ask_gemini_json")


def split(text: str, start: date, end: date) -> dict:
    """Întoarce {"status", "days": [{"date", "label", "text"}]} — câte un
    element pentru FIECARE zi din interval (cu text gol unde n-ai zis nimic)."""
    text = (text or "").strip()
    if not text:
        return {"status": "error", "message": "Scrie ceva despre zilele astea."}
    days = days_between(start, end)
    if not days:
        return {"status": "error", "message": "Intervalul e în viitor."}
    if len(days) == 1:
        return {"status": "ok", "days": [{"date": days[0].isoformat(), "label": day_label(days[0]), "text": text}]}

    azi = date.today()
    lista = "\n".join(f"- {d.isoformat()} = {day_label(d)}" + (" (azi)" if d == azi else
                      " (ieri)" if d == azi - timedelta(days=1) else "") for d in days)
    prompt = f"""Sergiu n-a scris în jurnal câteva zile și acum scrie o dată despre toată perioada.
Împarte ce a scris pe zile, ca intrări de jurnal la persoana I, în română, cu cuvintele lui.

ZILELE (folosește exact aceste date):
{lista}

REGULI:
1. NU inventa nimic. O zi despre care nu spune nimic primește text gol "".
2. Ce spune despre o perioadă („toată săptămâna", „în weekend", „de marți până joi")
   pune în FIECARE zi la care se referă.
3. Păstrează cuvintele și tonul lui; doar le așezi pe zile. Fără titluri, fără liste.
   Dacă o propoziție vorbește despre mai multe zile, fiecare zi primește doar partea
   care o privește (ex: „marți și miercuri acasă, miercuri am avut test" → marți:
   „am stat acasă"; miercuri: „am stat acasă și am avut test").
4. „ieri", „alaltăieri", numele zilelor se raportează la azi = {day_label(azi)} ({azi.isoformat()}).

CE A SCRIS:
\"\"\"{text}\"\"\"
"""
    schema = {"type": "OBJECT", "properties": {
        "zile": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "date": {"type": "STRING"}, "text": {"type": "STRING"}}, "required": ["date", "text"]}},
    }, "required": ["zile"]}
    try:
        rez = _ask()(prompt, schema=schema, temperature=0.2) or {}
    except Exception as e:
        logger.error(f"❌ [Jurnal] Împărțirea pe zile a eșuat: {e}")
        return {"status": "error", "message": "Nu am putut împărți textul (AI indisponibil). Încearcă din nou."}

    by_date = {}
    for z in rez.get("zile") or []:
        d, t = str(z.get("date", "")).strip(), str(z.get("text", "")).strip()
        if t:
            by_date[d] = (by_date.get(d, "") + "\n" + t).strip()
    if not by_date:
        return {"status": "error", "message": "N-am reușit să leg textul de nicio zi. Pomenește zilele (ex: „luni…, marți…”)."}
    return {"status": "ok", "days": [
        {"date": d.isoformat(), "label": day_label(d), "text": by_date.get(d.isoformat(), "")} for d in days]}


def main(argv=None) -> None:
    import argparse
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="python -m tools.journal_split")
    p.add_argument("text")
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    a = p.parse_args(argv)
    r = split(a.text, date.fromisoformat(a.start), date.fromisoformat(a.end))
    if r["status"] != "ok":
        sys.exit(r["message"])
    for d in r["days"]:
        print(f"\n── {d['label']} ({d['date']})\n{d['text'] or '(nimic)'}")


if __name__ == "__main__":
    main()
