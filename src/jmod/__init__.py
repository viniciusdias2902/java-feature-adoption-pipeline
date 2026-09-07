"""Longitudinal study of Java baseline upgrades and language feature adoption."""

#: Versão da pipeline, gravada em toda linha derivada. É o que permite reprocessar a
#: população aos poucos sem perder a validade do que já existe: uma feature cuja detecção
#: mudou só entra no dataset vinda de projetos minerados na versão que a corrigiu.
#:
#: 0.1.0 — primeira execução populacional
#: 0.2.0 — D-016: `constructor_body` na classificação de posição do switch. Invalida
#:         `switch_expression` (e só ele) nos projetos minerados em 0.1.0.
#: 0.3.0 — D-022: entra `unnamed_pattern` (JEP 456, estável no Java 22). Recurso novo,
#:         não invalida nenhum dos oito anteriores.
PIPELINE_VERSION = "0.3.0"

#: Por feature, a versão a partir da qual a detecção é considerada correta. Projetos
#: minerados abaixo disso têm a feature omitida do dataset, com a perda reportada — em vez
#: de entrarem com dado sabidamente errado.
#: `unnamed_pattern` está implementado e minerado, mas retido fora do dataset até que
#: sua cobertura fique completa: dos 159 projetos que podem ser elegíveis, 145 foram
#: reprocessados, e os 14 que faltam são os maiores (mediana de LOC 8,1x maior). Reportá-lo
#: junto dos demais convidaria a uma comparação entre amostras não equivalentes. Baixar o
#: piso para 0.3.0 devolve o recurso ao dataset (D-023).
MIN_VERSION_BY_FEATURE = {"switch_expression": "0.2.0", "unnamed_pattern": "0.4.0"}
