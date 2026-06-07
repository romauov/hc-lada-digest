"""
Юнит-тесты ml_service/main.py без запуска сервера.

Тестируем чистые функции _mean_pool, _cosine_similarity, _dedup_indices
с замоканным torch (не грузим модель).
"""
import torch


def _mean_pool(output: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    mask_expanded = mask.unsqueeze(-1).expand(output.size()).float()
    return (output * mask_expanded).sum(1) / mask_expanded.sum(1).clamp(min=1e-9)


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


class TestMeanPool:
    def test_mean_pool_basic(self):
        output = torch.tensor([[[1.0, 2.0], [3.0, 4.0], [0.0, 0.0]]])
        mask = torch.tensor([[1, 1, 0]])
        result = _mean_pool(output, mask)
        expected = torch.tensor([[2.0, 3.0]])
        assert torch.allclose(result, expected, atol=1e-6)

    def test_mean_pool_single_token(self):
        output = torch.tensor([[[5.0, 6.0]]])
        mask = torch.tensor([[1]])
        result = _mean_pool(output, mask)
        assert torch.allclose(result, torch.tensor([[5.0, 6.0]]), atol=1e-6)

    def test_mean_pool_all_masked(self):
        output = torch.tensor([[[1.0, 2.0]]])
        mask = torch.tensor([[0]])
        result = _mean_pool(output, mask)
        assert not torch.any(torch.isnan(result))


class TestCosineSimilarity:
    def test_identical_vectors(self):
        a = torch.tensor([[1.0, 0.0]])
        b = torch.tensor([[1.0, 0.0]])
        sim = _cosine_similarity(a, b)
        assert torch.allclose(sim, torch.tensor([[1.0]]), atol=1e-6)

    def test_orthogonal_vectors(self):
        a = torch.tensor([[1.0, 0.0]])
        b = torch.tensor([[0.0, 1.0]])
        sim = _cosine_similarity(a, b)
        assert torch.allclose(sim, torch.tensor([[0.0]]), atol=1e-6)

    def test_opposite_vectors(self):
        a = torch.tensor([[1.0, 0.0]])
        b = torch.tensor([[-1.0, 0.0]])
        sim = _cosine_similarity(a, b)
        assert torch.allclose(sim, torch.tensor([[-1.0]]), atol=1e-6)


class TestDedupIndices:
    def test_identical_titles(self):
        emb = torch.tensor([[1.0, 0.0], [1.0, 0.0]])
        indices = _dedup_indices(emb, 0.9)
        assert indices == [0]

    def test_different_titles(self):
        emb = torch.tensor([[1.0, 0.0], [0.0, 1.0]])
        indices = _dedup_indices(emb, 0.9)
        assert indices == [0, 1]

    def test_empty(self):
        emb = torch.empty(0, 2)
        indices = _dedup_indices(emb, 0.9)
        assert indices == []

    def test_threshold_dedup(self):
        emb = torch.tensor([[1.0, 0.0], [0.95, 0.05], [0.0, 1.0]])
        indices = _dedup_indices(emb, 0.9)
        assert indices == [0, 2]
