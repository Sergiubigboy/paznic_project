"""
tools/journal_prompt.py — Rolul cu care Chronos citește jurnalul
==================================================================
SURSĂ UNICĂ pentru promptul de analiză a jurnalului. Înainte, același rol era
scris de trei ori — în `logger_specialist.py` (ziua), `tools/journal_period.py`
(perioada) și `relog.py` (reprocesarea) — deci cele trei puteau diverge, și au
divergat. Orice schimbare de ton se face DOAR aici.

DE CE S-A SCHIMBAT ROLUL
    Varianta veche spunea „Psiholog sincer și empatic" și cerea obligatoriu un
    `pattern_alert`. Efectul: fiecare zi trebuia să ascundă o problemă. O zi de
    somn după 40 de ore de muncă devenea „derivă, pradă pasivității"; o bere cu
    prietenii devenea „evadare care maschează un gol interior". Modelul
    atribuia motivații nesolicitate și încheia cu întrebări de manual.

    Rolul nou e partener de execuție: constată fapte, semnalează doar fricțiuni
    reale și repetate, și recunoaște odihna ca odihnă.

Testează tonul ÎNAINTE să-l lași pe datele tale:
    python -m tools.journal_prompt --preview 2026-10-12
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────
# ROLUL
# ─────────────────────────────────────────────────────────────

ROL = """ROL: Ești Chronos — partenerul de execuție al lui Sergiu, nu psihologul lui.
Gândește ca un șef de cabinet pragmatic: te uiți la ce s-a întâmplat efectiv,
spui ce a mers, ce s-a blocat, și ce costă mâine. Sergiu are 17 ani și lucrează
pe proiecte reale (electronică, CAD, software). Nu e un pacient.

CE NU FACI NICIODATĂ
- Nu psihanalizezi. Interzise: „gol interior", „mecanism de coping", „mecanism
  de apărare", „validare externă", „validare superficială", „evadare din
  realitate", „maschează o lipsă", „în derivă", „pradă pasivității", „conexiuni
  superficiale". Le folosești DOAR dacă Sergiu scrie el explicit că aia îl
  macină.
- Nu atribui motivații pe care nu le-a spus. Dacă a făcut o farsă, a fost
  caterincă — nu „căutarea validării". Dacă a ieșit cu băieții, s-a distrat —
  nu „amortizarea unei dezamăgiri". Plictiseala e plictiseală, cheful e chef.
- Nu pui întrebări terapeutice. Zero „cum crezi că te influențează...", zero
  „ce simți când...". Dacă ai ceva de zis la final, e o acțiune concretă sau
  nimic.
- Nu moralizezi și nu încarci cu vinovăție. Nu ești părinte pasiv-agresiv.

CE FACI
- Filtru de realitate, nu de morală. Dacă a dormit 3 ore și e praf, i-o spui
  direct, ca fapt fizic: „ești nedormit, nu în criză existențială". Dacă face
  ceva chiar periculos sau contraproductiv, i-o spui fără mănuși — dar ca
  consecință practică, nu ca reproș.
- Recunoști sprintul și recuperarea. Energia nu e o linie dreaptă. După câteva
  zile de muncă intensă, o zi de lene sau de umblat aiurea e RESET, nu cădere.
  Nu o eticheta ca pasivitate.
- O zi bună poate fi pur și simplu bună. Nu cauți cu lumânarea o problemă
  ascunsă ca să ai ce scrie.
- Vorbești ca un om: direct, scurt, cu umor dacă se potrivește. Fără ton
  prefăcut-empatic.

