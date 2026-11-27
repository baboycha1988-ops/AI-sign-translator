"""CPU baseline: pooled landmark sequence -> standardise -> PCA -> logistic regression,
plus a distance-to-prototype score used by the open-set decision. Trains in seconds without a GPU.
It is the reference every neural model (GRU / Transformer) must beat on the SAME signer-disjoint test set."""
import numpy as np
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from .features import BASE_DIM


def pool(seq, steps=8):
    """Resample a variable-length sequence to `steps` time points (base features only, flattened)."""
    a = np.asarray([v[:BASE_DIM] for v in seq], dtype=float)
    if len(a) == 0:
        raise ValueError("empty sequence")
    if len(a) == 1:
        return np.tile(a[0], steps)
    xs = np.linspace(0, len(a) - 1, steps)
    i0 = np.floor(xs).astype(int)
    i1 = np.minimum(i0 + 1, len(a) - 1)
    w = (xs - i0)[:, None]
    return ((1 - w) * a[i0] + w * a[i1]).ravel()


class Baseline:
    def __init__(self, steps=8, n_components=32, C=1.0):
        self.steps, self.n_components, self.C = steps, n_components, C

    def _embed(self, seqs):
        X = np.stack([pool(s, self.steps) for s in seqs])
        return self.pca.transform(self.scaler.transform(X))

    def fit(self, seqs, labels):
        labels = np.asarray(labels)
        X = np.stack([pool(s, self.steps) for s in seqs])
        self.scaler = StandardScaler().fit(X)
        Z = self.scaler.transform(X)
        self.pca = PCA(n_components=min(self.n_components, Z.shape[0] - 1, Z.shape[1]), random_state=0).fit(Z)
        P = self.pca.transform(Z)
        self.clf = LogisticRegression(C=self.C, max_iter=3000).fit(P, labels)
        self.classes = [str(c) for c in self.clf.classes_]
        self.protos = {c: P[labels == c].mean(axis=0) for c in self.clf.classes_}
        self.radius = {c: float(np.median(np.linalg.norm(P[labels == c] - self.protos[c], axis=1))) + 1e-9
                       for c in self.clf.classes_}
        return self

    def predict(self, seq):
        """-> (probs: {label: p}, distance: nearest-prototype distance in units of that class's typical radius)."""
        P = self._embed([seq])
        probs = self.clf.predict_proba(P)[0]
        dist = min(float(np.linalg.norm(P[0] - self.protos[c])) / self.radius[c] for c in self.clf.classes_)
        return {str(c): float(p) for c, p in zip(self.clf.classes_, probs)}, dist
