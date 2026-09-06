"""Longitudinal study of Java baseline upgrades and language feature adoption."""

#: Versão da pipeline, gravada em toda linha derivada. É o que permite reprocessar a
#: população aos poucos sem perder a validade do que já existe: uma feature cuja detecção
#: mudou só entra no dataset vinda de projetos minerados na versão que a corrigiu.
#:
#: 0.1.0 — primeira execução populacional
#: 0.2.0 — D-016: `constructor_body` na classificação de posição do switch. Invalida
#:         `switch_expression` (e só ele) nos projetos minerados em 0.1.0.
PIPELINE_VERSION = "0.2.0"

#: Por feature, a versão a partir da qual a detecção é considerada correta. Projetos
#: minerados abaixo disso têm a feature omitida do dataset, com a perda reportada — em vez
#: de entrarem com dado sabidamente errado.
MIN_VERSION_BY_FEATURE = {"switch_expression": "0.2.0"}
