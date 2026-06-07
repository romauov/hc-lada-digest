import logging
import os

import numpy as np
import torch
import torch.nn.functional as F
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import AutoModel, AutoTokenizer

logger = logging.getLogger(__name__)

app = FastAPI(title="ML Service", version="1.0")

_model = None
_tokenizer = None
_device = None
_relevance_threshold = float(os.environ.get("RELEVANCE_THRESHOLD", "0.65"))


class DedupRequest(BaseModel):
    titles: list[str]
    threshold: float = 0.92


class DedupResponse(BaseModel):
    indices_to_keep: list[int]


class RelevanceRequest(BaseModel):
    title: str
    anchor_phrases: list[str]


class RelevanceResponse(BaseModel):
    score: float
    is_relevant: bool


class HealthResponse(BaseModel):
    status: str
    model: str


@app.on_event("startup")
def load_model():
    global _model, _tokenizer, _device
    model_name = os.environ.get("EMBED_MODEL", "cointegrated/rubert-tiny2")
    _device = torch.device("cpu")
    logger.info("Loading model: %s", model_name)
    _tokenizer = AutoTokenizer.from_pretrained(model_name)
    _model = AutoModel.from_pretrained(model_name).to(_device)
    _model.eval()
    logger.info("Model loaded: %s", model_name)


def _mean_pool(output: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    mask_expanded = mask.unsqueeze(-1).expand(output.size()).float()
    return (output * mask_expanded).sum(1) / mask_expanded.sum(1).clamp(min=1e-9)


def _embed(texts: list[str]) -> torch.Tensor:
    if _model is None or _tokenizer is None:
        raise RuntimeError("Model not loaded")
    encoded = _tokenizer(
        texts, padding=True, truncation=True, max_length=512, return_tensors="pt"
    )
    encoded = {k: v.to(_device) for k, v in encoded.items()}
    with torch.no_grad():
        output = _model(**encoded).last_hidden_state
    embeddings = _mean_pool(output, encoded["attention_mask"])
    embeddings = F.normalize(embeddings, p=2, dim=1)
    return embeddings


def _cosine_similarity(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    return torch.mm(a, b.T)


def _dedup_indices(emb: torch.Tensor, threshold: float) -> list[int]:
    if emb.shape[0] == 0:
        return []
    sim = _cosine_similarity(emb, emb)
    keep = []
    for i in range(emb.shape[0]):
        if not keep:
            keep.append(i)
        else:
            if torch.all(sim[i, keep] < threshold):
                keep.append(i)
    return keep


@app.post("/dedup", response_model=DedupResponse)
def dedup(req: DedupRequest):
    if not req.titles:
        return DedupResponse(indices_to_keep=[])
    emb = _embed(req.titles)
    indices = _dedup_indices(emb, req.threshold)
    return DedupResponse(indices_to_keep=indices)


@app.post("/relevance", response_model=RelevanceResponse)
def relevance(req: RelevanceRequest):
    all_texts = [req.title] + req.anchor_phrases
    emb = _embed(all_texts)
    title_emb = emb[0:1]
    anchor_embs = emb[1:]
    if anchor_embs.shape[0] == 0:
        score = 0.0
    else:
        sims = _cosine_similarity(title_emb, anchor_embs)
        score = float(sims.max().item())
    return RelevanceResponse(
        score=score,
        is_relevant=score >= _relevance_threshold,
    )


@app.get("/health", response_model=HealthResponse)
def health():
    if _model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return HealthResponse(
        status="ok",
        model=os.environ.get("EMBED_MODEL", "cointegrated/rubert-tiny2"),
    )
