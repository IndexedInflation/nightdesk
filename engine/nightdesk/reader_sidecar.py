# Optional local headline reader, configured by the user.
# No model is shipped and no folder from an earlier installation is assumed.
# Only safetensors and local tokenizer files are loaded; remote code is disabled.
#
# Protocol: one JSON object per line.
#   in :  {"id": 7, "headlines": ["...", "..."]}
#   out:  {"id": 7, "results": [{"p": [good, bad, nothing]}, ...]}
# It loads the model on the first request and quits after 45 idle seconds, handing back about 2.5 GB
# of memory. The engine asks every 15 minutes, so the model is awake for under a minute each time.
#
# Kept compatible with Python 3.9.
import json
import queue
import threading
import sys

IDLE_SECONDS = 45


def validate_labels(mapping):
    labels = {int(k): str(v).lower() for k, v in mapping.items()}
    if len(labels) != 3 or [labels.get(i) for i in range(3)] != ["good", "bad", "nothing"]:
        raise ValueError("Reader must declare id2label 0=good, 1=bad, 2=nothing. Generic sentiment models are not compatible.")


def memory(dev):
    """How much memory this program is using, in megabytes (reported with every answer)."""
    out = {"device": dev}
    try:
        import resource
        scale = 1e6 if sys.platform == "darwin" else 1e3
        out["peak_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / scale, 1)
    except ImportError:
        pass
    try:
        import torch
        if dev == "mps":
            out["gpu_mb"] = round(torch.mps.driver_allocated_memory() / 1e6, 1)
    except Exception:
        pass
    return out


def main(model_dir):
    import time
    model = tok = dev = None
    load_s = None
    print(json.dumps({"ready": True}), flush=True)
    inbox = queue.Queue()
    def read_input():
        for line in sys.stdin:
            inbox.put(line)
        inbox.put("")
    threading.Thread(target=read_input, daemon=True).start()
    while True:
        try:
            line = inbox.get(timeout=IDLE_SECONDS)
        except queue.Empty:
            return                                   # idle: exit and give the memory back
        if not line:
            return                                   # the engine went away
        try:
            req = json.loads(line)
            if model is None:
                t0 = time.time()
                import torch
                from transformers import AutoModelForSequenceClassification, AutoTokenizer
                dev = "mps" if torch.backends.mps.is_available() else "cpu"
                tok = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False)
                model = AutoModelForSequenceClassification.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False, use_safetensors=True).to(dev).eval()
                validate_labels(model.config.id2label)
                load_s = round(time.time() - t0, 2)
            import torch
            t1 = time.time()
            results = []
            heads = [str(h)[:400] for h in req.get("headlines", [])]
            with torch.no_grad():
                for i in range(0, len(heads), 32):
                    x = tok(heads[i:i + 32], return_tensors="pt", padding=True, truncation=True, max_length=64).to(dev)
                    for p in model(**x).logits.softmax(-1).cpu().tolist():
                        results.append({"p": [round(v, 4) for v in p]})
            stats = dict(memory(dev), load_s=load_s, read_ms=round((time.time() - t1) * 1000, 1), headlines=len(heads),
                         params_millions=round(sum(p.numel() for p in model.parameters()) / 1e6, 1))
            print(json.dumps({"id": req.get("id"), "results": results, "stats": stats}), flush=True)
        except Exception as exc:                     # report the problem instead of dying silently
            model = tok = dev = None                 # a failed label validation must never permit later scoring
            print(json.dumps({"id": None, "error": "%s: %s" % (type(exc).__name__, exc)}), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
