"""
Fixture `[SIM-HARNESS]` do SR-1 (SIM-1.5F): gerador DETERMINISTICO de uma populacao tecnica que reproduz os PICOS do
`nh-small` sem usar a populacao empresarial. Le um unico arquivo de perfil de carga (so inteiros, fixado por hash);
nunca le os diretorios de cenarios nem a definicao da empresa do NH. O utilitario de autoria `extract_profile` e o unico
que toca a agenda publica do NH (so para contar) e NAO pode ser importado pelo gerador.
"""
