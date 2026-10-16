"""
Plano de controle do run do `nh-small` (SIM-1.5E): identidade (RIM / `run_id`), registro e ledger com hash
encadeado, ciclo de vida sem retrocesso, interlock de autorizacao do D0, guarda de disco, registros diarios,
verificadores de autoridade (sem Oracle), pacote de evidencia selavel e o laco de 180 dias que REUSA o
`RunOrchestrator`/`build_hooks` ja provados (floor D14 e restart D90: nenhuma segunda implementacao).

Nada aqui importa `sim.oracle` nem `sim.generator` (o Oracle nunca chega ao runner por este pacote): a avaliacao
semantica e o Evaluator offline. Politica de recuperacao do 1o run: R0 (sem resume).
"""
