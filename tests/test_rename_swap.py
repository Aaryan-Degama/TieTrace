"""P2-4 (Aaryan, Oct 17): what in-flight readers see when RENAME swaps a graph, and GRAPH.COPY as a backup.

Run:  FALKORDB_PORT=6379 uv run python tests/test_rename_swap.py
Scratch keys only: zz-rename-a, zz-rename-b, zz-copy-a (deleted at the start and the end). Never touches
research:* or newsroom:*. The nodes are synthetic test fixtures. No API calls.

FalkorDB does not document what RENAME does to a query that is already running on the key, so this test
pins what FalkorDB 4.22.1 does. The loader swaps exactly like this: `RENAME staging:<slug> research:<slug>`.
"""
import os
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FALKORDB_PORT", "6379")
import loader  # noqa: E402

DB = loader.connect()
A, B, COPY = "zz-rename-a", "zz-rename-b", "zz-copy-a"
N = 1500  # nodes per graph; the reader below pairs every node with every node (2.25M pairs, about 1-2 s)
# Reads the tag before AND after the long part, so a reader that switched graphs halfway would show it.
SLOW = ("MATCH (m:Meta) WITH m.tag AS first MATCH (a:N), (b:N) WHERE a.v <> b.v + 0.5 "
        "WITH first, count(*) AS pairs MATCH (m2:Meta) RETURN first, pairs, m2.tag")


def drop(*keys):
    for k in keys:
        if k in DB.list_graphs():
            DB.select_graph(k).delete()


def make(key, tag, n):
    g = DB.select_graph(key)
    g.query("CREATE INDEX FOR (x:N) ON (x.v)")
    g.query("UNWIND range(1, $n) AS i CREATE (:N {v: i})", {"n": n})
    g.query("CREATE (:Meta {tag: $tag})", {"tag": tag})


def test_rename_swap_with_in_flight_readers():
    drop(A, B)
    try:
        make(A, "old", N)
        make(B, "new", N + 1)  # one more node, so the pair count also says which graph was read
        old_pairs, new_pairs = N * N, (N + 1) * (N + 1)
        t0 = time.time()
        DB.select_graph(A).ro_query(SLOW, timeout=60000)
        alone = time.time() - t0
        print(f"  one slow read alone: {alone:.2f} s")

        results = []

        def reader(i):
            start = time.time()
            try:
                row = loader.connect().select_graph(A).ro_query(SLOW, timeout=60000).result_set[0]
                results.append((i, start, time.time(), row, None))
            except Exception as e:  # recorded, not raised: an error is a finding
                results.append((i, start, time.time(), None, f"{type(e).__name__}: {e}"))

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        time.sleep(min(0.3, alone / 3))  # the readers are running now
        swap_at = time.time()
        DB.connection.rename(B, A)  # what loader.write_graph does
        swapped = time.time()
        for t in threads:
            t.join()

        in_flight = [r for r in results if r[1] < swap_at < r[2]]
        for i, start, end, row, err in sorted(results):
            print(f"  reader {i}: started {start - swap_at:+.2f} s, ended {end - swap_at:+.2f} s from the RENAME; "
                  f"{'error ' + err if err else f'first tag {row[0]}, pairs {row[1]}, last tag {row[2]}'}")
        print(f"  RENAME took {1000 * (swapped - swap_at):.1f} ms")
        assert in_flight, "no reader was running at the RENAME; the test did not test anything"
        assert not [r for r in results if r[4]], "an in-flight reader got an error"
        for _, _, _, row, _ in in_flight:
            # Pinned behaviour of 4.22.1: an in-flight read finishes on the OLD graph, consistently.
            assert list(row) == ["old", old_pairs, "old"], row
        after = DB.select_graph(A).ro_query(SLOW, timeout=60000).result_set[0]
        assert list(after) == ["new", new_pairs, "new"], after  # a reader after the swap sees the new graph
        assert B not in DB.list_graphs() and A in DB.list_graphs()
        assert "Index" in str(DB.select_graph(A).explain("MATCH (x:N) WHERE x.v = 3 RETURN x"))  # index kept
    finally:
        drop(A, B)


def test_graph_copy_makes_a_separate_backup():
    drop(A, COPY)
    try:
        make(A, "orig", 50)
        t0 = time.time()
        DB.connection.execute_command("GRAPH.COPY", A, COPY)
        print(f"  GRAPH.COPY of a 51-node graph: {1000 * (time.time() - t0):.1f} ms")
        c = DB.select_graph(COPY)
        assert c.ro_query("MATCH (x:N) RETURN count(x)").result_set == [[50]]
        assert c.ro_query("MATCH (m:Meta) RETURN m.tag").result_set == [["orig"]]
        assert "Index" in str(c.explain("MATCH (x:N) WHERE x.v = 3 RETURN x")), "index not copied"
        DB.select_graph(A).query("MATCH (m:Meta) SET m.tag = 'changed'")  # the copy is separate
        assert c.ro_query("MATCH (m:Meta) RETURN m.tag").result_set == [["orig"]]
        try:  # copying onto an existing key is refused (the backup is never overwritten silently)
            DB.connection.execute_command("GRAPH.COPY", A, COPY)
        except Exception as e:
            print(f"  GRAPH.COPY onto an existing key: {type(e).__name__}: {e}")
            assert "already exists" in str(e), e
        else:
            raise AssertionError("GRAPH.COPY overwrote an existing key")
        assert c.ro_query("MATCH (m:Meta) RETURN m.tag").result_set == [["orig"]]
    finally:
        drop(A, COPY)


if __name__ == "__main__":
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        t0 = time.time()
        try:
            fn()
            print(f"PASS  {name}  ({time.time() - t0:.1f}s)")
        except Exception as e:
            failed += 1
            line = [f.lineno for f in traceback.extract_tb(e.__traceback__) if f.filename == __file__][-1]
            print(f"FAIL  {name}  (line {line}): {str(e)[:400] or type(e).__name__}")
    left = [k for k in (A, B, COPY) if k in DB.list_graphs()]
    print(f"\n{len(tests) - failed}/{len(tests)} passed; scratch keys left: {left or 'none'}")
    sys.exit(1 if failed or left else 0)
