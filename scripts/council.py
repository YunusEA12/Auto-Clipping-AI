"""Free KI-Council for cloud and local sessions: Gemini reviews automatically over the free tier
of the Gemini API, and GPT reviews through the ChatGPT app with a ready-to-paste prompt that `ask`
writes. Nothing here calls a paid API.

Gemini is free as long as GEMINI_API_KEY belongs to a Google project without billing enabled.
Host: generativelanguage.googleapis.com.
Optional: COUNCIL_GEMINI_MODEL, COUNCIL_BLOCKLIST (comma-separated terms that must never leave
the machine, e.g. employer/customer names; also read from .council/blocklist.txt).

Usage (see .claude/skills/council/SKILL.md):
    uv run --locked python scripts/council.py status
    uv run --locked python scripts/council.py ask --mode review --question-file q.md \
        --context-file ctx.md --own-file own.md [--file analyze.py ...]
    uv run --locked python scripts/council.py rate --job <id> --rating-file rating.md --contribution 2
"""

import argparse
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = ROOT / ".council"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
MAX_CONTEXT_CHARS = 400_000

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[=:]\s*['\"]?[^\s'\"<]{8,}"),
    re.compile(r"https://discord(app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"),
]

MODE_INSTRUCTIONS = {
    "answer": (
        "Beantworte die Aufgabe selbstständig und vollständig. Gliederung: 1) Antwort, "
        "2) Begründung, 3) Risiken und Randfälle, 4) was du nicht sicher weißt."
    ),
    "review": (
        "Prüfe das Ergebnis im Kontext streng und unabhängig. Liste jeden Mangel als "
        "Punkt mit: Schweregrad (kritisch/hoch/mittel/niedrig), Fundstelle, Begründung, "
        "konkreter Verbesserung. Nenne danach, was gut ist. Erfinde keine Mängel."
    ),
    "vote": (
        "Gib eine klare Empfehlung mit Konfidenz in Prozent, die drei wichtigsten Gründe, "
        "das stärkste Gegenargument und was deine Empfehlung kippen würde."
    ),
}

SYSTEM_PROMPT = (
    "Du bist ein unabhängiger, strenger Gutachter in einem Gremium. Antworte auf Deutsch. "
    "Trenne klar zwischen Belegtem (mit Fundstelle im Kontext) und Vermutetem. "
    "Dein Wissen kann veraltet sein: kennzeichne zeitabhängige Fakten (Preise, Versionen, "
    "Plattformregeln) als 'aktuell prüfen'."
)


class SensitiveContentError(ValueError):
    pass


def check_sensitive(text: str, blocklist: List[str]) -> None:
    for pattern in SECRET_PATTERNS:
        match = pattern.search(text)
        if match:
            raise SensitiveContentError(
                f"Mögliches Geheimnis gefunden ({match.group(0)[:12]}…). Stelle schwärzen, z. B. <SECRET>.")
    lowered = text.lower()
    for term in blocklist:
        if term and term.lower() in lowered:
            raise SensitiveContentError(
                f"Begriff von der Sperrliste gefunden: {term!r}. Inhalt weglassen oder Nutzer fragen.")


