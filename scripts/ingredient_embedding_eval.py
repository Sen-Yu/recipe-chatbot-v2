"""재료명 임베딩 기준선: bge-m3 vs KURE-v1.

평가 쌍은 data/ingredients/graph.tsv에서 만든다.
- same (정답): same 관계, 그리고 같은 대표 이름으로 모이는 이름끼리
- 음성(같으면 안 되는 쌍), 종류별로 따로 본다:
  sibling  : 같은 상위 재료의 서로 다른 하위 (대파/쪽파, 진간장/국간장)
  kind     : 하위와 상위 (표고버섯/버섯)
  form     : 손질 형태와 원재료 (다진마늘/마늘)
  contains : 가공품과 원료 (부침가루/밀가루)
  different: 사전에 '다르다'고 적은 쌍
지표
- 쌍 분류: 평균 유사도, AUC(same vs 음성), 최적 임계값에서 정밀도/재현율
- 정규화 검색: same 이름마다 어휘집(상위 300 + 사전의 모든 이름)에서 가장 가까운 이름이
  같은 대표 이름이면 정답 (top-1, top-5)
사용 (sentence-transformers가 있는 환경, 예: rag 의존성 그룹):
  python scripts/ingredient_embedding_eval.py data/ingredients/graph.tsv \\
      <names.json> <out.json> BAAI/bge-m3 nlpai-lab/KURE-v1
<names.json>은 [[재료명, 빈도], ...] 형식이며 상위 300개를 어휘집에 넣는다.
결과는 docs/ingredient-graph.md의 "임베딩 기준선"에 적는다.
"""

import itertools
import json
import sys
import time
from collections import defaultdict

import numpy as np
from sentence_transformers import SentenceTransformer

graph_path, names_path, out_path, *models = sys.argv[1:]

edges = []
for line in open(graph_path, encoding="utf-8"):
    if not line.strip() or line.startswith("#"):
        continue
    c = line.rstrip("\n").split("\t")
    edges.append((c[0], c[1], c[2], c[3] == "ok"))

same = {n: t for n, r, t, _ in edges if r == "same"}


def canon(x):
    seen = set()
    while x in same and x not in seen:
        seen.add(x)
        x = same[x]
    return x


# 같은 대표 이름 묶음
groups = defaultdict(set)
for n, _r, t, _ in edges:
    for x in (n, t):
        groups[canon(x)].add(x)

pairs = {"same": set(), "same_check": set()}
for n, r, t, ok in edges:
    if r == "same":
        pairs["same" if ok else "same_check"].add(frozenset((n, t)))
for g in groups.values():
    for a, b in itertools.combinations(sorted(g), 2):
        p = frozenset((a, b))
        if p not in pairs["same_check"]:
            pairs["same"].add(p)
# check 표시된 same 쌍이 묶음을 통해 다시 들어오지 않게
pairs["same"] -= pairs["same_check"]

children = defaultdict(set)
for k in ("sibling", "kind", "form", "contains", "different"):
    pairs[k] = set()
for n, r, t, _ok in edges:
    if r == "kind":
        children[canon(t)].add(canon(n))
    if r in ("kind", "form", "contains", "different"):
        pairs[r].add(frozenset((n, t)))
for kids in children.values():
    for a, b in itertools.combinations(sorted(kids), 2):
        pairs["sibling"].add(frozenset((a, b)))
for k in ("sibling", "kind", "form", "contains", "different"):
    pairs[k] = {p for p in pairs[k] if len({canon(x) for x in p}) == 2}

top = [n for n, _ in json.load(open(names_path, encoding="utf-8"))[:300]]
vocab = sorted(
    set(top)
    | {x for g in groups.values() for x in g}
    | {x for p in itertools.chain(*pairs.values()) for x in p}
)
queries = sorted({x for g in groups.values() if len(g) > 1 for x in g if x != canon(x)})


def auc(pos, neg):
    pos, neg = np.asarray(pos), np.asarray(neg)
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


result = {
    "counts": {k: len(v) for k, v in pairs.items()},
    "vocab": len(vocab),
    "queries": len(queries),
    "models": {},
}
NEG = ("sibling", "kind", "form", "contains", "different")
for m in models:
    t0 = time.time()
    model = SentenceTransformer(m, device="mps")
    emb = model.encode(vocab, normalize_embeddings=True, batch_size=64)
    idx = {v: i for i, v in enumerate(vocab)}

    def sim(p, emb=emb, idx=idx):
        a, b = sorted(p)
        return float(emb[idx[a]] @ emb[idx[b]])

    scores = {k: [sim(p) for p in v] for k, v in pairs.items()}
    r = {"mean": {k: round(float(np.mean(v)), 3) for k, v in scores.items() if v}}
    allneg = [s for k in NEG for s in scores[k]]
    r["auc"] = {k: round(auc(scores["same"], scores[k]), 3) for k in NEG}
    r["auc"]["all"] = round(auc(scores["same"], allneg), 3)
    # 최적 F1 임계값
    best = (0, 0, 0, 0)
    for th in np.arange(0.3, 0.99, 0.01):
        tp = sum(s >= th for s in scores["same"])
        fp = sum(s >= th for s in allneg)
        prec = tp / (tp + fp) if tp + fp else 0
        rec = tp / len(scores["same"])
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
        if f1 > best[0]:
            best = (f1, float(th), prec, rec)
    r["best_threshold"] = {
        "f1": round(best[0], 3),
        "threshold": round(best[1], 2),
        "precision": round(best[2], 3),
        "recall": round(best[3], 3),
    }
    # 정규화 검색
    S = emb @ emb.T
    hit1 = hit5 = 0
    misses = []
    for q in queries:
        i = idx[q]
        order = [j for j in np.argsort(-S[i]) if j != i]
        cands = [vocab[j] for j in order[:5]]
        ok = [canon(c) == canon(q) for c in cands]
        hit1 += ok[0]
        hit5 += any(ok)
        if not ok[0]:
            misses.append((q, canon(q), cands[0], round(float(S[i, order[0]]), 3)))
    r["normalize"] = {
        "top1": round(hit1 / len(queries), 3),
        "top5": round(hit5 / len(queries), 3),
        "misses": misses,
    }
    # 가장 헷갈린 음성 쌍
    worst = sorted(((sim(p), k, "/".join(sorted(p))) for k in NEG for p in pairs[k]), reverse=True)[:12]
    r["top_negatives"] = [(round(s, 3), k, p) for s, k, p in worst]
    lowpos = sorted((sim(p), "/".join(sorted(p))) for p in pairs["same"])[:10]
    r["low_positives"] = [(round(s, 3), p) for s, p in lowpos]
    r["seconds"] = round(time.time() - t0, 1)
    result["models"][m] = r
    print(
        m,
        json.dumps({k: r[k] for k in ("mean", "auc", "best_threshold")}, ensure_ascii=False),
        r["normalize"]["top1"],
        r["normalize"]["top5"],
        flush=True,
    )

json.dump(result, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("counts", result["counts"], "vocab", result["vocab"], "queries", result["queries"])
