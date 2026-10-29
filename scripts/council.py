"""KI-Council for cloud and local sessions: asks GPT (OpenAI API) and Gemini (Gemini API) in
parallel and returns their reviews as anonymous A/B, so Claude can grade them blind before
`reveal` shows who wrote which.

Works anywhere the two HTTPS APIs are reachable — no Codex login or desktop app required:
    GEMINI_API_KEY   (already used by the pipeline)   host: generativelanguage.googleapis.com
    OPENAI_API_KEY                                     host: api.openai.com
Optional: COUNCIL_GEMINI_MODEL, COUNCIL_OPENAI_MODEL, COUNCIL_BLOCKLIST (comma-separated terms
that must never leave the machine, e.g. employer/customer names).

Usage (see .claude/skills/council/SKILL.md):
    uv run --locked python scripts/council.py status
    uv run --locked python scripts/council.py ask --mode review --question-file q.md \
        --context-file ctx.md --own-file own.md [--file analyze.py ...] [--reviewers both]
    uv run --locked python scripts/council.py reveal --job <id> --rating-file rating.md --contribution 2
"""

import argparse
import json
import os
import random
import re
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = ROOT / ".council"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_OPENAI_MODEL = "gpt-5"
TIMEOUT_SECONDS = 300
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
    "Plattformregeln) als 'aktuell prüfen'. Nenne nicht, welches Modell du bist."
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


def load_blocklist() -> List[str]:
    terms = [t.strip() for t in os.environ.get("COUNCIL_BLOCKLIST", "").split(",")]
    path = JOBS_DIR / "blocklist.txt"
    if path.exists():
        terms += [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [t for t in terms if t and not t.startswith("#")]


def build_prompt(mode: str, question: str, context: str) -> str:
    parts = [f"## Modus\n{MODE_INSTRUCTIONS[mode]}", f"## Aufgabe\n{question.strip()}"]
    if context.strip():
        parts.append(f"## Kontext\n{context.strip()}")
    return "\n\n".join(parts)


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


def ask_gpt(prompt: str) -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY fehlt")
    resp = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": os.environ.get("COUNCIL_OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
            "instructions": SYSTEM_PROMPT,
            "input": prompt,
        },
        timeout=TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    texts = [
        c.get("text", "")
        for item in data.get("output", []) if item.get("type") == "message"
        for c in item.get("content", []) if c.get("type") == "output_text"
    ]
    if not any(texts):
        raise RuntimeError("leere Antwort")
    return "\n".join(texts)


REVIEWERS: Dict[str, Callable[[str], str]] = {"gpt": ask_gpt, "gemini": ask_gemini}


def _redact(text: str) -> str:
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
        value = os.environ.get(name)
        if value:
            text = text.replace(value, "<SECRET>")
    return text


def run_job(mode: str, question: str, context: str, own_answer: str, reviewers: List[str],
            jobs_dir: Path = JOBS_DIR, rng: Optional[random.Random] = None) -> dict:
    prompt = build_prompt(mode, question, context)
    if len(prompt) > MAX_CONTEXT_CHARS:
        raise ValueError(f"Kontext zu groß ({len(prompt)} Zeichen, max. {MAX_CONTEXT_CHARS}). Auszüge senden.")
    check_sensitive(prompt, load_blocklist())

    def call(name: str) -> dict:
        try:
            return {"ok": True, "text": REVIEWERS[name](prompt)}
        except Exception as e:  # any provider failure is reported neutrally, never fatal
            # The error detail could reveal the provider, so it stays in the job file only.
            return {"ok": False, "text": "Gutachten nicht verfügbar.",
                    "error": f"{type(e).__name__}: {_redact(str(e))[:300]}"}

    with ThreadPoolExecutor(max_workers=len(reviewers)) as pool:
        results = dict(zip(reviewers, pool.map(call, reviewers)))

    order = list(reviewers)
    (rng or random.SystemRandom()).shuffle(order)
    labels = {chr(ord("A") + i): name for i, name in enumerate(order)}
    job_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    job = {
        "job_id": job_id, "created_at": datetime.now(timezone.utc).isoformat(), "mode": mode,
        "question": question, "own_answer": own_answer, "labels": labels,
        "reviews": {label: results[name] for label, name in labels.items()},
    }
    jobs_dir.mkdir(parents=True, exist_ok=True)
    (jobs_dir / f"{job_id}.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def reveal(job_id: str, rating: str, contribution: int, jobs_dir: Path = JOBS_DIR) -> dict:
    path = jobs_dir / f"{job_id}.json"
    job = json.loads(path.read_text(encoding="utf-8"))
    if "rating" not in job:  # the first rating is final, so later edits can't rewrite history
        job["rating"] = rating
        job["contribution"] = contribution
        job["revealed_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def status(jobs_dir: Path = JOBS_DIR) -> str:
    lines = ["Schlüssel:"]
    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
        lines.append(f"  {name}: {'gesetzt' if os.environ.get(name) else 'FEHLT'}")
    lines.append(f"Modelle: Gemini={os.environ.get('COUNCIL_GEMINI_MODEL', DEFAULT_GEMINI_MODEL)}, "
                 f"GPT={os.environ.get('COUNCIL_OPENAI_MODEL', DEFAULT_OPENAI_MODEL)}")
    jobs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(jobs_dir.glob("*.json"))] if jobs_dir.exists() else []
    stats = {name: {"ok": 0, "fail": 0, "last_error": None} for name in REVIEWERS}
    for job in jobs:
        for label, name in job["labels"].items():
            review = job["reviews"][label]
            stats[name]["ok" if review["ok"] else "fail"] += 1
            if not review["ok"]:
                stats[name]["last_error"] = review.get("error")
    lines.append(f"Jobs: {len(jobs)}")
    for name, s in stats.items():
        line = f"  {name}: {s['ok']} erfolgreich, {s['fail']} ausgefallen"
        if s["last_error"]:
            line += f" (letzter Fehler: {s['last_error']})"
        lines.append(line)
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
    ask.add_argument("--reviewers", choices=["both", "gpt", "gemini"], default="both")
    rev = sub.add_parser("reveal")
    rev.add_argument("--job", required=True)
    rev.add_argument("--rating-file", required=True)
    rev.add_argument("--contribution", type=int, required=True)
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
    if args.cmd == "reveal":
        job = reveal(args.job, _read(args.rating_file), args.contribution)
        for label, name in job["labels"].items():
            print(f"Gutachten {label} = {name}")
        print(f"Bewertung gespeichert am {job['revealed_at']} (unveränderlich), Beitrag: {job['contribution']}")
        return 0

    question = args.question or _read(args.question_file)
    context = _read(args.context_file)
    for f in args.file:
        context += f"\n\n### Datei: {f}\n```\n{Path(f).read_text(encoding='utf-8')}\n```"
    reviewers = ["gpt", "gemini"] if args.reviewers == "both" else [args.reviewers]
    try:
        job = run_job(args.mode, question, context, _read(args.own_file), reviewers)
    except (SensitiveContentError, ValueError) as e:
        print(f"BLOCKIERT: {e}", file=sys.stderr)
        return 2
    print(f"Job-ID: {job['job_id']}")
    usable = sum(r["ok"] for r in job["reviews"].values())
    if usable == 0:
        print("Hinweis: kein Gutachten verfügbar – Ursache mit `status` prüfen.")
    elif usable == 1:
        print("Hinweis: nur ein verwertbares Gutachten – kein voller Council.")
    for label, review in job["reviews"].items():
        print(f"\n===== Gutachten {label} =====\n{review['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
