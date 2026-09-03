# MolScribe OCSR worker image.
#
# Runs FormuMind's dedicated Celery worker for the `molscribe` queue
# (structure-image -> SMILES recognition). The base backend image carries all
# app code + dependencies; this layer only:
#   1) downgrades torch to the MolScribe-verified combination
#      (2.3.0+cpu / torchvision 0.18.0+cpu — the backend image's torch 2.13 /
#      torchvision 0.28 is incompatible with MolScribe's OpenNMT-py stack),
#   2) installs MolScribe + its pinned deps (--no-deps: molscribe declares an
#      outdated torch<2.0 constraint; torch 2.3.0+cpu is proven by the host
#      venv — 20/20 structures, ~3-8s each on this CPU).
#
# The swin_base_char_aux_1m.pth weights (1.1 GB) are NOT baked into the image:
# they are bind-mounted at runtime from ./deploy/molscribe/hf-cache (see
# docker-compose.yml) so weight updates don't require a rebuild.
#
# Only the molscribe worker uses this image, so the downgrade never touches
# the main backend/worker images.
FROM formumind-backend:latest

# 1) Downgrade torch/torchvision to the MolScribe-verified pair.
RUN pip install --index-url https://download.pytorch.org/whl/cpu \
        torch==2.3.0+cpu torchvision==0.18.0+cpu

# 2) MolScribe + pinned deps. --no-deps skips molscribe's own resolver, which
#    hard-fails on torch<2.0 against our 2.3.0+cpu install.
RUN pip install --no-deps \
        molscribe==1.1.1 \
        timm==0.4.12 \
        SmilesPE==0.0.3 \
        OpenNMT-py==2.2.0 \
        albumentations==1.1.0 \
        qudida==0.0.4 \
        scikit-image==0.26.0 \
        lazy-loader==0.5 \
        tensorboard==2.21.0 \
        pyonmttok==1.38.1 \
        sentencepiece==0.2.2 \
        torchtext==0.5.0 \
    # opencv-python (GUI) and -headless both install into site-packages/cv2;
    # mixing them leaves a broken cv2 (missing BORDER_* constants) that makes
    # molscribe's albumentations import fail. Remove BOTH then install the
    # headless build cleanly, and wipe any leftover cv2 directory.
    && pip uninstall -y opencv-python opencv-python-headless \
    && rm -rf /usr/local/lib/python3.11/site-packages/cv2 \
    && pip install opencv-python-headless==4.11.0.86 \
    && python -c "import cv2; assert hasattr(cv2, 'BORDER_REFLECT_101'), 'cv2 broken'; print('cv2', cv2.__version__, 'OK')"

# 3) Offline weights are bind-mounted at runtime; tell huggingface_hub to
#    never phone home (it must use the mounted cache).
ENV HF_HUB_OFFLINE=1
