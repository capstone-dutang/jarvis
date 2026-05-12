"""Verification of MCP/REST tools against personal workspace."""
import json, urllib.request, sys

WS = "2d92735f-c858-4398-b4dd-d28423208e17"  # personal
URL = "http://127.0.0.1:8004/api/v1/memory"

def post(path, payload):
    req = urllib.request.Request(
        f"{URL}{path}", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"_error": str(e)}

print("=== recall_memory ===")
r = post("/recall", {"workspace_id": WS, "query": "SecondBrain", "limit": 3})
if "_error" in r:
    print(f"  FAIL: {r['_error']}")
else:
    print(f"  results: {len(r.get('results', []))}")
    for f in r.get("results", [])[:2]:
        print(f"    {f['entity']}.{f['predicate']} = {f['object_value'][:60]}")

print("\n=== search_passages ===")
r = post("/search-passages", {"workspace_id": WS, "query": "예창패 SecondBrain", "limit": 3})
if "_error" in r:
    print(f"  FAIL: {r['_error']}")
else:
    print(f"  results: {len(r.get('results', []))}")
    for p in r.get("results", [])[:2]:
        print(f"    sim={p['similarity']:.3f} {p['content'][:80]}")

print("\n=== explore_topic ===")
r = post("/explore", {"workspace_id": WS, "query": "JARVIS", "limit": 5})
if "_error" in r:
    print(f"  FAIL: {r['_error']}")
else:
    print(f"  results keys: {list(r.keys())}")
    if "results" in r:
        print(f"  results count: {len(r['results'])}")

print("\n=== follow_relation ===")
r = post("/follow-relation", {"workspace_id": WS, "entity": "JARVIS", "direction": "both", "limit": 5})
if "_error" in r:
    print(f"  FAIL: {r['_error']}")
else:
    print(f"  anchor: {r.get('anchor_entity_name', '?')}, neighbors: {r.get('total_neighbors', 0)}")
    for n in r.get("neighbors", [])[:3]:
        print(f"    {n['relation_type']}/{n['direction']} → {n['entity_name']} (facts={n['fact_count']})")

print("\n=== get_episode_excerpt ===")
# Get any episode_id from personal workspace
import subprocess
ep = subprocess.run(
    ["docker", "exec", "jarvis-db-1", "psql", "-U", "jarvis", "-d", "jarvis", "-tA", "-c",
     f"SELECT id FROM episodes WHERE workspace_id='{WS}' LIMIT 1"],
    capture_output=True, text=True
).stdout.strip()
if ep:
    r = post("/episode-excerpt", {"workspace_id": WS, "episode_id": ep, "query": "SecondBrain", "mode": "relevant", "max_chars": 500})
    if "_error" in r:
        print(f"  FAIL: {r['_error']}")
    else:
        ex = r.get("excerpt", "")
        print(f"  mode={r.get('mode')} length={len(ex)} matched={r.get('matched_keywords', [])}")
        print(f"  excerpt[:200]: {ex[:200]}")
else:
    print("  SKIP: no episode found")

print("\n=== initialize_memory ===")
r = post("/initialize", {"workspace_id": WS})
if "_error" in r:
    print(f"  FAIL: {r['_error']}")
else:
    print(f"  keys: {list(r.keys())[:10]}")
