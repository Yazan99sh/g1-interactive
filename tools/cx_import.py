#!/usr/bin/env python3
"""
Bulk-import bilingual (EN + AR) Q&A intents into the Dialogflow CX "Nova-1" agent.

Reads a Q&A JSON (default tools/nif_qa.json), creates one CX intent per entry with
English + Arabic training phrases and responses, and wires each as a route on the
Default Start Flow's Start Page so they fire from the first turn. This is the
content the robot answers from when Dialogflow is enabled (DIALOGFLOW_ENABLED=true);
the LLM handles anything CX doesn't match.

WHAT THIS ADDS OVER THE super-star reference (tools/cx_bulk_import.py)
---------------------------------------------------------------------
* --agent-name: find the agent by DISPLAY NAME across all CX regions (so you don't
  need to know its UUID or location). The Nova-1 agent lives in asia-south1.
* Auto-adds any languages the Q&A needs (e.g. Arabic) to the agent's supported
  languages BEFORE importing — otherwise CX silently skips phrases/responses in a
  language the agent doesn't declare. (Nova-1 ships English-only.)
* Everything else mirrors the proven reference: --purge, --dry-run, 60/min rate
  limiting with retry, and a final flow-train.

REQUIREMENTS
------------
  pip install google-cloud-dialogflow-cx
  A service-account JSON with the "Dialogflow API Admin" role (creating/deleting
  intents needs Admin, not just the runtime "Client" role). Point at it with:
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json   (mac/linux)
    set     GOOGLE_APPLICATION_CREDENTIALS=C:\\path\\to\\service_account.json  (Windows)

USAGE
-----
  # dry run (no API writes) — see what would be created
  python tools/cx_import.py --agent-name Nova-1 --qa tools/nif_qa.json --dry-run

  # real import, clearing any existing user intents first
  python tools/cx_import.py --agent-name Nova-1 --qa tools/nif_qa.json --purge

  # or target an explicit agent UUID + location instead of discovery
  python tools/cx_import.py --location asia-south1 --agent <uuid> --qa tools/nif_qa.json --purge
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    from google.cloud import dialogflowcx_v3 as cx
except ImportError:
    print("ERROR: google-cloud-dialogflow-cx is not installed.")
    print("Run: pip install google-cloud-dialogflow-cx")
    sys.exit(1)

DEFAULT_PROJECT = "nova-1-474411"
SYSTEM_INTENTS = ("Default Welcome Intent", "Default Negative Intent")
# Regions to scan when discovering an agent by display name.
SCAN_LOCATIONS = [
    "global", "us-central1", "us-east1", "us-west1", "northamerica-northeast1",
    "europe-west1", "europe-west2", "europe-west3", "australia-southeast1",
    "asia-northeast1", "asia-south1", "asia-southeast1",
]

# Dialogflow's default quota is ~60 requests/min/project; a full import makes 100+
# calls, so pace below the limit and retry on 429.
_RATE = {"interval": 1.2, "last": 0.0}


def call(fn, *args, **kwargs):
    for attempt in range(5):
        wait = _RATE["interval"] - (time.monotonic() - _RATE["last"])
        if wait > 0:
            time.sleep(wait)
        try:
            result = fn(*args, **kwargs)
            _RATE["last"] = time.monotonic()
            return result
        except Exception as e:
            _RATE["last"] = time.monotonic()
            msg = str(e)
            if attempt < 4 and ("429" in msg or "RATE_LIMIT" in msg or "Quota exceeded" in msg):
                print("    rate limited — waiting 60s, then retrying...")
                time.sleep(60)
                continue
            raise


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import bilingual Q&A intents into a Dialogflow CX agent.")
    p.add_argument("--project", default=DEFAULT_PROJECT, help=f"GCP project ID (default {DEFAULT_PROJECT})")
    p.add_argument("--agent-name", default=None, help="Find the agent by display name across regions (e.g. Nova-1)")
    p.add_argument("--location", default=None, help="Agent location (required if --agent is used)")
    p.add_argument("--agent", default=None, help="CX agent UUID (skip discovery)")
    p.add_argument("--qa", default="tools/nif_qa.json", help="Path to the Q&A JSON file")
    p.add_argument("--purge", action="store_true",
                   help="DELETE all existing non-default intents and their Start Page routes before importing.")
    p.add_argument("--dry-run", action="store_true", help="Print what would happen without calling the write API.")
    p.add_argument("--min-interval", type=float, default=1.2, help="Min seconds between API calls (default 1.2).")
    return p.parse_args()


def api_endpoint(location: str) -> str:
    return "dialogflow.googleapis.com" if location == "global" else f"{location}-dialogflow.googleapis.com"


def build_clients(location: str):
    opts = {"api_endpoint": api_endpoint(location)}
    return (
        cx.IntentsClient(client_options=opts),
        cx.PagesClient(client_options=opts),
        cx.FlowsClient(client_options=opts),
        cx.AgentsClient(client_options=opts),
    )


def discover_agent(project: str, display_name: str):
    """Scan regions for an agent whose display name matches (case-insensitive).
    Returns (location, agent_uuid) or (None, None)."""
    target = display_name.strip().lower()
    for loc in SCAN_LOCATIONS:
        try:
            client = cx.AgentsClient(client_options={"api_endpoint": api_endpoint(loc)})
            for a in client.list_agents(parent=f"projects/{project}/locations/{loc}"):
                if a.display_name.strip().lower() == target:
                    return loc, a.name.rsplit("/", 1)[-1]
        except Exception:
            continue  # region not enabled / no access — skip quietly
    return None, None


def parent_for(project: str, location: str, agent: str) -> str:
    return f"projects/{project}/locations/{location}/agents/{agent}"


def qa_languages(entries: list) -> list:
    langs: list = []
    for e in entries:
        for key in ("training_phrases", "responses"):
            for lang in (e.get(key) or {}).keys():
                if lang not in langs:
                    langs.append(lang)
    return langs


def ensure_languages(agents_client, parent: str, needed: list) -> tuple:
    """Make sure the agent declares every language the Q&A uses, adding missing ones.
    Returns (default_language, [all_supported_including_default])."""
    agent = call(agents_client.get_agent, name=parent)
    default = agent.default_language_code
    supported = list(agent.supported_language_codes)
    to_add = [l for l in needed if l != default and l not in supported]
    if to_add:
        agent.supported_language_codes.extend(to_add)
        call(
            agents_client.update_agent,
            request=cx.UpdateAgentRequest(
                agent=agent, update_mask={"paths": ["supported_language_codes"]}
            ),
        )
        print(f"  added supported language(s): {to_add}")
        agent = call(agents_client.get_agent, name=parent)
        supported = list(agent.supported_language_codes)
        default = agent.default_language_code
    return default, [default] + [c for c in supported if c != default]


def _phrases_to_proto(phrases: list) -> list:
    return [
        cx.Intent.TrainingPhrase(parts=[cx.Intent.TrainingPhrase.Part(text=p)], repeat_count=1)
        for p in phrases
    ]


def create_or_update_intent(intents_client, parent, entry, languages, default_language) -> str:
    default_phrases = entry["training_phrases"].get(default_language, [])
    if not default_phrases:
        for lang in languages:
            if entry["training_phrases"].get(lang):
                default_language, default_phrases = lang, entry["training_phrases"][lang]
                break
        if not default_phrases:
            return ""

    intent = cx.Intent(
        display_name=entry["id"],
        training_phrases=_phrases_to_proto(default_phrases),
        priority=500000,
    )
    created = call(
        intents_client.create_intent,
        request=cx.CreateIntentRequest(parent=parent, intent=intent, language_code=default_language),
    )
    intent_name = created.name

    for lang in languages:
        if lang == default_language:
            continue
        phrases = entry["training_phrases"].get(lang, [])
        if not phrases:
            continue
        try:
            intent_lang = cx.Intent(
                name=intent_name, display_name=entry["id"],
                training_phrases=_phrases_to_proto(phrases), priority=500000,
            )
            call(intents_client.update_intent,
                 request=cx.UpdateIntentRequest(intent=intent_lang, language_code=lang))
        except Exception as e:
            print(f"  warn: could not add {lang} phrases for {entry['id']}: {e}")
    return intent_name


def get_default_flow(flows_client, parent: str):
    flows = list(call(flows_client.list_flows, parent=parent))
    flow = next((f for f in flows if f.display_name == "Default Start Flow"), None)
    if flow is None:
        raise RuntimeError("Default Start Flow not found — has the agent been initialised?")
    return flow


def ensure_flow_start(agents_client, parent: str, flow_name: str) -> None:
    """Make the agent START from the Default Start Flow (intent routing), not a
    generative Playbook. A generative agent (start_playbook set) intercepts every
    query with an LLM answer and NEVER reaches our intent routes, so detectIntent
    returns matchType=PLAYBOOK instead of our answers. start_flow/start_playbook are
    a oneof, so setting start_flow clears the playbook."""
    agent = call(agents_client.get_agent, name=parent)
    if agent.start_flow == flow_name and not agent.start_playbook:
        return
    agent.start_flow = flow_name
    call(agents_client.update_agent,
         request=cx.UpdateAgentRequest(agent=agent, update_mask={"paths": ["start_flow"]}))
    print("  set agent start resource -> Default Start Flow (was a generative Playbook)")


def purge_user_intents(intents_client, flows_client, parent, flow_name, default_language) -> None:
    intents = list(call(intents_client.list_intents,
                        request=cx.ListIntentsRequest(parent=parent, language_code=default_language)))
    to_delete = [i for i in intents
                 if i.display_name not in SYSTEM_INTENTS and not i.display_name.startswith("Default ")]
    if not to_delete:
        print("  (nothing to purge — no user intents found)")
        return
    delete_names = {i.name for i in to_delete}

    flow = call(flows_client.get_flow, request=cx.GetFlowRequest(name=flow_name, language_code=default_language))
    kept = [r for r in flow.transition_routes if not r.intent or r.intent not in delete_names]
    removed = len(flow.transition_routes) - len(kept)
    if removed:
        del flow.transition_routes[:]
        flow.transition_routes.extend(kept)
        call(flows_client.update_flow,
             request=cx.UpdateFlowRequest(flow=flow, update_mask={"paths": ["transition_routes"]},
                                          language_code=default_language))
    deleted = 0
    for i in to_delete:
        try:
            call(intents_client.delete_intent, name=i.name)
            deleted += 1
            print(f"    - deleted intent {i.display_name}")
        except Exception as e:
            print(f"    warn: could not delete {i.display_name}: {e}")
    print(f"  purged {deleted} intent(s) and {removed} Start Page route(s)")


def add_route_to_start(flows_client, flow_name, intent_name, entry, languages, default_language) -> None:
    default_response = entry["responses"].get(default_language, "")
    if not default_response:
        for lang in languages:
            if entry["responses"].get(lang):
                default_response = entry["responses"][lang]
                break

    flow = call(flows_client.get_flow, request=cx.GetFlowRequest(name=flow_name, language_code=default_language))
    flow.transition_routes.append(cx.TransitionRoute(
        intent=intent_name,
        trigger_fulfillment=cx.Fulfillment(
            messages=[cx.ResponseMessage(text=cx.ResponseMessage.Text(text=[default_response]))]),
    ))
    call(flows_client.update_flow,
         request=cx.UpdateFlowRequest(flow=flow, update_mask={"paths": ["transition_routes"]},
                                      language_code=default_language))

    for lang in languages:
        if lang == default_language:
            continue
        response = entry["responses"].get(lang, "")
        if not response:
            continue
        try:
            flow_lang = call(flows_client.get_flow, request=cx.GetFlowRequest(name=flow_name, language_code=lang))
            patched = False
            for route in flow_lang.transition_routes:
                if route.intent == intent_name:
                    route.trigger_fulfillment = cx.Fulfillment(
                        messages=[cx.ResponseMessage(text=cx.ResponseMessage.Text(text=[response]))])
                    patched = True
                    break
            if patched:
                call(flows_client.update_flow,
                     request=cx.UpdateFlowRequest(flow=flow_lang, update_mask={"paths": ["transition_routes"]},
                                                  language_code=lang))
        except Exception as e:
            print(f"  warn: could not add {lang} response for {entry['id']}: {e}")


def main() -> int:
    args = parse_args()
    _RATE["interval"] = args.min_interval

    if "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
        print("ERROR: GOOGLE_APPLICATION_CREDENTIALS env var is not set.")
        print("Point it at a service-account JSON with the Dialogflow API Admin role.")
        return 1

    qa_path = Path(args.qa)
    if not qa_path.exists():
        print(f"ERROR: Q&A file not found: {qa_path}")
        return 1
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    entries = qa.get("intents", [])
    if not entries:
        print("ERROR: Q&A file has no 'intents' array.")
        return 1
    needed_langs = qa_languages(entries)

    # Resolve the target agent.
    location, agent = args.location, args.agent
    if args.agent_name:
        print(f"Discovering agent '{args.agent_name}' in project {args.project} ...")
        location, agent = discover_agent(args.project, args.agent_name)
        if not agent:
            print(f"ERROR: no agent named '{args.agent_name}' found in {args.project}.")
            return 1
        print(f"  found: location={location}  agent={agent}")
    if not (location and agent):
        print("ERROR: specify --agent-name, or both --location and --agent.")
        return 1

    print(f"\nLoaded {len(entries)} Q&A entries from {qa_path}  (languages: {needed_langs})")
    print(f"Target: {args.project}/{location}/{agent}")
    if args.purge:
        print("PURGE ON: existing non-default intents will be deleted first.")

    if args.dry_run:
        print("\nDRY RUN — would " + ("purge, then " if args.purge else "") + "create:")
        for e in entries:
            counts = ", ".join(f"{len(e['training_phrases'].get(l, []))} {l}" for l in needed_langs)
            print(f"  - {e['id']}: {counts} phrases")
        print(f"\nWould also ensure the agent supports: {needed_langs}")
        return 0

    intents_client, pages_client, flows_client, agents_client = build_clients(location)
    parent = parent_for(args.project, location, agent)

    print("\nEnsuring the agent declares the Q&A languages...")
    default_lang, all_langs = ensure_languages(agents_client, parent, needed_langs)
    print(f"  default={default_lang}  supported={all_langs}")

    print("\nResolving Default Start Flow...")
    flow_name = get_default_flow(flows_client, parent).name
    print(f"  -> {flow_name}")

    print("\nEnsuring the agent routes through the flow (not a generative Playbook)...")
    ensure_flow_start(agents_client, parent, flow_name)

    if args.purge:
        print("\nPurging existing intents:")
        purge_user_intents(intents_client, flows_client, parent, flow_name, default_lang)

    print("\nCreating intents and routes:")
    created = 0
    for e in entries:
        try:
            print(f"  + {e['id']}", end=" ")
            intent_name = create_or_update_intent(intents_client, parent, e, all_langs, default_lang)
            if not intent_name:
                print("(skipped — no phrases)")
                continue
            add_route_to_start(flows_client, flow_name, intent_name, e, all_langs, default_lang)
            created += 1
            print("OK")
        except Exception as e2:
            print(f"FAILED: {e2}")
    print(f"\nDone. Created {created} of {len(entries)} intents.")

    print("\nTraining the Default Start Flow NLU...")
    try:
        call(flows_client.train_flow, request=cx.TrainFlowRequest(name=flow_name)).result(timeout=900)
        print("  trained.")
    except Exception as e:
        print(f"  warn: training failed ({e}); click 'Train' in the console, or it auto-trains.")

    print("\nVerify with: python tools/cx_test.py --agent-name Nova-1")
    print(f"Console: https://dialogflow.cloud.google.com/cx/projects/{args.project}/locations/{location}/agents/{agent}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
