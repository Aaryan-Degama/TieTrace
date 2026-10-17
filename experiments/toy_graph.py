"""Checkpoint 1: test the FalkorDB behaviours TieTrace relies on, on a 10-node toy graph.

Run: uv run python experiments/toy_graph.py
"""
import os

from falkordb import FalkorDB

db = FalkorDB(port=int(os.environ.get("FALKORDB_PORT", 6379)),
              password=os.environ.get("FALKORDB_PASSWORD") or None)  # same env as loader.connect()
g = db.select_graph("toy")
try:
    g.delete()
except Exception:
    pass

# People: Ana (Tier A, funded), Ben and Cy co-author with Ana, Dee co-authors with Ben, Eve is alone.
# Orgs: Coke Foundation -[:PARENT_OF]-> ... Coke Co is the parent.
g.query("""
CREATE
  (ana:Person {id:'ana'}), (ben:Person {id:'ben'}), (cy:Person {id:'cy'}),
  (dee:Person {id:'dee'}), (eve:Person {id:'eve'}),
  (w1:Work {doi:'10/w1', n_authors:2}), (w2:Work {doi:'10/w2', n_authors:3}),
  (co:Org {name:'Coke Co', target:true}), (fd:Org {name:'Coke Foundation', target:true}),
  (f:Funder {name:'Coke Foundation funder', target:true}),
  (co)-[:PARENT_OF]->(fd),
  (ana)-[:AUTHORED]->(w1), (w1)-[:FUNDED_BY]->(f),
  (ana)-[:AFFILIATED {year:2014, doi:'10/w1'}]->(fd),
  (ana)-[:COAUTH {w:1.0}]->(ben), (ben)-[:COAUTH {w:1.0}]->(ana),
  (ana)-[:COAUTH {w:0.5}]->(cy),  (cy)-[:COAUTH {w:0.5}]->(ana),
  (ben)-[:COAUTH {w:0.5}]->(dee), (dee)-[:COAUTH {w:0.5}]->(ben)
""")


def run(label, q, params=None, ro=False):
    try:
        res = (g.ro_query if ro else g.query)(q, params or {})
        print(f"[ok]   {label}: {res.result_set}")
    except Exception as e:
        print(f"[err]  {label}: {e}")


# 1. COAUTH is stored both ways (3 pairs = 6 edges). Undirected counts are inconsistent in FalkorDB
#    (anonymous endpoints count each edge once, labelled endpoints twice), so always match COAUTH with ->.
run("directed COAUTH edges", "MATCH ()-[r:COAUTH]->() RETURN count(r)")
run("undirected COAUTH matches", "MATCH ()-[r:COAUTH]-() RETURN count(r)")
run("dee's neighbours (directed)", "MATCH (:Person {id:'dee'})-[:COAUTH]->(p) RETURN collect(p.id)")

# 2. Bounded variable-length path over the corporate family.
run("affiliation inside Coke family",
    "MATCH (p:Person)-[:AFFILIATED]->(:Org)<-[:PARENT_OF*0..3]-(top:Org {name:'Coke Co'}) RETURN p.id")

# 3. Bounded path existence: who reaches a Tier A person within k hops?
run("within 2 hops of ana",
    "MATCH (p:Person)-[:COAUTH*1..2]->(:Person {id:'ana'}) WHERE p.id <> 'ana' RETURN DISTINCT p.id ORDER BY p.id")

# 4. Native BFS from dee, depth 2, over COAUTH.
run("algo.BFS from dee depth 2",
    "MATCH (s:Person {id:'dee'}) CALL algo.BFS(s, 2, 'COAUTH') YIELD nodes RETURN [n IN nodes | n.id]")
run("algo.BFS from eve depth 3",
    "MATCH (s:Person {id:'eve'}) CALL algo.BFS(s, 3, 'COAUTH') YIELD nodes RETURN [n IN nodes | n.id]")

# 5. Max flow from dee to ana with w as capacity.
run("algo.maxFlow dee -> ana",
    """MATCH (s:Person {id:'dee'}), (t:Person {id:'ana'})
       CALL algo.maxFlow({sourceNodes:[s], targetNodes:[t], capacityProperty:'w', relationshipTypes:['COAUTH']})
       YIELD maxFlow RETURN maxFlow""")

# 6. Read-only query must refuse a write.
run("RO_QUERY write attempt", "CREATE (:Person {id:'mallory'})", ro=True)
run("mallory exists?", "MATCH (p:Person {id:'mallory'}) RETURN count(p)")

g.delete()
