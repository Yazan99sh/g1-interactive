#!/usr/bin/env python3
"""
Verify the Dialogflow CX "Nova-1" agent answers, in both Arabic and English.

Sends a set of test queries through sessions.detectIntent and prints, for each:
the detected language, the matched intent + confidence, and the spoken response.
Use it after tools/cx_import.py to confirm the agent is wired correctly.

  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json
  python tools/cx_test.py --agent-name Nova-1
  python tools/cx_test.py --location asia-south1 --agent <uuid>
  python tools/cx_test.py --agent-name Nova-1 --query "ما هو صندوق البنية التحتية الوطني؟"
"""

import argparse
import os
import sys
import uuid

try:
    from google.cloud import dialogflowcx_v3 as cx
except ImportError:
    print("ERROR: pip install google-cloud-dialogflow-cx")
    sys.exit(1)

DEFAULT_PROJECT = "nova-1-474411"
SCAN_LOCATIONS = [
    "global", "us-central1", "us-east1", "us-west1", "europe-west1",
    "europe-west2", "asia-northeast1", "asia-south1", "asia-southeast1",
]

# Default smoke set: the two user-provided test cases plus a couple of cross-language
# variants, so we prove both Arabic AND English match.
DEFAULT_QUERIES = [
    "ما هو صندوق البنية التحتية الوطني وما أهدافه؟",
    "What is the National Infrastructure Fund and what are its objectives?",
    "What operational milestones and functional capabilities has the fund achieved?",
    "ما هي الإنجازات التشغيلية والقدرات الوظيفية التي حققها الصندوق؟",
    "Hello",
    "شكراً",
]


def api_endpoint(location: str) -> str:
    return "dialogflow.googleapis.com" if location == "global" else f"{location}-dialogflow.googleapis.com"


def discover_agent(project: str, display_name: str):
    target = display_name.strip().lower()
    for loc in SCAN_LOCATIONS:
        try:
            client = cx.AgentsClient(client_options={"api_endpoint": api_endpoint(loc)})
            for a in client.list_agents(parent=f"projects/{project}/locations/{loc}"):
                if a.display_name.strip().lower() == target:
                    return loc, a.name.rsplit("/", 1)[-1]
        except Exception:
            continue
    return None, None


def is_arabic(text: str) -> bool:
    return any("؀" <= ch <= "ۿ" for ch in text)


def detect(session_client, session_base: str, query: str):
    lang = "ar" if is_arabic(query) else "en"
    # Fresh session per query: CX sessions are sticky for ~30 min, so a session first
    # seen under an old config (e.g. a generative Playbook) keeps routing that way.
    session_path = f"{session_base}/sessions/{uuid.uuid4().hex}"
    req = cx.DetectIntentRequest(
        session=session_path,
        query_input=cx.QueryInput(text=cx.TextInput(text=query), language_code=lang),
    )
    resp = session_client.detect_intent(request=req)
    qr = resp.query_result
    intent = qr.match.intent.display_name if qr.match and qr.match.intent else ""
    match_type = cx.Match.MatchType(qr.match.match_type).name if qr.match else "NO_MATCH"
    conf = qr.match.confidence if qr.match else 0.0
    texts = []
    for m in qr.response_messages:
        if m.text and m.text.text:
            texts.extend(m.text.text)
    return lang, match_type, intent, conf, " ".join(t.strip() for t in texts if t.strip())


def main() -> int:
    p = argparse.ArgumentParser(description="Verify a Dialogflow CX agent's answers (AR + EN).")
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--agent-name", default="Nova-1")
    p.add_argument("--location", default=None)
    p.add_argument("--agent", default=None)
    p.add_argument("--query", action="append", help="Custom query (repeatable). Overrides the default set.")
    p.add_argument("--threshold", type=float, default=0.6, help="Confidence the runtime requires (for the PASS mark).")
    args = p.parse_args()

    if "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
        print("ERROR: set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON.")
        return 1

    location, agent = args.location, args.agent
    if not (location and agent):
        location, agent = discover_agent(args.project, args.agent_name)
        if not agent:
            print(f"ERROR: agent '{args.agent_name}' not found in {args.project}.")
            return 1
    print(f"Agent: {args.project}/{location}/{agent}\n")

    session_client = cx.SessionsClient(client_options={"api_endpoint": api_endpoint(location)})
    session_base = f"projects/{args.project}/locations/{location}/agents/{agent}"

    queries = args.query or DEFAULT_QUERIES
    fails = 0
    for q in queries:
        try:
            lang, mtype, intent, conf, answer = detect(session_client, session_base, q)
        except Exception as e:
            print(f"[ERR ] {q!r}: {e}")
            fails += 1
            continue
        # A real PASS is a deterministic INTENT match (not a generative PLAYBOOK answer).
        ok = mtype == "INTENT" and conf >= args.threshold and bool(answer)
        if not ok:
            fails += 1
        mark = "PASS" if ok else "MISS"
        print(f"[{mark}] ({lang}) {q}")
        print(f"        intent={intent or '-'}  type={mtype}  conf={conf:.2f}")
        print(f"        answer: {answer[:160] or '(none)'}{'…' if len(answer) > 160 else ''}\n")

    total = len(queries)
    print(f"Summary: {total - fails}/{total} matched at confidence ≥ {args.threshold}.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
