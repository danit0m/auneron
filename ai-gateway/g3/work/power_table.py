import sys
sys.path.insert(0, r"C:\Users\Tomaz\AppData\Local\Temp\claude\3f8f7fcbff3437eb\e022271a-4bb1-4a75-85a7-edf63593d450\scratchpad\g3-prep")
from g3eval import stats

print("n=148 pares; poder McNemar exato bilateral alfa 0,05")
for p1, p2 in ((0.01, 0.0), (0.02, 0.0), (0.03, 0.0), (0.05, 0.0), (0.08, 0.0), (0.10, 0.0),
               (0.05, 0.02), (0.10, 0.02), (0.03, 0.03)):
    print(f"  so um acerta {p1:.2f} / so o outro {p2:.2f}: poder {stats.mcnemar_power(148, p1, p2):.3f}")
print("menor discordancia unilateral significativa: b=6,c=0 p=", stats.mcnemar_exact(6, 0), " b=5,c=0 p=", stats.mcnemar_exact(5, 0), " b=8,c=1 p=", round(stats.mcnemar_exact(8,1),4), " b=10,c=2 p=", round(stats.mcnemar_exact(10,2),4))
print("limites superiores 95% (Clopper-Pearson) com 0 eventos:")
for n in (36, 40, 72, 76, 148, 168):
    print("  0/%d -> %.4f" % (n, stats.clopper_pearson(0, n)[1]))
print("1/72:", [round(x,4) for x in stats.clopper_pearson(1, 72)], " 3/168:", [round(x,4) for x in stats.clopper_pearson(3, 168)])
print("acuracia 148/148:", [round(x,4) for x in stats.clopper_pearson(148, 148)], " 145/148:", [round(x,4) for x in stats.clopper_pearson(145,148)])
print("amostra v1+v2 B 159/160:", [round(x,4) for x in stats.clopper_pearson(159, 160)], " invalid 1/160:", [round(x,4) for x in stats.clopper_pearson(1,160)])
print("sem raciocinio 16/11 63/80:", [round(x,4) for x in stats.clopper_pearson(63, 80)], " 17/40:", [round(x,4) for x in stats.clopper_pearson(17, 40)])
print("B 2048: invalid 13/80:", [round(x,4) for x in stats.clopper_pearson(13, 80)])
print("10 vs 0 em 120 casos poder p1=.05:", round(stats.mcnemar_power(120, 0.05, 0.0), 3))