def load_blocklist(jobs_dir: Path = JOBS_DIR) -> List[str]:
    terms = [t.strip() for t in os.environ.get("COUNCIL_BLOCKLIST", "").split(",")]
    path = jobs_dir / "blocklist.txt"
    if path.exists():
        terms += [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [t for t in terms if t and not t.startswith("#")]


def build_prompt(mode: str, question: str, context: str) -> str:
    parts = [f"## Modus\n{MODE_INSTRUCTIONS[mode]}", f"## Aufgabe\n{question.strip()}"]
    if context.strip():
        parts.append(f"## Kontext\n{context.strip()}")
    return "\n\n".join(parts)


def chatgpt_prompt(prompt: str) -> str:
    return f"{SYSTEM_PROMPT}\n\n{prompt}"


def ask_gemini(prompt: str) -> str:
    from google import genai
    from google.genai import types

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY fehlt")
    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model=os.environ.get("COUNCIL_GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    if not response.text:
        raise RuntimeError("leere Antwort")
    return response.text


def _redact(text: str) -> str:
    value = os.environ.get("GEMINI_API_KEY")
    return text.replace(value, "<SECRET>") if value else text


def run_job(mode: str, question: str, context: str, own_answer: str,
            jobs_dir: Path = JOBS_DIR, gemini=ask_gemini) -> dict:
    prompt = build_prompt(mode, question, context)
    if len(prompt) > MAX_CONTEXT_CHARS:
        raise ValueError(f"Kontext zu groß ({len(prompt)} Zeichen, max. {MAX_CONTEXT_CHARS}). Auszüge senden.")
    # Guards both outputs: the Gemini call and the ChatGPT prompt file the user will paste.
    check_sensitive(prompt, load_blocklist(jobs_dir))

    try:
        review = {"ok": True, "text": gemini(prompt)}
    except Exception as e:  # a provider failure is reported, never fatal
        review = {"ok": False, "text": "Gemini-Gutachten nicht verfügbar.",
                  "error": f"{type(e).__name__}: {_redact(str(e))[:300]}"}

    job_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = jobs_dir / f"{job_id}-chatgpt-prompt.md"
    prompt_path.write_text(chatgpt_prompt(prompt), encoding="utf-8")
    job = {
        "job_id": job_id, "created_at": datetime.now(timezone.utc).isoformat(), "mode": mode,
        "question": question, "own_answer": own_answer, "gemini": review,
        "chatgpt_prompt": str(prompt_path),
    }
    (jobs_dir / f"{job_id}.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def rate(job_id: str, rating: str, contribution: int, jobs_dir: Path = JOBS_DIR) -> dict:
    path = jobs_dir / f"{job_id}.json"
    job = json.loads(path.read_text(encoding="utf-8"))
    if "rating" not in job:  # the first rating is final, so later edits can't rewrite history
        job["rating"] = rating
        job["contribution"] = contribution
        job["rated_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def status(jobs_dir: Path = JOBS_DIR) -> str:
    lines = [
        f"GEMINI_API_KEY: {'gesetzt' if os.environ.get('GEMINI_API_KEY') else 'FEHLT'}",
        f"Gemini-Modell: {os.environ.get('COUNCIL_GEMINI_MODEL', DEFAULT_GEMINI_MODEL)} (Gratis-Tarif)",
        "GPT: manuell über die ChatGPT-App (kostenlos)",
    ]
    jobs = ([json.loads(p.read_text(encoding="utf-8")) for p in sorted(jobs_dir.glob("*.json"))]
            if jobs_dir.exists() else [])
    ok = sum(1 for j in jobs if j["gemini"]["ok"])
    lines.append(f"Jobs: {len(jobs)} (Gemini: {ok} erfolgreich, {len(jobs) - ok} ausgefallen)")
    failed = [j["gemini"].get("error") for j in jobs if not j["gemini"]["ok"]]
    if failed:
        lines.append(f"Letzter Gemini-Fehler: {failed[-1]}")
    rated = [j for j in jobs if "contribution" in j]
    if rated:
        total = sum(j["contribution"] for j in rated)
        lines.append(f"Mehrwert: {total} berechtigte neue Punkte in {len(rated)} bewerteten Jobs "
                     f"({sum(1 for j in rated if j['contribution'] == 0)} ohne neuen Beitrag)")
    return "\n".join(lines)


def _read(path: Optional[str]) -> str:
    return Path(path).read_text(encoding="utf-8") if path else ""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("--mode", choices=sorted(MODE_INSTRUCTIONS), default="answer")
    ask.add_argument("--question", default="")
    ask.add_argument("--question-file")
    ask.add_argument("--context-file")
    ask.add_argument("--file", action="append", default=[], help="Datei als Kontext anhängen (mehrfach)")
    ask.add_argument("--own-file", required=True, help="Eigene Antwort C, bleibt lokal")
    rt = sub.add_parser("rate")
    rt.add_argument("--job", required=True)
    rt.add_argument("--rating-file", required=True)
    rt.add_argument("--contribution", type=int, required=True)
    sub.add_parser("status")
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    if args.cmd == "status":
        print(status())
        return 0
    if args.cmd == "rate":
        job = rate(args.job, _read(args.rating_file), args.contribution)
        print(f"Bewertung gespeichert am {job['rated_at']} (unveränderlich), Beitrag: {job['contribution']}")
        return 0

    question = args.question or _read(args.question_file)
    context = _read(args.context_file)
    for f in args.file:
        context += f"\n\n### Datei: {f}\n```\n{Path(f).read_text(encoding='utf-8')}\n```"
    try:
        job = run_job(args.mode, question, context, _read(args.own_file))
    except (SensitiveContentError, ValueError) as e:
        print(f"BLOCKIERT: {e}", file=sys.stderr)
        return 2
    print(f"Job-ID: {job['job_id']}")
    print(f"ChatGPT-Prompt (für die App, kostenlos): {job['chatgpt_prompt']}")
    print(f"\n===== Gutachten Gemini =====\n{job['gemini']['text']}")
    if not job["gemini"]["ok"]:
        print("\nHinweis: Gemini ausgefallen – Ursache mit `status` prüfen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
