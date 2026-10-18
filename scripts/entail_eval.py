"""Measure the model entailment check (memfilter.entailed) with a local GGUF model: python scripts/entail_eval.py
model.gguf  (needs llama-cpp-python). dev = grounding*.jsonl (prompt tuned on these), holdout = entail_holdout.jsonl
(frozen before the check was written)."""
import asyncio, json, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "gateway" / "src"))
from llama_cpp import Llama
from hub_gateway.memfilter import entailed, grounded
M = Llama(model_path=sys.argv[1],
          n_ctx=4096, n_threads=8, verbose=False)
class L:
    async def json(self, p):
        r = M.create_chat_completion(messages=[{"role": "user", "content": p}], temperature=0,
                                     response_format={"type": "json_object"}, max_tokens=200)
        return json.loads(r["choices"][0]["message"]["content"])
def load(n): return [json.loads(x) for x in open(__import__("pathlib").Path(__file__).resolve().parents[1] / "eval" / "persona" / n) if x.strip()]
async def run(name, items):
    st = {k: [0, 0] for k in ("rules", "model", "both")}  # [ok kept, invented dropped]
    n_ok = n_inv = 0
    for it in items:
        e = await entailed(L(), it["facts"], it["source"])
        for f, m in zip(it["facts"], e):
            g = grounded(f, it["source"])[0]
            ok = f["label"] == "ok"; n_ok += ok; n_inv += not ok
            for k, keep in (("rules", g), ("model", m), ("both", g and m)):
                st[k][0 if ok else 1] += (keep if ok else not keep)
    print(name, {k: {"supported_kept": round(a / n_ok, 2), "invented_dropped": round(b / n_inv, 2)} for k, (a, b) in st.items()}, n_ok, n_inv)
async def main():
    await run("dev", load("grounding.jsonl") + load("grounding_holdout.jsonl"))
    await run("holdout", load("entail_holdout.jsonl"))
asyncio.run(main())
