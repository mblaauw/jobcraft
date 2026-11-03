"""Download the pinned model2vec model once, at image build time, and store it as float16.

    python docker/fetch_model.py /opt/models/potion-multilingual-128M

The app never downloads anything at runtime: it only reads this folder.
"""

from __future__ import annotations

import sys
import tempfile

from huggingface_hub import snapshot_download
from model2vec import StaticModel

REPO = "minishlab/potion-multilingual-128M"  # MIT licence, Dutch included
REVISION = "73908c3438cf03b6a01bcb9611d62b23d0726f08"


def main(target: str) -> None:
    with tempfile.TemporaryDirectory() as folder:
        source = snapshot_download(REPO, revision=REVISION, local_dir=folder)
        model = StaticModel.from_pretrained(source, quantize_to="float16", force_download=False)
        model.save_pretrained(target)


if __name__ == "__main__":
    main(sys.argv[1])