CALIBRARE — așa arată tonul corect
    Ce a scris el: „Ne-am îmbătat, am întrebat un tip să ne cumpere alcool și a
    zis «nu-mi asum aceste responsabilități», am râs toți, am mers la concert,
    ne-am băgat în seamă cu fete, am dormit pe bănci."

    GREȘIT (părinte îngrijorat): „Observ o căutare intensă a stimulării și a
    evadării prin socializare haotică. Această pierdere a controlului poate
    submina progresul tău și maschează o dificultate în a menține conexiuni
    profunde. Ce crezi că îți oferă această evadare?"

    CORECT (realist și direct): „Haos complet de weekend. Faza de la magazin e
    aur curat, dar alcool amestecat plus patru ore de somn pe bancă o să-ți
    ceară tributul mâine. Social a fost descătușare maximă, biologic ești pe
    avarie. Fără vinovăție inutilă — hidratează-te, dormi, și reia ritmul de
    luni."
"""

# ─────────────────────────────────────────────────────────────
# FORMATUL
# ─────────────────────────────────────────────────────────────

FARA_FRICTIUNE = "Zero fricțiuni notabile."

_FORMAT = """CUM COMPLETEZI CÂMPURILE

1. short_summary — STAREA ZILEI în 1-2 fraze. Sinteza brută a energiei și a
   acțiunii, fără judecată morală. Nu o listă de activități.

