"""Rough recall check of a fake-batch ledger against the facts and conflicts injected in data/interactions_fake.jsonl.
Usage: .venv/bin/python pilot/coverage_check.py [table_prefix]   (needs the docker Postgres; keyword regexes, not a judge)
An item counts as found when any version of any memory of the user (active, candidate or replaced) matches."""
import json, re, subprocess, sys
P = sys.argv[1] if len(sys.argv) > 1 else "habitantes_fake"
SQL = f"select coalesce(json_agg(row_to_json(t)),'[]') from (select right(user_id,1) u, type, status, content, conflicts_with, version from {P}_memory where type<>'area') t"
rows = json.loads(subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "memhub", "-d", "memhub", "-At", "-c", SQL],
                                 capture_output=True, text=True, check=True).stdout)
FACTS = {
    "1": {"peanut/gluten": r"amendoim|gl[úu]ten", "used bike": r"bici|bike", "grocery budget": r"150", "AI track": r"IA|filière",
          "Dec visit to SP": r"dezembro", "pref bullets/short": r"bullet|curt|list|t[óo]pic", "pref no emoji": r"emoji", "pref address": r"Cami"},
    "2": {"34, single": r"34|solteir", "French B1": r"B1", "20k savings": r"20\.?0?0?0?k?\b.*reserva|reserva.*20|20k", "father visit": r"pai\b.*(visit|vem|outubro)|visit.*pai",
          "pref short/direct": r"curt|objetiv|diret|linhas", "pref address": r"Rafa"},
    "3": {"vegetarian 5y": r"vegetarian", "runs 3x": r"corr", "2200 net": r"2\.?200", "mother Nov": r"m[ãa]e", "Thor 3": r"Thor.*3 anos|3 anos.*Thor",
          "cat Luna": r"Luna", "pref sources": r"fontes", "pref address": r"Ana"},
    "4": {"22 y/o": r"22 anos", "marketing": r"[Mm]arketing", "rock": r"rock", "lactose": r"lactose", "basketball/weights": r"basquet|muscula",
          "family visits": r"fam[ií]lia.*(visit|vir)|visit.*fam[ií]lia|familiares", "Schneider internship": r"Schneider|est[áa]gio", "pref short list": r"curt|lista|itens"},
    "5": {"shellfish": r"frutos do mar", "Alice lactose": r"Alice.*lactose", "husband football": r"marido.*futebol|futebol", "coworking": r"coworking",
          "école maternelle": r"maternelle", "pref numbered steps": r"passo", "pref no emoji": r"emoji"},
}
CONFLICTS = {
    "1": {"vegetarian -> meat": (r"vegetarian", r"carne"), "Crous -> Île Verte": (r"Crous|resid[êe]ncia", r"[ÎI]le Verte"), "Boursorama -> Revolut": (r"Boursorama", r"Revolut")},
    "2": {"vegan -> meat": (r"vegan", r"carne"), "Fontaine -> Meylan": (r"Fontaine", r"Meylan")},
    "3": {"stay in Saint-Martin-d'Hères": (r"Saint-Martin", r"(decid|ficar|permanec)")},
    "4": {"Alpexpo -> Berriat": (r"Alpexpo", r"Berriat")},
}
tot = hit = 0
for u, facts in FACTS.items():
    mine = " || ".join(r["content"] for r in rows if r["u"] == u)
    ok = {k: bool(re.search(rx, mine)) for k, rx in facts.items()}
    tot += len(ok); hit += sum(ok.values())
    print(f"user {u}: {sum(ok.values())}/{len(ok)}  missing: {[k for k, v in ok.items() if not v] or '-'}")
print(f"facts found: {hit}/{tot}")
ctot = chit = 0
for u, cs in CONFLICTS.items():
    mine = [r for r in rows if r["u"] == u]
    for name, (old, new) in cs.items():
        ctot += 1
        touched = any(re.search(old, r["content"]) for r in mine) and any(re.search(new, r["content"]) for r in mine)
        flagged = any(r["version"] > 1 or r["conflicts_with"] for r in mine if re.search(new, r["content"]) or re.search(old, r["content"]))
        chit += touched and flagged
        print(f"  conflict {u} {name}: both sides stored={touched}, update/conflict recorded={flagged}")
print(f"conflicts seen: {chit}/{ctot}")
