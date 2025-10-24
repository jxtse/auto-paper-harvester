from pathlib import Path
import matplotlib.pyplot as plt

data = {
    "Crossref": (145, 442),
    "Elsevier": (446, 954),
    "Springer": (86, 340),
    "Wiley": (427, 941),
}

publishers = list(data)
success = [data[p][0] for p in publishers]
total = [data[p][1] for p in publishers]
fail = [tot - suc for suc, tot in zip(success, total)]

fig, ax = plt.subplots(figsize=(7.5, 4.5))
indices = range(len(publishers))
ax.bar(indices, success, label="Success", color="#2ca02c")
ax.bar(indices, fail, bottom=success, label="Failure", color="#d62728")
ax.set_xticks(list(indices))
ax.set_xticklabels(publishers, fontsize=11)
ax.set_ylabel('Articles', fontsize=11)
ax.set_title('Publisher Download Outcomes (Latest Run)', fontsize=14)

for idx, (s, t) in enumerate(zip(success, total)):
    ax.text(idx, t + 15, f"{s}/{t}\n({s / t * 100:.1f}%)", ha='center', va='bottom', fontsize=10)

ax.set_ylim(0, max(total) * 1.2)
ax.legend(frameon=False, fontsize=10)
fig.tight_layout()
Path('artifacts').mkdir(exist_ok=True)
fig.savefig('artifacts/publisher_download_summary_latest.png', dpi=200)
plt.close(fig)
