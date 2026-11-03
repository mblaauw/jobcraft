"""Runs the embedding model in its own short-lived process.

The multilingual model needs about 1.8 GB while loading (mostly its tokenizer). In a
child process all of that is returned the moment the work is done, so the worker keeps
its usual footprint. Reads a JSON list of texts on stdin, writes a float32 .npy to stdout.
Deliberately imports nothing from the app.
"""

from __future__ import annotations

import io
import json
import sys

import numpy as np


def main(model_path: str) -> None:
    from model2vec import StaticModel

    texts = json.load(sys.stdin)
    model = StaticModel.from_pretrained(model_path, force_download=False)
    output = io.BytesIO()  # a pipe cannot seek, which np.save needs
    np.save(output, np.asarray(model.encode(texts), dtype=np.float32))
    sys.stdout.buffer.write(output.getvalue())


if __name__ == "__main__":
    main(sys.argv[1])
