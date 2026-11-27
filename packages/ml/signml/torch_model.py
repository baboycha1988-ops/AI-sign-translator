"""Neural candidate: Conv1D + GRU over fixed-length landmark sequences (PyTorch, CPU-friendly, ~0.3M params).
Must be compared against signml.baseline on the SAME signer-disjoint split before any model is chosen.
STATUS: written without PyTorch available in the authoring environment -> NOT yet executed."""
import numpy as np

from .features import FEATURE_DIM

T_FIXED = 48


def resample(seq, t=T_FIXED):
    a = np.asarray(seq, dtype=np.float32)
    xs = np.linspace(0, len(a) - 1, t)
    i0 = np.floor(xs).astype(int)
    i1 = np.minimum(i0 + 1, len(a) - 1)
    w = (xs - i0)[:, None].astype(np.float32)
    return (1 - w) * a[i0] + w * a[i1]


def augment(x, rng):
    """x: (T, F) float32. Light, label-preserving augmentation: small noise + random time shift."""
    x = x + rng.normal(0, 0.01, x.shape).astype(np.float32)
    shift = int(rng.integers(-3, 4))
    return np.roll(x, shift, axis=0)


def build_model(n_classes, hidden=128, emb=64):
    import torch.nn as nn

    class SignNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.norm = nn.LayerNorm(FEATURE_DIM)
            self.proj = nn.Linear(FEATURE_DIM, hidden)
            self.conv = nn.Conv1d(hidden, hidden, kernel_size=5, padding=2)
            self.gru = nn.GRU(hidden, hidden, batch_first=True, bidirectional=True)
            self.emb = nn.Linear(2 * hidden, emb)
            self.head = nn.Linear(emb, n_classes)
            self.drop = nn.Dropout(0.3)

        def forward(self, x):                       # x: (B, T, F)
            h = self.proj(self.norm(x)).relu()
            h = self.conv(h.transpose(1, 2)).relu().transpose(1, 2)
            h, _ = self.gru(h)
            e = self.emb(self.drop(h.mean(dim=1)))
            return self.head(self.drop(e.relu())), e

    return SignNet()


def train(model, train_xy, val_xy, *, epochs=60, lr=2e-3, batch=32, seed=0, patience=10):
    """train_xy / val_xy: (np.ndarray[N,T,F], np.ndarray[N] int labels). Early-stops on validation macro-F1."""
    import torch
    from .metrics import macro, per_class_prf

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
    loss_fn = torch.nn.CrossEntropyLoss(label_smoothing=0.1)
    Xtr, ytr = train_xy
    Xva, yva = val_xy
    best, best_state, bad = -1.0, None, 0
    for ep in range(epochs):
        model.train()
        idx = rng.permutation(len(Xtr))
        for i in range(0, len(idx), batch):
            b = idx[i:i + batch]
            xb = torch.from_numpy(np.stack([augment(Xtr[j], rng) for j in b]))
            logits, _ = model(xb)
            loss = loss_fn(logits, torch.from_numpy(ytr[b]).long())
            opt.zero_grad(); loss.backward(); opt.step()
        sched.step()
        model.eval()
        with torch.no_grad():
            pred = model(torch.from_numpy(Xva))[0].argmax(1).numpy()
        f1 = macro(per_class_prf(list(yva), list(pred), sorted(set(yva))), "f1")
        if f1 > best:
            best, bad = f1, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(best_state)
    return best


def export_onnx(model, path):
    """ONNX with a dynamic batch axis; runs in the browser (onnxruntime-web) and on the server."""
    import torch
    model.eval()
    dummy = torch.zeros(1, T_FIXED, FEATURE_DIM)
    torch.onnx.export(model, dummy, path, input_names=["landmarks"], output_names=["logits", "embedding"],
                      dynamic_axes={"landmarks": {0: "batch"}, "logits": {0: "batch"}, "embedding": {0: "batch"}},
                      opset_version=17)