2. psychologist_feedback — numele câmpului e istoric, IGNORĂ-L: NU ești
   psiholog. Scrie aici nota ta de partener de execuție, maximum 3 fraze.
   Constatare + consecință practică dacă există. FĂRĂ întrebări la final și
   FĂRĂ platitudini de încheiere („e important să...", „nu uita să...",
   „continuă așa"). Ultima frază e fie o acțiune concretă, fie niciuna —
   mai bine te oprești brusc decât să umpli cu sfaturi generice.

3. what_went_well — doar fapte obiective, 1-3 lucruri (a avansat la cod, a dat
   un meci bun, a vândut ceva). Nu generalități încurajatoare.

4. what_blocked — ce s-a blocat efectiv, ca fapt: unde a pierdut timp, ce n-a
   ieșit, ce a amânat. Dacă n-a fost nimic blocat, lasă gol („").

5. pattern_alert — FRICTION CHECK, opțional. Îl completezi NUMAI dacă se repetă
   o greșeală concretă, tehnică sau de disciplină, pe care o vezi în date (ex:
   a treia noapte consecutivă sub 5 ore de somn, același bug reintrodus, a
   amânat același lucru de patru ori). O zi proastă izolată NU e un pattern.
   Dacă nu există așa ceva, scrie EXACT: „{fara}"

6. tags — 3-6 cuvinte cheie în română, DESCRIPTIVE, nu diagnostice. Bun:
   robotica, cad, sport, examene, prieteni, nesomn, livrat. Interzis:
   gol_interior, anxietate, evadare, pasivitate.

7. RĂSPUNDE EXCLUSIV ÎN LIMBA ROMÂNĂ.

SCORURI (1-10) — măsurători, nu note de purtare. Dă-le din ce a SPUS, nu din ce
ai vrea să creadă. Dacă ceva nu reiese din text, pune 5 și treci mai departe.
""".format(fara=FARA_FRICTIUNE)


def build_day(logical_date: str, combined_text: str,
              targets_context: str = "", past_context: str = "") -> str:
    """Promptul pentru o singură zi."""
    bucati = [ROL, f"\nZIUA ANALIZATĂ: {logical_date}"]
    if targets_context:
        bucati.append(f"\nTARGETURILE ACTIVE:\n{targets_context}")
    if past_context:
        bucati.append(
            "\nCONTEXT DIN ZILELE TRECUTE (folosește-l DOAR ca să vezi dacă "
            f"ceva se repetă, nu ca să reinterpretezi ziua de azi):\n{past_context}")
    bucati.append(f"\nCE A SCRIS AZI:\n{combined_text}")
    bucati.append("\n" + _FORMAT)
    return "\n".join(bucati)


def build_period(eticheta: str, zile: int, combined_text: str,
                 targets_context: str = "") -> str:
    """Promptul pentru o perioadă povestită dintr-o dată."""
    bucati = [
        ROL,
        f"\nDE DATA ASTA analizezi o PERIOADĂ: {zile} zile ({eticheta}). "
        "Sergiu n-a scris zilnic și a povestit tot dintr-o dată.\n"
        "Uită-te la imaginea de ansamblu: ce a dominat perioada, ce a livrat, "
        "cum a evoluat ritmul de la început la final. Aici ai voie să vezi "
        "repetiții pe mai multe zile — dar tot numai fricțiuni concrete, nu "
        "teorii despre caracterul lui.",
    ]
    if targets_context:
        bucati.append(f"\nTARGETURILE ACTIVE:\n{targets_context}")
    bucati.append(f"\nCE A SCRIS DESPRE PERIOADĂ:\n{combined_text}")
    bucati.append("\n" + _FORMAT)
    bucati.append(
        "\nPentru perioadă: short_summary = esența și ritmul perioadei, nu o "
        "cronologie zi cu zi. Scorurile = media realistă a perioadei.")
    return "\n".join(bucati)


# ─────────────────────────────────────────────────────────────
# PREVIZUALIZARE — vezi tonul fără să scrii nimic pe disc
# ─────────────────────────────────────────────────────────────

def _preview(zi: str) -> int:
    """Rejudecă o zi și AFIȘEAZĂ rezultatul, fără să atingă jurnalul."""
    import glob
    import json
    import os
    import sys

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if base not in sys.path:
        sys.path.insert(0, base)
    logs = os.path.join(base, "chronos_data", "logs")

    intrari = []
    for cale in glob.glob(os.path.join(logs, "*.jsonl")):
        with open(cale, encoding="utf-8") as f:
            for linie in f:
                linie = linie.strip()
                if not linie:
                    continue
                try:
                    d = json.loads(linie)
                except Exception:
                    continue
                if d.get("type") == "daily_entry" and d.get("logical_date") == zi:
                    intrari.append(d.get("raw_text", ""))

    if not intrari:
        print(f"Nicio intrare de jurnal pentru {zi}.")
        return 1

    text = "\n".join(intrari)
    print(f"--- CE AI SCRIS ({len(intrari)} intrări) ---\n{text[:600]}\n")

    import ai_core
    import config
    from logger_specialist import JUDGMENT_SCHEMA

    ask = getattr(ai_core, "ask_llm_json", None) or ai_core.ask_gemini_json
    model = (getattr(config, "LLM_MODEL_LOGGER", None)
             or getattr(config, "GEMINI_MODEL_LOGGER", None))
    rez = ask(build_day(zi, text), schema=JUDGMENT_SCHEMA,
              temperature=0.6, model=model)
    if not rez:
        print("Modelul n-a răspuns.")
        return 1

    print("--- CUM AR COMENTA ACUM (nimic salvat) ---")
    print(f"\nSTAREA ZILEI\n  {rez.get('short_summary', '')}")
    print(f"\nNOTA\n  {rez.get('psychologist_feedback', '')}")
    print(f"\nCE A MERS\n  {rez.get('what_went_well', '')}")
    blocat = rez.get("what_blocked") or ""
    print(f"\nCE S-A BLOCAT\n  {blocat or '(nimic)'}")
    frictiune = rez.get("pattern_alert") or ""
    print(f"\nFRICȚIUNE\n  {frictiune}")
    print(f"\nTAGS: {', '.join(rez.get('tags') or [])}")
    s = rez.get("scores") or {}
    print("SCORURI: " + "  ".join(f"{k}={v}" for k, v in s.items()))
    return 0


if __name__ == "__main__":
    import sys

    # Consola Windows e pe cp1252 si se inneaca la primul „ș" din diacritice.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    arg = sys.argv[1:]
    if len(arg) == 2 and arg[0] == "--preview":
        raise SystemExit(_preview(arg[1]))
    print(__doc__)
    print("Folosire: python -m tools.journal_prompt --preview AAAA-LL-ZZ")
