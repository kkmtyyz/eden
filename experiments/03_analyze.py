import numpy as np
import matplotlib.pyplot as plt

d = np.load("embeddings.npz")
vecs, labels = d["vecs"], d["labels"]
vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)  # 正規化
n = len(vecs)

# cosine distance matrix
dist = 1.0 - vecs @ vecs.T
same = labels[:, None] == labels[None, :]
triu = np.triu(np.ones((n, n), dtype=bool), k=1)

intra = dist[same & triu]  # 同一物体・別アングル間
inter = dist[~same & triu]  # 別物体間

print(f"intra(同一物体): mean={intra.mean():.4f}  max={intra.max():.4f}")
print(f"inter(別物体)  : mean={inter.mean():.4f}  min={inter.min():.4f}")
print(f"分離マージン: inter.min - intra.max = {inter.min() - intra.max():+.4f}")
print("  (正なら完全分離=勝ち確。負なら重なりの程度を下のヒストグラムで確認)\n")

# --- 懸念2: leave-one-out 1-NN 分類精度 ---
dist_self = dist + np.eye(n) * 10  # 自分自身を除外
nn_idx = dist_self.argmin(axis=1)
acc = (labels[nn_idx] == labels).mean()
print(f"1-NN分類精度 (leave-one-out): {acc:.1%}")
miss = np.where(labels[nn_idx] != labels)[0]
for i in miss:
    print(
        f"  誤分類: {d['files'][i]} -> {labels[nn_idx[i]]} (dist={dist_self[i, nn_idx[i]]:.4f})"
    )

# --- 懸念3: 閾値スイープ ---
# 「最近傍距離 <= θ なら登録済みと判定」とした時のTPR/FPR相当
print("\n閾値スイープ (θ: これ以下なら『登録済み』と判定):")
print(f"{'θ':>6} {'同一物体を拾える率':>10} {'別物体を誤って拾う率':>12}")
for th in np.arange(0.05, 0.65, 0.05):
    tpr = (intra <= th).mean()
    fpr = (inter <= th).mean()
    print(f"{th:6.2f} {tpr:14.1%} {fpr:16.1%}")

# EER近傍の推奨閾値
ths = np.linspace(0, 1, 500)
tprs = np.array([(intra <= t).mean() for t in ths])
fprs = np.array([(inter <= t).mean() for t in ths])
eer_i = np.abs((1 - tprs) - fprs).argmin()
print(
    f"\n推奨閾値(EER付近): θ={ths[eer_i]:.3f} (取りこぼし{1-tprs[eer_i]:.1%} / 誤ヒット{fprs[eer_i]:.1%})"
)

# ヒストグラム
plt.figure(figsize=(9, 5))
plt.hist(intra, bins=40, alpha=0.6, label="intra (same object)", density=True)
plt.hist(inter, bins=40, alpha=0.6, label="inter (different objects)", density=True)
plt.axvline(ths[eer_i], color="k", ls="--", label=f"threshold={ths[eer_i]:.3f}")
plt.xlabel("cosine distance")
plt.legend()
plt.title("Distance distributions")
plt.savefig("distance_hist.png", dpi=120)
print("=> distance_hist.png")
